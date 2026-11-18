"""A presented credential that does not verify is never anonymous (#1754).

ADR-0043 §2 defines the anonymous principal as "no credential". A request
that *presents* one (an API key in ``X-API-Key``, the ``api_key`` query
parameter or ``Authorization: Bearer``, or a ``pyrite_session`` cookie) that
does not verify is not that principal: it gets 401 on REST and on ``/mcp``,
and its live-update socket is refused at the handshake -- whatever
``anonymous_tier`` says. Until #1754, with ``anonymous_tier`` set, a mistyped,
rotated or expired key or cookie silently became the anonymous visitor on
REST and on the socket.

Also pinned here:

- REST accepts ``Authorization: Bearer <key>`` (maintainer decision, Q2 a);
  it used to ignore the header.
- Precedence: the first credential presented (Bearer, ``X-API-Key``, the
  ``api_key`` query parameter, the cookie) decides, and nothing weaker is
  consulted. A failing key next to a valid cookie is 401, not the cookie's
  user; a valid key next to a stale cookie is the key's principal.
- A 401 caused by the session cookie also clears it (the cookie is
  ``httponly``: the web client cannot), so the browser's next request is the
  anonymous visitor rather than stuck at 401.
- What does not change: no credential still gets the anonymous tier; auth
  disabled with no keys is still open; auth disabled with keys still refuses
  a wrong key.

Every request goes through ``create_app`` with no auth dependency mocked.
"""

from __future__ import annotations

import hashlib
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app
from pyrite.services.auth_service import AuthService
from pyrite.storage.database import PyriteDB
from tests.auth_seed import SESSION_COOKIE

KB_NAME = "public-kb"
ADMIN_KEY = "operator-admin-key"
WRONG = "not-a-key-this-server-knows"
TIERS = (None, "read", "write")
UNKNOWN_SESSION_ID = "0" * 32

_PASSES_ON_DEV = "already refused before #1754 (no anonymous tier, or /mcp's own resolver)"
_UNCHANGED = "behaviour #1754 must keep: true on either side of the change"


# =============================================================================
# The world
# =============================================================================


def _config(tmp: Path, *, auth: bool = True, tier: str | None = None, keys: bool = True):
    (tmp / KB_NAME).mkdir(exist_ok=True)
    api_keys = (
        [{"key_hash": hashlib.sha256(ADMIN_KEY.encode()).hexdigest(), "role": "admin"}]
        if keys
        else []
    )
    return PyriteConfig(
        knowledge_bases=[
            KBConfig(name=KB_NAME, path=tmp / KB_NAME, kb_type="generic", default_role="read")
        ],
        settings=Settings(
            index_path=tmp / "index.db",
            api_keys=api_keys,
            auth=AuthConfig(enabled=auth, allow_registration=True, anonymous_tier=tier),
        ),
    )


class World:
    """An app, its DB, and the session tokens of one user: one live, one
    expired, one logged out."""

    def __init__(self, tmp: Path, **config_kwargs) -> None:
        self.config = _config(tmp, **config_kwargs)
        self.db = PyriteDB(self.config.settings.index_path)
        auth = AuthService(self.db, AuthConfig(enabled=True))
        auth.create_user("alice", "password123", role="admin")
        _, self.live = auth.login("alice", "password123")
        _, self.expired = auth.login("alice", "password123")
        _, self.revoked = auth.login("alice", "password123")
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        self.db.execute_write_sql(
            "UPDATE session SET expires_at = :past WHERE token_hash = :h",
            {"past": past, "h": hashlib.sha256(self.expired.encode()).hexdigest()},
        )
        assert auth.logout(self.revoked)
        # The app opens its own handle on the same index file.
        self.app = create_app(config=self.config)

    def client(self) -> TestClient:
        return TestClient(self.app)

    def token(self, state: str) -> str:
        return {
            "unknown": WRONG,
            "empty": "",
            "expired": self.expired,
            "revoked": self.revoked,
        }[state]


@pytest.fixture
def make_world():
    worlds: list[World] = []
    with tempfile.TemporaryDirectory() as d:

        def _make(name: str = "w", **kwargs) -> World:
            path = Path(d) / name
            path.mkdir()
            world = World(path, **kwargs)
            worlds.append(world)
            return world

        yield _make
        for w in worlds:
            w.db.close()


# =============================================================================
# The credentials: location x state
# =============================================================================

# A key location holds no session, so its states are unknown and empty; a
# session location adds expired and revoked. Bearer carries either.
_CASES = (
    [("x-api-key", s) for s in ("unknown", "empty")]
    + [("query", s) for s in ("unknown", "empty")]
    + [("bearer", s) for s in ("unknown", "empty", "expired", "revoked")]
    + [("cookie", s) for s in ("unknown", "empty", "expired", "revoked")]
)


