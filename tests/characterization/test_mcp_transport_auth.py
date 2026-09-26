"""The MCP transport's OWN authentication path is exercised, not bypassed
(#498, a follow-up from the final cold read of #476/ADR-0037 theme 0).

`test_mcp_matrix.py` calls `world.mcp_server._dispatch_tool` directly with
`readable_kbs`/`writable_kbs` computed by `world.py` (a snapshot, or
`world.resolve_readable_writable`'s fresh call to `api.kbs_for_user_at_tier`)
-- it never goes through `pyrite/server/mcp_routes.py`'s own
`_resolve_bearer_auth` / `_authenticate`, the connection-level code that
turns a Bearer token, an `X-API-Key` header or a session cookie into a tier
and a scope for a *real* MCP connection (`/mcp/sse`, `/mcp/messages/`).
Forcing `readable_kbs = writable_kbs = None` there -- every bearer session
reading and writing every KB -- passed all 42 harness tests before this file
existed (#498's own reproduction). This module is the missing golden: one
case per principal drives `_resolve_bearer_auth` (and, once, the async
`_authenticate` wrapper that runs it off the event loop) with that
principal's real credential -- the header or cookie `world.py` already built
for it -- and pins the resolved tier, `readable_kbs` and `writable_kbs`.

**Why `_resolve_bearer_auth` directly, not a live SSE session.** `/mcp/sse`
negotiates a long-lived SSE stream (`sse_transport.connect_sse`) that never
returns a plain JSON response to assert against; the auth decision itself
happens once, before that negotiation, in `_resolve_bearer_auth`. Calling it
with a real `starlette.requests.Request` built from the same
headers/cookies a real HTTP request would carry (`_transport_request`
below) drives the exact code path a connection resolves through -- the
function's own docstring is the contract this file pins. `/mcp/info`
(`test_info_endpoint_...` below) additionally proves the wiring from a real
`TestClient` GET all the way through `_authenticate` for the tier half of
the same decision, closing the gap #498 names for "the `/mcp` endpoint
through `TestClient`, if that's feasible" -- feasible for `/mcp/info`
specifically because, unlike `/mcp/sse`, it returns a plain JSON body after
authenticating.

Regenerate: none of this needs a golden-file regenerate step (unlike the
main matrix) -- every case asserts a literal expected value, because the
whole point is a fixed, reviewable pin per principal, not a large surface
where a table pays for itself.
"""

from __future__ import annotations

import hashlib

import pytest
from starlette.requests import Request

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.services.auth_service import AuthService
from pyrite.server.mcp_routes import _authenticate, _resolve_bearer_auth
from tests.characterization.world import NO_DEFAULT_ROLE, PRIVATE, READABLE, READ_ONLY


def _transport_request(headers: dict[str, str] | None = None) -> Request:
    """A real `starlette.requests.Request` carrying exactly the headers (and,
    via a `Cookie` header, cookies) a live HTTP request to `/mcp/sse` would
    -- built from a raw ASGI scope the same way `tests/test_mcp_write_scoping.py`
    and `tests/test_request_guard.py` already do for other transport-level
    tests, not through `TestClient` (which has no hook to hand back the
    `Request` object `_resolve_bearer_auth` takes)."""
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/mcp/sse",
        "raw_path": b"/mcp/sse",
        "root_path": "",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("testclient", 123),
        "server": ("testserver", 80),
    }
    return Request(scope)


def _request_for(principal) -> Request:
    """A transport-level request carrying `principal`'s real credential --
    the same `rest_headers`/`rest_cookies` `world.py` built for it, so this
    file drives the identical credential the main REST/MCP matrix does, just
    through the transport auth path instead of a precomputed scope."""
    headers = dict(principal.rest_headers)
    if principal.rest_cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in principal.rest_cookies.items())
        headers["cookie"] = cookie_str
    return _transport_request(headers)


