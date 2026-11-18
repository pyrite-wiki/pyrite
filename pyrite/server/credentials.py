"""The one credential resolver: verified, none, or presented-but-failed (#1754).

Every surface that admits callers (REST's ``verify_api_key``, ``/mcp``'s
``_resolve_credential``, the live-update socket's ``resolve_socket_scope``)
asks this module what the request presented and whether it verified. It
answers one of three things:

- ``VERIFIED``: a principal, as a ctx dict (an operator key, a session user,
  or the open-access admin of a server with auth off and no keys);
- ``NONE``: the request presented no credential. Only this caller may become
  the anonymous visitor, and only where ``anonymous_tier`` allows it;
- ``FAILED``: the request presented a credential that did not verify. It is
  refused (401, or a refused handshake) whatever ``anonymous_tier`` says:
  ADR-0043 §2's anonymous principal is "no credential", and this is not it.

**Precedence.** The first credential *presented*, in the order

    Authorization: Bearer  >  X-API-Key  >  ?api_key  >  pyrite_session cookie

decides, and nothing weaker is consulted: a failing key next to a valid
cookie is ``FAILED``, never the cookie's user. A presented credential is one
whose location is there at all, empty values included (``X-API-Key:`` with
nothing, ``Bearer`` alone, ``pyrite_session=``). Only the ``Bearer`` scheme of
``Authorization`` is ours: ``Basic`` and the rest belong to whatever proxy
sits in front, and are not a credential here. The ``api_key`` query parameter
is read only where the surface accepts it (``query_key=True``: REST, and the
socket, since a browser cannot set headers on a WebSocket); ``/mcp`` never
reads it. The cookie is a credential only while auth is enabled.

What a key is worth stays the access policy's rule
(``access_policy.resolve_api_key_role``); sessions are verified through the
policy's identity store. Synchronous, and a session lookup touches the DB:
callers on the event loop run it in a threadpool (``needs_threadpool``).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any

from starlette.requests import HTTPConnection

from ..config import PyriteConfig
from ..services.access_policy import AccessPolicy, resolve_api_key_role

SESSION_COOKIE = "pyrite_session"

# Where a credential was presented: the `location` of a `Presented`.
BEARER = "bearer"
API_KEY_HEADER = "x-api-key"
API_KEY_QUERY = "api_key"
COOKIE = "cookie"


class Outcome(Enum):
    VERIFIED = "verified"
    NONE = "none"
    FAILED = "failed"


@dataclass(frozen=True)
class Presented:
    """The credential a request presents first, and where."""

    location: str
    value: str


@dataclass(frozen=True)
class Resolution:
    """What the resolver decided. ``ctx`` is set only when ``VERIFIED``;
    ``presented`` is the credential that decided (None for ``NONE`` and for
    open access without one)."""

    outcome: Outcome
    ctx: dict[str, Any] | None = None
    presented: Presented | None = None

    @property
    def failed_cookie(self) -> bool:
        """The session cookie was presented and did not verify: the caller
        should clear it, since the web client cannot (it is ``httponly``)."""
        return (
            self.outcome is Outcome.FAILED
            and self.presented is not None
            and self.presented.location == COOKIE
        )


def presented_credential(
    conn: HTTPConnection, config: PyriteConfig, *, query_key: bool
) -> Presented | None:
    """The first credential ``conn`` presents, in precedence order; None
    when it presents none. Reads only headers, query and cookies."""
    auth_header = conn.headers.get("authorization")
    if auth_header is not None:
        scheme, _, token = auth_header.strip().partition(" ")
        if scheme.lower() == "bearer":
            return Presented(BEARER, token.strip())
    api_key = conn.headers.get("x-api-key")
    if api_key is not None:
        return Presented(API_KEY_HEADER, api_key)
    if query_key and API_KEY_QUERY in conn.query_params:
        return Presented(API_KEY_QUERY, conn.query_params.get(API_KEY_QUERY, ""))
    if config.settings.auth.enabled and SESSION_COOKIE in conn.cookies:
        return Presented(COOKIE, conn.cookies.get(SESSION_COOKIE, ""))
    return None


def open_access(config: PyriteConfig) -> bool:
    """Auth disabled and no keys configured: the default local install,
    where every caller is admin and nothing is verified."""
    s = config.settings
    return not s.api_key and not s.api_keys and not s.auth.enabled


def needs_threadpool(presented: Presented | None, config: PyriteConfig) -> bool:
    """Whether resolving ``presented`` may query the DB (a session lookup).
    Keys and the absence of a credential resolve without it."""
    if presented is None or not config.settings.auth.enabled:
        return False
    if presented.location == COOKIE:
        return True
    return presented.location == BEARER and resolve_api_key_role(presented.value, config) is None


def resolve_credential(
    conn: HTTPConnection,
    config: PyriteConfig,
    policy: AccessPolicy,
    *,
    query_key: bool = False,
    presented: Presented | None = None,
) -> Resolution:
    """Decide what ``conn`` presented. See the module docstring for the rule.

    ``presented`` may be passed when the caller has already read it (to
    decide whether to run this off the event loop); otherwise it is read.
    """
    if presented is None:
        presented = presented_credential(conn, config, query_key=query_key)
    if presented is None:
        if open_access(config):
            return Resolution(Outcome.VERIFIED, _open_ctx())
        return Resolution(Outcome.NONE)

    if presented.location != COOKIE:
        # Every non-cookie location may hold an operator key.
        role = resolve_api_key_role(presented.value, config)
        if role is not None:
            return Resolution(Outcome.VERIFIED, _key_ctx(presented.value, role), presented)

    if presented.location in (BEARER, COOKIE) and presented.value and config.settings.auth.enabled:
        found = policy.auth_service.verify_session_detail(presented.value)
        if found:
            return Resolution(Outcome.VERIFIED, _session_ctx(*found), presented)

    return Resolution(Outcome.FAILED, presented=presented)


def _session_ctx(user: dict, session: dict) -> dict[str, Any]:
    """A session credential's ctx. ``session_hash`` and ``session_expires_at``
    let a live-update socket be closed when that session ends (ADR-0036);
    ``user`` is the user dict REST records as ``auth_user``."""
    return {
        "role": user["role"],
        "username": user["username"],
        "user_id": user["id"],
        "user": user,
        "session_hash": session["token_hash"],
        "session_expires_at": session["expires_at"],
        "principal_kind": "user",
        "principal_id": str(user["id"]),
    }


def _key_ctx(key: str, role: str) -> dict[str, Any]:
    """An operator key's ctx. ``principal_id`` is the key's whole hash:
    ``username`` shows only a prefix, and is a name a user could register."""
    digest = hashlib.sha256(key.encode()).hexdigest()
    return {
        "role": role,
        "username": f"apikey-{digest[:8]}",
        "user_id": None,
        "principal_kind": "operator_key",
        "principal_id": digest,
    }


def _open_ctx() -> dict[str, Any]:
    """Auth disabled with no keys: every caller is admin."""
    return {
        "role": "admin",
        "username": "anonymous",
        "user_id": None,
        "principal_kind": "operator_key",
        "principal_id": "open-access",
    }