def _request_parts(location: str, token: str) -> tuple[dict[str, str], str]:
    """(headers, query string) presenting ``token`` at ``location``."""
    if location == "x-api-key":
        return {"X-API-Key": token}, ""
    if location == "query":
        return {}, f"api_key={token}"
    if location == "bearer":
        return {"Authorization": f"Bearer {token}"}, ""
    if location == "cookie":
        return {"Cookie": f"{SESSION_COOKIE}={token}"}, ""
    raise AssertionError(location)


def _with_query(path: str, query: str) -> str:
    if not query:
        return path
    return f"{path}{'&' if '?' in path else '?'}{query}"


def _cells(passes_on_dev):
    """Every tier x location x state, with the cells that already held on
    dev marked as controls."""
    out = []
    for tier in TIERS:
        for location, state in _CASES:
            marks = (
                [pytest.mark.control(reason=_PASSES_ON_DEV)]
                if passes_on_dev(tier, location, state)
                else []
            )
            out.append(
                pytest.param(tier, location, state, marks=marks, id=f"{tier}-{location}-{state}")
            )
    return out


# =============================================================================
# Property 1 on REST
# =============================================================================


@pytest.mark.parametrize(
    ("tier", "location", "state"), _cells(lambda tier, location, state: tier is None)
)
def test_rest_refuses_a_presented_credential_that_does_not_verify(
    make_world, tier, location, state
):
    world = make_world(tier=tier)
    headers, query = _request_parts(location, world.token(state))
    r = world.client().get(_with_query("/api/kbs", query), headers=headers)
    assert r.status_code == 401, (r.status_code, r.text[:200])
    # The existing error shape (docs/json-contracts.md): a body with `detail`.
    assert "detail" in r.json()


@pytest.mark.parametrize(
    ("tier", "location", "state"), _cells(lambda tier, location, state: tier is None)
)
def test_rest_write_with_a_bad_credential_is_401_not_a_tier_refusal(
    make_world, tier, location, state
):
    """The report's second symptom: a write with a wrong key said "requires
    'write' tier" (403), as if the caller were anonymous."""
    world = make_world(tier=tier)
    headers, query = _request_parts(location, world.token(state))
    r = world.client().post(
        _with_query("/api/entries", query),
        headers=headers,
        json={"kb": KB_NAME, "title": "x", "entry_type": "note", "body": "x"},
    )
    assert r.status_code == 401, (r.status_code, r.text[:200])


@pytest.mark.control(reason=_UNCHANGED)
@pytest.mark.parametrize("tier", ["read", "write"])
def test_rest_still_admits_no_credential_at_the_anonymous_tier(make_world, tier):
    world = make_world(tier=tier)
    r = world.client().get("/api/kbs")
    assert r.status_code == 200, r.text


@pytest.mark.control(reason=_UNCHANGED)
def test_rest_refuses_no_credential_without_an_anonymous_tier(make_world):
    world = make_world(tier=None)
    assert world.client().get("/api/kbs").status_code == 401