# Every principal `world.py` builds, and what `_resolve_bearer_auth` must
# resolve for its real credential: (tier, readable_kbs, writable_kbs), each
# `None` meaning unscoped. Pinned as a literal, not derived from
# `principal.readable_kbs`/`writable_kbs` (a *snapshot* `world.py` computed
# once at construction with `api.kbs_for_user_at_tier`, a DIFFERENT
# resolution path from the one under test here) -- so a change to either
# resolution path independently is caught, not laundered by comparing one
# path's own output to itself.
EXPECTED = {
    "read_key": ("read", None, None),
    "write_key": ("write", None, None),
    "admin_key": ("admin", None, None),
    "global_user": (
        "read",
        frozenset({READABLE, READ_ONLY, NO_DEFAULT_ROLE}),
        frozenset(),
    ),
    "local_user": ("read", frozenset({READABLE, READ_ONLY}), frozenset()),
    "granted_user": ("read", frozenset({READABLE, READ_ONLY, PRIVATE}), frozenset()),
}


@pytest.mark.parametrize("principal_name", sorted(EXPECTED))
def test_bearer_auth_resolves_the_pinned_scope(world, principal_name):
    principal = world.principals[principal_name]
    ctx = _resolve_bearer_auth(_request_for(principal), world.config, world.db)

    expected_tier, expected_readable, expected_writable = EXPECTED[principal_name]
    assert ctx["role"] == expected_tier, ctx
    assert ctx["readable_kbs"] == expected_readable, ctx
    assert ctx["writable_kbs"] == expected_writable, ctx


def test_anonymous_has_no_bearer_auth_branch(world):
    """`_resolve_bearer_auth`'s own docstring: "there is no anonymous
    branch" -- a caller with no credential at all is refused, not admitted
    at `anonymous_tier` the way REST would. A no-credential MCP connection
    401s outright."""
    with pytest.raises(Exception) as exc_info:
        _resolve_bearer_auth(_transport_request({}), world.config, world.db)
    assert getattr(exc_info.value, "status_code", None) == 401


def test_invalid_bearer_token_is_refused(world):
    request = _transport_request({"authorization": "Bearer not-a-real-token-at-all"})
    with pytest.raises(Exception) as exc_info:
        _resolve_bearer_auth(request, world.config, world.db)
    assert getattr(exc_info.value, "status_code", None) == 401


def test_invalid_session_cookie_is_refused(world):
    request = _transport_request({"cookie": "pyrite_session=not-a-real-session"})
    with pytest.raises(Exception) as exc_info:
        _resolve_bearer_auth(request, world.config, world.db)
    assert getattr(exc_info.value, "status_code", None) == 401


def test_expired_session_is_refused(world):
    """A session whose `expires_at` has already passed must be refused, the
    same as an invalid one -- not silently admitted with its stale role.

    Logs in AGAIN for a fresh, throwaway session of its own, rather than
    expiring `local_user`'s existing `principal.rest_cookies` session: that
    session is shared, session-scoped `world` state every other case in this
    module (and `test_unscoped_calls.py`, sharing the same worker under
    `-n4`) also authenticates with. `verify_session_detail` DELETES an
    expired session's row outright (`auth_service.py`, "Check expiry"), so
    there is no restoring it afterwards by re-`UPDATE`ing `expires_at` --
    the row is simply gone, and every later case sees `local_user` degrade
    to an unauthenticated caller (which, for `NO_DEFAULT_ROLE`, actually
    gains read access under the anonymous ceiling rule that a real
    `global_access=False` user does NOT get -- exactly the kind of golden
    corruption this harness exists to catch, found by running this file
    together with `test_unscoped_calls.py` at `-n4`: 1 in ~3 runs, whichever
    xdist worker drew both). A second login's session is disposable: this
    test deletes it in `finally`, every other case's session is untouched.
    """
    principal = world.principals["local_user"]
    auth_service = AuthService(world.db, world.config.settings.auth)
    _, raw_token = auth_service.login("characterization-local-user", "password123")
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    world.db.execute_write_sql(
        "UPDATE session SET expires_at = :expires_at WHERE token_hash = :token_hash",
        {"expires_at": "2000-01-01T00:00:00+00:00", "token_hash": token_hash},
    )
    try:
        request = _transport_request({"cookie": f"pyrite_session={raw_token}"})
        with pytest.raises(Exception) as exc_info:
            _resolve_bearer_auth(request, world.config, world.db)
        assert getattr(exc_info.value, "status_code", None) == 401
        # The original, still-live session (`principal.rest_cookies`) is
        # untouched and keeps resolving normally.
        live_ctx = _resolve_bearer_auth(_request_for(principal), world.config, world.db)
        assert live_ctx["role"] == "read"
    finally:
        world.db.execute_write_sql(
            "DELETE FROM session WHERE token_hash = :token_hash", {"token_hash": token_hash}
        )


