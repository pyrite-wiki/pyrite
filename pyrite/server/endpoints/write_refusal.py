"""REST adapters for the KBService write pipeline's refusals (#378).

The service decides every create/update refusal; these only translate: a
refusal into an ``HTTPException`` with its own ``error_code``, and the raw
request body into the ADR-0034 check the pydantic models cannot see.
"""

from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request
from pydantic import BaseModel

from ...exceptions import ValidationError
from ...services.body_bounds import MARKER_KEYS, ensure_not_truncated


def refusal_http(exc: ValidationError) -> HTTPException:
    """Map a write-pipeline refusal to REST, keeping its own code.

    The service decides; this only picks the status. `ENTRY_EXISTS` is a
    conflict (409); every other refusal is a bad request (400). Never
    retryable: the same request fails the same way.

    ``exc.error_code`` is a class attribute on every ``PyriteError`` since
    ADR-0037 theme 2 -- the base ``ValidationError``'s is
    ``VALIDATION_FAILED`` (conductor decision, fix round 1: the write
    pipeline's long-documented spelling, which REST, MCP and the CLI all
    already agreed on before this theme). ``exc.error_code`` is unconditional
    on every ``PyriteError`` subclass now, so there is no fallback case left
    to reach here: this function's own type hint (``exc: ValidationError``)
    guarantees a class code is always present. This is also the code
    `#378`'s write-pipeline parity test (`test_write_surface_parity.py`)
    checks agrees with MCP's `kb_create`, `kb_bulk_create`,
    `POST /api/entries/import` and every CLI write surface -- one pipeline,
    one code, on every surface including this one.
    """
    code = exc.error_code
    detail: dict = {"code": code, "message": str(exc), "retryable": False}
    suggestion = getattr(exc, "suggestion", None)
    if suggestion:
        detail["hint"] = suggestion
    declared = getattr(exc, "declared_types", None)
    if declared is not None:
        detail["declared_types"] = declared
        detail.setdefault(
            "hint",
            f"Use an entry_type in [{', '.join(declared)}], or send allow_undeclared: true.",
        )
    # A file its declared type cannot load is not a bad request: the same
    # 422 as the central handler gives it (ADR-0042 8a).
    status = {"ENTRY_EXISTS": 409, "ENTRY_UNREADABLE": 422}.get(code, 400)
    return HTTPException(status_code=status, detail=detail)


async def refuses_truncated_body(request: Request) -> None:
    """ADR-0034 rule 2 on the REST JSON write endpoints' *raw* body.

    A FastAPI dependency, because the pydantic request models drop every key
    they do not declare -- a `body_truncated` nested under any other key would
    never reach the service, while the body it marks would. This reads the raw
    JSON and applies the service's own check (``ensure_not_truncated``) at any
    depth, before the handler runs.

    A request whose payload is not JSON (a multipart upload, an empty body) is
    left alone: there is nothing to inspect, and `/entries/import` hands each
    parsed record to the pipeline, which checks it.

    **The JSON test mirrors FastAPI's own**, deliberately. FastAPI parses a
    body whenever the media type's maintype is `application` and its subtype
    is `json` or ends `+json`, lower-cased by `email.message` first
    (`fastapi/routing.py::get_request_handler`). A narrower test here does not
    make the guard conservative -- it makes it *bypassable*, because the
    handler still runs and still writes.
    """
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if not (media_type == "application/json" or media_type.endswith("+json")):
        return
    try:
        payload = await request.json()
    except ValueError:
        # Not JSON (JSONDecodeError, UnicodeDecodeError): nothing to inspect,
        # and FastAPI's own parse refuses it. Only a parse error is let
        # through: a read that failed -- the body limit stopping it
        # (server/body_limit.py), a client that went away -- propagates, so
        # the handler never runs on a body this guard did not see.
        return

    # PATCH writes one named field, so it is a body write only when that field
    # IS the body -- and then `value`, not a `body` key, holds the body. The
    # method decides, never the payload: a stray `field` key on a POST or PUT
    # must not skip the check.
    if request.method == "PATCH" and isinstance(payload, dict):
        if payload.get("field") != "body":
            return
        payload = {**payload, "body": payload.get("value")}

    try:
        ensure_not_truncated(payload)
    except ValidationError as e:
        raise refusal_http(e) from e


def refuses_undeclared_keys(
    model: type[BaseModel], verb: str
) -> Callable[[Request], Awaitable[None]]:
    """Refuse unknown entry-write keys before validating declared values.

    ADR-0034 markers are accepted transport on all three verbs. After the
    truncation dependency has checked them, remove them from the parsed JSON
    before the model's extra-forbid guard; they are never entry content.
    """
    allowed = set(model.model_fields) | set(MARKER_KEYS)

    async def check(request: Request) -> None:
        media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if not (media_type == "application/json" or media_type.endswith("+json")):
            return
        try:
            payload = await request.json()
        except ValueError:
            return  # FastAPI owns malformed JSON; other read failures propagate.
        if not isinstance(payload, dict):
            return
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise refusal_http(
                ValidationError(
                    f"Unknown field(s) on a {verb}: {', '.join(unknown)}. "
                    f"A {verb} accepts: {', '.join(sorted(allowed))}."
                )
            )
        # FastAPI validates this same parsed object after solving dependencies.
        for key in MARKER_KEYS:
            payload.pop(key, None)

    return check
