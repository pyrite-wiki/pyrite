"""REST adapters for the KBService write pipeline's refusals (#378).

The service decides every create/update refusal; these only translate: a
refusal into an ``HTTPException`` with its own ``error_code``, and the raw
request body into the ADR-0034 check the pydantic models cannot see.
"""

from fastapi import HTTPException, Request

from ...exceptions import ValidationError
from ...services.body_bounds import ensure_not_truncated
from ..schemas import CreateEntryRequest


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
    return HTTPException(status_code=409 if code == "ENTRY_EXISTS" else 400, detail=detail)


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
    except Exception:
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


async def refuses_unknown_create_keys(request: Request) -> None:
    """#1090: refuse a top-level key the create body model does not declare.

    The model drops every key it does not declare, so a managed field sent at
    the top level (``evidence``, ``status_change_log``, ``provenance``) was
    answered 200 ``created: true``, never written and never named -- the
    caller was not told (ADR-0042 decision 3). Read the raw JSON, as
    :func:`refuses_truncated_body` does, and answer the write pipeline's own
    refusal shape: 400 ``VALIDATION_FAILED`` naming the keys. The ``metadata``
    envelope spelling reaches the service and is refused there (#1010); the
    model's ``extra="forbid"`` is the belt to this dependency's braces.
    """
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if not (media_type == "application/json" or media_type.endswith("+json")):
        return
    try:
        payload = await request.json()
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    unknown = sorted(set(payload) - set(CreateEntryRequest.model_fields))
    if not unknown:
        return
    accepted = ", ".join(sorted(CreateEntryRequest.model_fields))
    raise refusal_http(
        ValidationError(
            f"Unknown field(s) on a create: {', '.join(unknown)}. A create accepts: {accepted}."
        )
    )