def test_authenticate_wrapper_resolves_the_same_ctx_off_the_event_loop(world):
    """`_authenticate` is the async wrapper every real connection (`/mcp/sse`,
    `/mcp/info`) actually calls -- it must resolve the identical ctx
    `_resolve_bearer_auth` does, just off the event loop with a per-request
    DB handle (its own docstring). Driven with `anyio.run`, the same way
    `tests/test_mcp_write_scoping.py` drives other async server code --
    no `pytest-asyncio` plugin is installed in this repo."""
    import anyio

    principal = world.principals["admin_key"]
    ctx = anyio.run(_authenticate, _request_for(principal), world.config, world.db)
    assert ctx["role"] == "admin"
    assert ctx["readable_kbs"] is None
    assert ctx["writable_kbs"] is None


def test_info_endpoint_reports_the_resolved_tier_over_the_real_transport(world):
    """`/mcp/info` is a real FastAPI route that calls `_authenticate` itself
    -- the one MCP transport endpoint that returns plain JSON instead of
    negotiating a stream, so a `TestClient` GET drives the actual transport
    wiring end to end for the tier half of the decision."""
    principal = world.principals["write_key"]
    resp = world.client.get("/mcp/info", headers=principal.rest_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tier"] == "write"
    assert body["transport"] == "sse"


def test_info_endpoint_refuses_no_credential_and_reports_unauthenticated(world):
    # No cookies, no headers -- a bare TestClient call. `handle_info` swallows
    # the HTTPException from `_authenticate` and reports "unauthenticated"
    # rather than 401ing (its own code, read directly) -- pinned as today's
    # actual behaviour, distinct from `/mcp/sse`'s hard 401.
    resp = world.client.get("/mcp/info")
    assert resp.status_code == 200, resp.text
    assert resp.json()["tier"] == "unauthenticated"


# ---------------------------------------------------------------------------
# Auth disabled, and #331 (no keys configured, auth enabled): both need their
# OWN config, since `world`'s is auth-enabled with three keys configured --
# built directly rather than through `build_world` (no KBs need seeding; the
# whole point is the credential branch, not KB content).
# ---------------------------------------------------------------------------


def _bare_config(tmp_path, *, auth_enabled: bool) -> PyriteConfig:
    (tmp_path / "kb").mkdir()
    return PyriteConfig(
        knowledge_bases=[KBConfig(name="kb", path=tmp_path / "kb", kb_type="generic")],
        settings=Settings(
            index_path=tmp_path / "index.db",
            auth=AuthConfig(enabled=auth_enabled),
        ),
    )


def test_auth_disabled_and_no_keys_is_unscoped_admin(tmp_path):
    """The default local install: no `api_key`/`api_keys`, auth disabled --
    every bearer connection is treated as the operator, unscoped."""
    from pyrite.storage.database import PyriteDB

    config = _bare_config(tmp_path, auth_enabled=False)
    db = PyriteDB(config.settings.index_path)
    try:
        ctx = _resolve_bearer_auth(_transport_request({}), config, db)
        assert ctx["role"] == "admin"
        assert ctx["readable_kbs"] is None
        assert ctx["writable_kbs"] is None
    finally:
        db.close()


def test_auth_enabled_no_keys_configured_refuses_any_key(tmp_path):
    """#331: auth enabled, no keys configured -- no key is valid, so an
    arbitrary Bearer/X-API-Key must not be admitted as admin (the bug
    `_resolve_api_key_role`'s docstring names: "a bug in the 'no keys
    configured' branch [...] had to be fixed in both" copies)."""
    from pyrite.storage.database import PyriteDB

    config = _bare_config(tmp_path, auth_enabled=True)
    db = PyriteDB(config.settings.index_path)
    try:
        request = _transport_request({"authorization": "Bearer anything-at-all"})
        with pytest.raises(Exception) as exc_info:
            _resolve_bearer_auth(request, config, db)
        assert getattr(exc_info.value, "status_code", None) == 401
    finally:
        db.close()


def test_auth_enabled_no_keys_configured_refuses_any_api_key_header(tmp_path):
    """#331's X-API-Key twin (#504 cold read point 3): the Bearer case above
    only drives branch 1 of `_resolve_credential`; branch 2 (`X-API-Key`,
    read directly from `pyrite/server/mcp_routes.py`) is a distinct code
    path with its own call to `_resolve_api_key_role`, and characterization
    needs its own golden for it rather than trusting the Bearer case to
    stand in for both."""
    from pyrite.storage.database import PyriteDB

    config = _bare_config(tmp_path, auth_enabled=True)
    db = PyriteDB(config.settings.index_path)
    try:
        request = _transport_request({"x-api-key": "anything-at-all"})
        with pytest.raises(Exception) as exc_info:
            _resolve_bearer_auth(request, config, db)
        assert getattr(exc_info.value, "status_code", None) == 401
    finally:
        db.close()


def test_auth_disabled_but_keys_configured_refuses_no_credential(tmp_path):
    """#504 cold read point 3's third case: auth DISABLED, but a key IS
    configured, and no credential is sent at all.

    Today's actual behaviour, pinned as found by reading
    `_resolve_bearer_auth`'s branch 4 ("No auth configured — open access")
    directly: that branch only admits the caller as unscoped admin when
    NEITHER `api_key`/`api_keys` NOR `auth.enabled` is set. Here `api_key`
    is configured (even though `auth.enabled=False`), so branch 4's
    condition is false and the call falls through to the same 401 every
    other unresolved credential gets -- distinct from
    `test_auth_disabled_and_no_keys_is_unscoped_admin` above, where NEITHER
    is configured and the same no-credential request is admitted as admin.
    """
    from pyrite.storage.database import PyriteDB

    config = _bare_config(tmp_path, auth_enabled=False)
    config.settings.api_key = "configured-but-unused-by-this-caller"
    db = PyriteDB(config.settings.index_path)
    try:
        with pytest.raises(Exception) as exc_info:
            _resolve_bearer_auth(_transport_request({}), config, db)
        assert getattr(exc_info.value, "status_code", None) == 401
    finally:
        db.close()


# =============================================================================
# POST /mcp/messages/ (#504 cold read point 2; security batch 3b). Every POST
# is identified by its own credential, and `mcp_routes._session_owner` puts
# that principal in the ASGI scope's `user`, where the SDK's same-owner check
# (`SseServerTransport._session_owners`) compares it with the principal that
# opened the session. A POST from anyone else -- or with no accepted
# credential -- is answered exactly as an unknown session (404).
#
# Changed goldens (intended): before batch 3b the session id alone was the
# credential, and `TestMCPMessagesScopeFixedAtConnect` pinned a 202 for a
# POST with no credential and for one with an unresolvable credential. Both
# are now 404. The session's scope is still resolved once at connect; it
# lives no longer than that credential (`pyrite/server/mcp_sessions.py`,
# driven end to end by tests/test_mcp_sse_session.py).
# =============================================================================


def _sse_transport_of(app):
    """The live `SseServerTransport` `mount_mcp_routes` builds, which it
    exposes on `app.state` for exactly this: the lowest seam that holds a
    session's registration (`_read_stream_writers`) and its owner
    (`_session_owners`) without negotiating a real SSE handshake through
    `TestClient` (which never returns from a GET to `/mcp/sse`)."""
    return app.state.pyrite_mcp_sse_transport


def _register_fake_session(app, owner_ctx=None):
    """Register a session id in the live transport, the same dicts a real
    `/mcp/sse` connect populates, owned by `owner_ctx`'s principal (the
    identity `_session_owner` builds from a resolved ctx). The paired read
    end is never read; the buffer keeps `writer.send` from blocking."""
    import uuid

    import anyio
    from mcp.server.auth.middleware.bearer_auth import authorization_context

    from pyrite.server.mcp_routes import _session_owner

    transport = _sse_transport_of(app)
    session_id = uuid.uuid4()
    send_stream, _recv_stream = anyio.create_memory_object_stream(4)
    transport._read_stream_writers[session_id] = send_stream
    if owner_ctx is not None:
        transport._session_owners[session_id] = authorization_context(_session_owner(owner_ctx))
    return session_id


def _drop_fake_session(app, session_id) -> None:
    transport = _sse_transport_of(app)
    transport._read_stream_writers.pop(session_id, None)
    transport._session_owners.pop(session_id, None)


def _rpc_body(msg_id: int) -> str:
    import json

    return json.dumps({"jsonrpc": "2.0", "method": "ping", "params": {}, "id": msg_id})


class TestMCPMessagesSessionCredential:
    """(2a): a missing or malformed session id is refused with 400, an
    unknown one with 404 -- against `world`'s auth-ENABLED app."""

    def test_missing_session_id_is_400(self, world):
        resp = world.client.post("/mcp/messages/")
        assert resp.status_code == 400, resp.text

    def test_malformed_session_id_is_400(self, world):
        resp = world.client.post("/mcp/messages/?session_id=not-a-uuid")
        assert resp.status_code == 400, resp.text

    def test_unknown_session_id_is_404(self, world):
        resp = world.client.post(
            "/mcp/messages/?session_id=00000000000000000000000000000000",
            content=_rpc_body(1),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 404, resp.text


def _owner_ctx(world, principal_name):
    return _resolve_bearer_auth(
        _request_for(world.principals[principal_name]), world.config, world.db
    )


def _post(world, session_id, msg_id, headers=None, cookies=None):
    if cookies:
        cookie = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers = {**(headers or {}), "cookie": cookie}
    return world.client.post(
        f"/mcp/messages/?session_id={session_id.hex}",
        content=_rpc_body(msg_id),
        headers={"Content-Type": "application/json", **(headers or {})},
    )


class TestMCPMessagesScopeFixedAtConnect:
    """(2b): a message acts only for the principal that opened the session.
    Golden changed in batch 3b: the two "accepts" cases were 202."""

    def test_known_session_refuses_a_message_with_no_credential_at_all(self, world):
        session_id = _register_fake_session(world.app, _owner_ctx(world, "local_user"))
        try:
            resp = _post(world, session_id, 2)
            assert resp.status_code == 404, resp.text
        finally:
            _drop_fake_session(world.app, session_id)

    def test_known_session_refuses_a_message_with_an_unresolvable_credential(self, world):
        session_id = _register_fake_session(world.app, _owner_ctx(world, "local_user"))
        try:
            resp = _post(
                world,
                session_id,
                3,
                headers={"Authorization": "Bearer this-key-does-not-exist-anywhere"},
            )
            assert resp.status_code == 404, resp.text
        finally:
            _drop_fake_session(world.app, session_id)

    def test_known_session_refuses_another_principals_credential(self, world):
        session_id = _register_fake_session(world.app, _owner_ctx(world, "local_user"))
        try:
            other = world.principals["granted_user"]
            resp = _post(world, session_id, 4, other.rest_headers, other.rest_cookies)
            assert resp.status_code == 404, resp.text
        finally:
            _drop_fake_session(world.app, session_id)

    def test_known_session_accepts_its_owners_credential(self, world):
        session_id = _register_fake_session(world.app, _owner_ctx(world, "local_user"))
        try:
            owner = world.principals["local_user"]
            resp = _post(world, session_id, 5, owner.rest_headers, owner.rest_cookies)
            assert resp.status_code == 202, resp.text
        finally:
            _drop_fake_session(world.app, session_id)
