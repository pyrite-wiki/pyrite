"""The single REST exception handler (ADR-0037 theme 2, §3).

Every ``PyriteError`` an endpoint does not catch itself is converted here to
a clean HTTP status and one wire shape:

    {"detail": {"code": "...", "message": "...", "retryable": bool, "hint"?: "..."}}

That is the same shape ``HTTPException(detail={...})`` sites already answer
(``write_refusal.refusal_http`` is the model this handler now matches, not
duplicates) -- so a caller reads ``resp.json()["detail"]["code"]`` no matter
which REST code path produced the response. Endpoint ``HTTPException`` sites
themselves are not converted in this theme (ADR-0037 migration theme 2); this
module only unifies the *central* handler that used to answer a third, flat
shape (``{"code", "message"}``, no ``detail`` wrapper, no ``retryable``).

The code comes from the raised exception's class (``exc.error_code``,
ADR-0037 §3: "codes live on exception classes") -- this module's table maps
that code string to an HTTP status, replacing the old table keyed by
exception *type*. A status is still one per code, decided here, not on the
exception class: the same domain condition can map to different statuses on
different transports (REST's status codes have no MCP or CLI equivalent).
A code the table does not list (a subclass that narrows its own error_code
without a row being added here) falls back to its nearest listed *base*
class's status via ``isinstance`` -- ``_BASE_CLASS_FALLBACK`` -- rather than
a blind 500; the table is the fast, common-case path, and the fallback is
the safety net for the class this table hasn't caught up with yet.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..exceptions import (
    ConfigError,
    EntryNotFoundError,
    KBNotFoundError,
    KBProtectedError,
    KBReadOnlyError,
    PluginError,
    PyriteError,
    StorageError,
    ValidationError,
)

logger = logging.getLogger(__name__)


# code -> HTTP status. Any code not listed here (a class that inherits
# PyriteError.error_code == "INTERNAL_ERROR" without narrowing it, or a new
# class whose code nobody added below yet) maps to 500 INTERNAL_ERROR --
# fails safe, the same way an unrecognised exception type used to.
_STATUS_BY_CODE: dict[str, int] = {
    "ENTRY_NOT_FOUND": 404,
    "KB_NOT_FOUND": 404,
    "KB_READ_ONLY": 403,
    "KB_PROTECTED": 403,
    "INVALID_FRONTMATTER": 422,
    "QUERY_TOO_LONG": 422,
    "QUERY_SYNTAX": 400,
    # The one route that raises LastAdminError (PUT /auth/users/{id}/role)
    # catches it itself and re-raises as HTTPException, so this row is never
    # reached from there -- kept for any future caller that lets it propagate.
    "LAST_ADMIN": 409,
    # The base ValidationError's code (conductor decision, fix round 1: the
    # write pipeline's documented VALIDATION_FAILED wins over the central
    # handler's old, less-visited VALIDATION_ERROR spelling).
    "VALIDATION_FAILED": 422,
    # The old table matched every ValidationError subclass on isinstance, so
    # a subclass with its own error_code (#378's family) still got 422 from
    # the ValidationError row. Listed explicitly for clarity and speed (the
    # common case never falls through to _BASE_CLASS_FALLBACK below), but a
    # code missing from this dict is no longer a silent 500: see
    # _status_for's isinstance fallback for the base classes below.
    "UNDECLARED_TYPE": 422,
    "ENTRY_EXISTS": 422,
    "SCHEMA_VIOLATION": 422,
    "INVALID_REF": 422,
    "CONFIG_SAVE_REFUSED": 409,
    "CONFIG_CONFLICT": 409,
    # A default_role change on a config.yaml KB: config.yaml is the source of
    # truth there, so the change is refused rather than reported as done.
    "KB_DEFINED_IN_CONFIG": 409,
    # KBAlreadyExistsError's own code (#506) -- listed explicitly so its
    # status doesn't depend on _BASE_CLASS_FALLBACK's ConfigError row (which
    # happens to answer the same 409 today, but coincidentally: nothing pins
    # CONFLICT itself to a status without this row).
    "CONFLICT": 409,
    "PLUGIN_ERROR": 502,
    "STORAGE_ERROR": 500,
    # 500, not 409: this row governs the anonymous, always-public GET routes
    # that build a BrandingService as a side effect of serving content
    # (/config/branding, /sitemap.xml, /robots.txt) -- a broken branding.yaml
    # there is a server misconfiguration, not something wrong with the
    # caller's request. POST /api/site/render catches BrandingInvalidError
    # itself before this handler ever sees it and answers 409 (#408); this
    # row is unreached from that path.
    "BRANDING_INVALID": 500,
    # ClipperBlockedHostError had no row in the old isinstance table either
    # (it is a direct PyriteError, not a ValidationError), so it already fell
    # through to 500 INTERNAL_ERROR -- this keeps that status, now under its
    # own code instead of the generic one.
    "CLIPPER_BLOCKED_HOST": 500,
    "INTERNAL_ERROR": 500,
    # AccessDenied family (ADR-0037 §3). Not raised by any surface yet
    # (theme 1/3b's job) -- listed so the mapping is ready when they are.
    "UNAUTHENTICATED": 401,
    "FORBIDDEN": 403,
}


#: A code this dict does not know falls back to its exception's *base*
#: class's status, taking the first match in this tuple's order (not the
#: MRO) rather than a blind 500; the eight bases are siblings today, so the
#: order only matters for a future class inheriting from two of them.
#: Guards against exactly the bug this list fixed once already: a
#: ValidationError/ConfigError/StorageError subclass that narrows its own
#: error_code (#378's family, and any future one) but whose code nobody
#: added to _STATUS_BY_CODE above -- a silent 500 for a request the caller
#: got right, instead of the 4xx (or 502) its base answers for the same
#: condition. Item 2 (conductor cold read of 5d65caa7) widened this from
#: three bases to eight.
_BASE_CLASS_FALLBACK: tuple[tuple[type[PyriteError], int], ...] = (
    (ValidationError, 422),
    (ConfigError, 409),
    (StorageError, 500),
    (PluginError, 502),
    (EntryNotFoundError, 404),
    (KBNotFoundError, 404),
    (KBReadOnlyError, 403),
    (KBProtectedError, 403),
)


def _status_for(exc: PyriteError) -> int:
    code = exc.error_code
    if code in _STATUS_BY_CODE:
        return _STATUS_BY_CODE[code]
    for base, status in _BASE_CLASS_FALLBACK:
        if isinstance(exc, base):
            return status
    return 500


def _retryable(exc: PyriteError) -> bool:
    """Whether the same call could succeed if made again unchanged.

    Only ``StorageBusyError`` says yes (a locked/busy database can succeed
    once the lock is released, #431); every other domain refusal is
    deterministic. Reads the instance attribute set by ``StorageError`` and
    its subclasses, defaulting to ``False`` for any class that has none.
    """
    return bool(getattr(exc, "retryable", False))


def error_response(exc: PyriteError) -> tuple[int, dict]:
    """The real (status, ``{"detail": {...}}``) pair this module answers for
    ``exc`` -- the exact classification and body-shaping logic the registered
    handler uses, factored out so a caller (the characterization harness,
    ``tests/characterization/error_bodies.py``) can read the real mapping
    directly instead of keeping a parallel copy of it in sync by hand.
    """
    code = exc.error_code
    status_code = _status_for(exc)
    if status_code >= 500:
        # The exception itself, not True: outside an except frame,
        # sys.exc_info() is empty and exc_info=True logs no traceback (#431).
        logger.error("Unhandled %s: %s", type(exc).__name__, exc, exc_info=exc)
    message = str(exc)
    public_message = exc.public_message
    if public_message is not None:
        # str(exc) may name a real filesystem path or other operator detail
        # unsafe to return over HTTP (#377's ConfigSaveRefusedError pattern,
        # extended to any PyriteError subclass that sets a public_message --
        # e.g. BrandingInvalidError, #445's cold read). A 5xx already logged
        # the detail above (with exc_info); logging it again here would
        # double it on every request -- the #445 delta cold read's second
        # finding. Only a sub-500 refusal needs this line, since nothing else
        # logs it.
        if status_code < 500:
            logger.warning("%s", exc)
        message = public_message
    detail: dict = {"code": code, "message": message, "retryable": _retryable(exc)}
    suggestion = getattr(exc, "suggestion", None)
    if suggestion:
        detail["hint"] = suggestion
    return status_code, {"detail": detail}


def register_pyrite_exception_handler(app: FastAPI) -> None:
    """Register the central handler mapping the PyriteError hierarchy to HTTP.

    Any PyriteError an endpoint does not catch itself is converted to a proper
    status code and the canonical ``{"detail": {...}}`` JSON body, instead of
    leaking a raw 500 with a Python traceback. The message shown is
    ``public_message`` when the class sets one (safe by construction -- see
    ``pyrite.exceptions``), else ``str(exc)`` (domain messages are written to
    be safe to show). No traceback or internals are exposed. 5xx cases are
    logged with a traceback server-side for debugging.
    """

    def _handler(request: Request, exc: PyriteError) -> JSONResponse:
        status_code, content = error_response(exc)
        return JSONResponse(status_code=status_code, content=content)

    app.add_exception_handler(PyriteError, _handler)
