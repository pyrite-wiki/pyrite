"""REST's adapter to the access policy (ADR-0037 §2, #383).

The rule lives in `pyrite/services/access_policy.py`. This module holds only
the FastAPI side of asking it:

- `get_principal`: what `api.verify_api_key` recorded on the request, as a
  `Principal`. The one place REST turns request state into a caller.
- `authorize(action, resource)`: the one dependency a route declares to ask
  the policy. Theme 3a decides reads:

  - ``authorize(Action.KB_READ, KB)`` -- every KB the request names (path,
    query, JSON body; `api._resolve_kb_names`) must be readable, and one that
    is not answers 404 ``KB_NOT_FOUND``, byte-identical to a KB that does not
    exist. The dependency returns the caller's `ReadScope`, so a route that
    also spans KBs when none is named takes it as a parameter.
  - ``authorize(Action.KB_READ, AnyKB)`` -- the `ReadScope` alone, for a
    route that spans KBs and names none it must check.

  Writes (`requires_kb_tier`, theme 3b) and instance/user routes
  (`requires_tier`, theme 3c) still use their `api.py` dependencies; asking
  `authorize` for one of those fails when the route is declared.

`tests/test_every_entry_point_passes_the_policy.py` (ADR-0037 §5) fails for
any REST operation whose dependant tree lacks exactly one `authorize(...)`,
unless it is public or on that test's shrinking not-yet-migrated list.
"""

from __future__ import annotations

import functools

from fastapi import Depends, HTTPException
from starlette.requests import HTTPConnection, Request

from ..exceptions import KBNotFoundError
from ..services.access_policy import (
    KB,
    AccessPolicy,
    Action,
    AnyKB,
    Principal,
    ReadScope,
    named_kb,
)


def get_principal(request: HTTPConnection) -> Principal | None:
    """The caller, as `verify_api_key` resolved it; None when it did not.

    `verify_api_key` sets exactly one of: a session user (`auth_user`), the
    anonymous visitor (`anonymous`), or neither -- an operator API key, or
    auth disabled -- with the role in `api_role`. Usable as a FastAPI
    dependency.
    """
    state = request.state
    role = getattr(state, "api_role", None)
    if role is None:
        return None
    auth_user = getattr(state, "auth_user", None)
    if auth_user:
        return Principal.user(auth_user["id"], role)
    if getattr(state, "anonymous", False):
        return Principal.anonymous(role)
    return Principal.from_api_key(role)


def not_authenticated() -> HTTPException:
    """REST's answer when no principal was recorded -- the same 401 the
    router floor (`verify_api_key`) gives."""
    return HTTPException(status_code=401, detail="Invalid or missing API key")


def read_scope(request: Request, policy: AccessPolicy) -> ReadScope:
    """The caller's `ReadScope`, from the policy.

    No principal is refused (401), never read as "not scoped": a route
    mounted without `verify_api_key` must not become a route anyone can
    read everything through. Asked once per request: FastAPI runs one
    `authorize(...)` callable once, however many times a route declares it.
    """
    principal = get_principal(request)
    if principal is None:
        raise not_authenticated()
    return policy.read_scope(principal)


def blank_path_kb(request: Request) -> str | None:
    """A KB path parameter that is blank, or None.

    A blank query or body value names no KB (`access_policy.named_kb`); a
    blank *path segment* does not get that reading. The path is the route's
    identity, so `/api/kbs/%20%20/permissions` names a KB called "  ", which
    is not one: refused as `KB_NOT_FOUND`, never served as "no KB named".
    """
    from .api import KB_PARAM_NAMES, _admin_kb_path_name

    for param in KB_PARAM_NAMES:
        value = request.path_params.get(param)
        if isinstance(value, str) and named_kb(value) is None:
            return value
    value = _admin_kb_path_name(request)
    if isinstance(value, str) and named_kb(value) is None:
        return value
    return None


def _blank_path_refusal(name: str) -> KBNotFoundError:
    from ..services.kb_resolver import HOW_TO_LIST_KBS

    return KBNotFoundError(f"KB '{name}' not found", suggestion=HOW_TO_LIST_KBS, did_you_mean=[])


def resolve_named_kb(request: Request, policy: AccessPolicy, name: object):
    """The KB a route names, through the one resolver, for a route that
    decides access inline rather than through `authorize` (#952): the
    admin-only KB routes, permissions, worktrees. Resolved for the caller's
    read scope, an unknown, unreadable or shortname KB is 404
    ``KB_NOT_FOUND`` before the handler runs; a blank or absent name names
    no KB and is None -- unless it is the path's (`blank_path_kb`). No
    principal is 401.
    """
    principal = get_principal(request)
    if principal is None:
        raise not_authenticated()
    blank = blank_path_kb(request)
    if blank is not None:
        raise _blank_path_refusal(blank)
    return policy.resolve_kb_for(principal, name)


# What this theme decides. Everything else is refused at declaration.
_DECIDED = {(Action.KB_READ, KB), (Action.KB_READ, AnyKB)}


@functools.cache
def authorize(action: Action, resource: type):
    """The dependency a route declares to pass the policy (ADR-0037 §2).

    Cached: the same ``(action, resource)`` is the same callable, so a route
    that declares it on its router and takes its value as a parameter runs
    it once per request, and the §5 guard counts one declaration.
    """
    if (action, resource) not in _DECIDED:
        raise NotImplementedError(
            f"authorize({action!s}, {getattr(resource, '__name__', resource)}) is not "
            "decided by REST's policy adapter yet (ADR-0037 themes 3b, 3c)"
        )
    # Lazy: `api` imports this module for `get_principal`.
    from ..services.kb_service import KBService
    from .api import (
        _resolve_kb_names,
        _UnparseableBodyError,
        get_access_policy,
        get_kb_service,
    )

    if resource is AnyKB:

        async def authorize_kb_read_any(
            request: Request, policy: AccessPolicy = Depends(get_access_policy)
        ) -> ReadScope:
            return read_scope(request, policy)

        return authorize_kb_read_any

    async def authorize_kb_read_named(
        request: Request,
        policy: AccessPolicy = Depends(get_access_policy),
        svc: KBService = Depends(get_kb_service),
    ) -> ReadScope:
        if get_principal(request) is None:
            raise not_authenticated()
        blank = blank_path_kb(request)
        if blank is not None:
            raise _blank_path_refusal(blank)
        try:
            names = await _resolve_kb_names(request)
        except _UnparseableBodyError:
            # Fail closed: an unreadable body names an unknown set of KBs,
            # and "names none" is what lets a request through.
            raise HTTPException(
                status_code=400,
                detail={"code": "INVALID_BODY", "message": "Request body could not be parsed"},
            ) from None
        scope = read_scope(request, policy)
        readable = scope.as_set()
        for name in names:
            # The one resolver (#952): an unknown name, a KB outside the
            # scope and a shortname are one KBNotFoundError, which the
            # central handler answers as 404 KB_NOT_FOUND with its
            # suggestion. Every caller, the unscoped one included.
            svc.resolve_kb(name, readable_kbs=readable)
        return scope

    return authorize_kb_read_named