@pytest.mark.control(reason=_UNCHANGED)
@pytest.mark.parametrize("tier", ["read", "write"])
def test_an_authorization_header_of_another_scheme_is_not_a_credential(make_world, tier):
    """A reverse proxy's own ``Authorization: Basic ...`` is not ours to
    judge: it is ignored, as it always was, and the caller is anonymous."""
    world = make_world(tier=tier)
    r = world.client().get("/api/kbs", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert r.status_code == 200, r.text


# =============================================================================
# Property 2: REST accepts Authorization: Bearer <key>
# =============================================================================


@pytest.mark.parametrize("tier", TIERS)
def test_rest_accepts_a_bearer_api_key_with_its_role(make_world, tier):
    """An admin key sent as Bearer reaches an admin-only route: REST read it
    as no credential (401, or anonymous and 403) before #1754."""
    world = make_world(tier=tier)
    r = world.client().get("/api/admin/usage", headers={"Authorization": f"Bearer {ADMIN_KEY}"})
    assert r.status_code == 200, (r.status_code, r.text[:200])


@pytest.mark.parametrize("tier", TIERS)
def test_rest_accepts_a_bearer_session_token_as_that_user(make_world, tier):
    """Bearer carries a session token on /mcp and /ws; REST, asking the same
    resolver, now does too."""
    world = make_world(tier=tier)
    r = world.client().get("/api/admin/usage", headers={"Authorization": f"Bearer {world.live}"})
    assert r.status_code == 200, (r.status_code, r.text[:200])


# =============================================================================
# Precedence
# =============================================================================


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("location", ["x-api-key", "query", "bearer"])
def test_a_failing_key_next_to_a_valid_cookie_is_401(make_world, tier, location):
    """The key is the credential this request presents first; it does not
    verify, so the request is refused. It used to fall through to the
    cookie and be served as the cookie's user."""
    world = make_world(tier=tier)
    headers, query = _request_parts(location, WRONG)
    headers["Cookie"] = f"{SESSION_COOKIE}={world.live}"
    r = world.client().get(_with_query("/api/kbs", query), headers=headers)
    assert r.status_code == 401, (r.status_code, r.text[:200])


@pytest.mark.control(reason=_UNCHANGED)
@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("state", ["unknown", "expired", "revoked"])
def test_a_valid_key_next_to_a_stale_cookie_is_the_key(make_world, tier, state):
    """The key decides; the cookie behind it is not consulted (as before)."""
    world = make_world(tier=tier)
    r = world.client().get(
        "/api/admin/usage",
        headers={"X-API-Key": ADMIN_KEY, "Cookie": f"{SESSION_COOKIE}={world.token(state)}"},
    )
    assert r.status_code == 200, (r.status_code, r.text[:200])


# =============================================================================
# A stale cookie's 401 clears it
# =============================================================================


@pytest.mark.parametrize("state", ["unknown", "expired", "revoked"])
def test_a_stale_cookie_401_clears_the_cookie_and_the_next_request_is_anonymous(make_world, state):
    world = make_world(tier="read")
    client = world.client()
    r = client.get("/api/kbs", headers={"Cookie": f"{SESSION_COOKIE}={world.token(state)}"})
    assert r.status_code == 401, (r.status_code, r.text[:200])
    # A deletion of the cookie at the path it was set on (`/`), as
    # `/auth/logout` sends it.
    set_cookie = r.headers.get("set-cookie", "")
    assert set_cookie.startswith(f'{SESSION_COOKIE}=""'), set_cookie
    assert "Max-Age=0" in set_cookie, set_cookie
    assert "Path=/" in set_cookie, set_cookie
    # A browser drops it; its next request carries no credential.
    assert client.get("/api/kbs").status_code == 200


def test_a_failing_key_401_does_not_touch_the_cookie(make_world):
    """Only the cookie's own failure clears it: a wrong key next to a live
    cookie is refused, and the live cookie is left alone."""
    world = make_world(tier="read")
    r = world.client().get(
        "/api/kbs", headers={"X-API-Key": WRONG, "Cookie": f"{SESSION_COOKIE}={world.live}"}
    )
    assert r.status_code == 401
    assert "set-cookie" not in r.headers


# =============================================================================
# Property 1 on /mcp
# =============================================================================

_MCP_CASES = [(loc, s) for loc, s in _CASES if loc != "query"]  # /mcp never reads ?api_key


def _mcp_cells(passes_on_dev):
    out = []
    for tier in TIERS:
        for location, state in _MCP_CASES:
            marks = (
                [pytest.mark.control(reason=_PASSES_ON_DEV)]
                if passes_on_dev(tier, location, state)
                else []
            )
            out.append(
                pytest.param(tier, location, state, marks=marks, id=f"{tier}-{location}-{state}")
            )
    return out


@pytest.mark.parametrize(("tier", "location", "state"), _mcp_cells(lambda *_: True))
def test_mcp_sse_refuses_a_presented_credential_that_does_not_verify(
    make_world, tier, location, state
):
    world = make_world(tier=tier)
    headers, _ = _request_parts(location, world.token(state))
    r = world.client().get("/mcp/sse", headers=headers)
    assert r.status_code == 401, (r.status_code, r.text[:200])


@pytest.mark.parametrize(("tier", "location", "state"), _mcp_cells(lambda *_: False))
def test_mcp_messages_refuses_a_presented_credential_that_does_not_verify(
    make_world, tier, location, state
):
    """A message POST with a failing credential is refused as one, not
    answered as an unknown session."""
    world = make_world(tier=tier)
    headers, _ = _request_parts(location, world.token(state))
    headers["Content-Type"] = "application/json"
    r = world.client().post(
        f"/mcp/messages/?session_id={UNKNOWN_SESSION_ID}", headers=headers, content="{}"
    )
    assert r.status_code == 401, (r.status_code, r.text[:200])


@pytest.mark.control(reason=_UNCHANGED)
def test_mcp_messages_without_a_credential_is_still_an_unknown_session(make_world):
    world = make_world(tier="read")
    r = world.client().post(
        f"/mcp/messages/?session_id={UNKNOWN_SESSION_ID}",
        headers={"Content-Type": "application/json"},
        content="{}",
    )
    assert r.status_code == 404


@pytest.mark.parametrize(("tier", "location", "state"), _mcp_cells(lambda *_: False))
def test_mcp_info_refuses_a_presented_credential_that_does_not_verify(
    make_world, tier, location, state
):
    """``/mcp/info`` answers anyone with no credential; a credential that
    does not verify is told so instead of being shown the anonymous view."""
    world = make_world(tier=tier)
    headers, _ = _request_parts(location, world.token(state))
    r = world.client().get("/mcp/info", headers=headers)
    assert r.status_code == 401, (r.status_code, r.text[:200])


@pytest.mark.control(reason=_UNCHANGED)
def test_mcp_info_without_a_credential_is_still_unauthenticated_info(make_world):
    world = make_world(tier="read")
    r = world.client().get("/mcp/info")
    assert r.status_code == 200
    assert r.json()["tier"] == "unauthenticated"


# =============================================================================
# Property 1 on the live-update socket
# =============================================================================


def _socket_admitted(world: World, headers: dict[str, str], query: str) -> bool:
    with TestClient(world.app) as c:
        try:
            with c.websocket_connect(_with_query("/ws", query), headers=headers):
                return True
        except WebSocketDisconnect:
            return False


@pytest.mark.parametrize(
    ("tier", "location", "state"), _cells(lambda tier, location, state: tier is None)
)
def test_socket_refuses_a_presented_credential_that_does_not_verify(
    make_world, tier, location, state
):
    world = make_world(tier=tier)
    headers, query = _request_parts(location, world.token(state))
    assert not _socket_admitted(world, headers, query)


@pytest.mark.control(reason=_UNCHANGED)
@pytest.mark.parametrize("tier", ["read", "write"])
def test_socket_still_admits_no_credential_as_anonymous(make_world, tier):
    world = make_world(tier=tier)
    assert _socket_admitted(world, {}, "")


@pytest.mark.parametrize("tier", TIERS)
def test_socket_refuses_a_failing_key_next_to_a_valid_cookie(make_world, tier):
    world = make_world(tier=tier)
    headers = {"X-API-Key": WRONG, "Cookie": f"{SESSION_COOKIE}={world.live}"}
    assert not _socket_admitted(world, headers, "")


# =============================================================================
# The regimes that must not change
# =============================================================================


@pytest.mark.control(reason=_UNCHANGED)
@pytest.mark.parametrize("location", ["x-api-key", "query", "bearer"])
def test_auth_off_with_no_keys_is_still_open_whatever_key_is_sent(make_world, location):
    world = make_world(auth=False, keys=False)
    headers, query = _request_parts(location, WRONG)
    r = world.client().get(_with_query("/api/admin/usage", query), headers=headers)
    assert r.status_code == 200, (r.status_code, r.text[:200])


@pytest.mark.control(reason=_UNCHANGED)
def test_auth_off_with_no_keys_ignores_a_cookie(make_world):
    world = make_world(auth=False, keys=False)
    r = world.client().get("/api/admin/usage", headers={"Cookie": f"{SESSION_COOKIE}={WRONG}"})
    assert r.status_code == 200


@pytest.mark.control(reason=_UNCHANGED)
@pytest.mark.parametrize("location", ["x-api-key", "query"])
def test_auth_off_with_keys_still_refuses_a_wrong_key(make_world, location):
    world = make_world(auth=False, keys=True)
    headers, query = _request_parts(location, WRONG)
    assert world.client().get(_with_query("/api/kbs", query), headers=headers).status_code == 401


@pytest.mark.control(reason=_UNCHANGED)
@pytest.mark.parametrize("tier", ["read", "write"])
def test_a_verified_key_and_a_live_cookie_keep_their_access(make_world, tier):
    world = make_world(tier=tier)
    c = world.client()
    assert c.get("/api/admin/usage", headers={"X-API-Key": ADMIN_KEY}).status_code == 200
    assert c.get("/api/admin/usage", params={"api_key": ADMIN_KEY}).status_code == 200
    r = c.get("/api/admin/usage", headers={"Cookie": f"{SESSION_COOKIE}={world.live}"})
    assert r.status_code == 200


@pytest.mark.control(reason=_UNCHANGED)
def test_mcp_never_reads_the_api_key_query_parameter(make_world):
    """``/mcp`` does not take a key from the URL: ``?api_key`` is not a
    credential there, so it is neither accepted nor refused as one."""
    world = make_world(tier="read")
    c = world.client()
    r = c.get("/mcp/info", params={"api_key": WRONG})
    assert r.status_code == 200
    assert r.json()["tier"] == "unauthenticated"
    r = c.get("/mcp/info", params={"api_key": ADMIN_KEY})
    assert r.json()["tier"] == "unauthenticated"
