"""An arbitrary API key must not grant admin when auth is enabled.

With ``auth.enabled`` and no ``api_key``/``api_keys`` configured -- the normal
multi-user web setup -- both key resolvers used to answer ``"admin"`` for ANY
key value, because "no keys configured" was read as "auth disabled". A visitor
with no account who sent ``X-API-Key: <anything>`` (REST) or
``Authorization: Bearer <anything>`` (MCP) could list, read and write private
KBs. No keys configured means no key is valid; it only means "open access"
when auth is also disabled, and that default is pinned here unchanged.
"""

import tempfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server import mcp_routes
from pyrite.server.api import create_app, resolve_api_key_role
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from tests.auth_seed import seed_user

PUBLIC, PRIVATE = "pub", "priv"
BOGUS = "garbage-not-a-configured-key"


def _config(tmp: Path, *, auth_enabled: bool, anonymous_tier: str | None = "read") -> PyriteConfig:
    (tmp / PUBLIC).mkdir()
    (tmp / PRIVATE).mkdir()
    return PyriteConfig(
        knowledge_bases=[
            KBConfig(name=PUBLIC, path=tmp / PUBLIC, kb_type="generic", default_role="read"),
            KBConfig(name=PRIVATE, path=tmp / PRIVATE, kb_type="generic", default_role="none"),
        ],
        settings=Settings(
            index_path=tmp / "index.db",
            auth=AuthConfig(
                enabled=auth_enabled, allow_registration=True, anonymous_tier=anonymous_tier
            ),
        ),
    )


@pytest.fixture
def auth_env():
    """Auth enabled, anonymous read tier, no API keys, one private entry."""
    with tempfile.TemporaryDirectory() as d:
        config = _config(Path(d), auth_enabled=True)
        app = create_app(config=config)
        db = PyriteDB(config.settings.index_path)
        KBService(config, db).create_entry(PRIVATE, "secret-note", "Secret note", "note", "hidden")
        # The sole admin, seeded via the operator path (this app has no
        # test-supplied get_db override, so /auth/register -- and
        # auth_seed's own app_db-based helpers -- are unavailable here).
        seed_user(db, "admin-user", "password123", role="admin")
        try:
            yield {"app": app, "config": config}
        finally:
            db.close()


class TestResolversRefuseUnconfiguredKeys:
    def test_rest_resolver_refuses_any_key_when_auth_enabled_and_no_keys(self, tmp_path):
        config = _config(tmp_path, auth_enabled=True)
        assert resolve_api_key_role(BOGUS, config) is None

    def test_mcp_resolver_refuses_any_key_when_auth_enabled_and_no_keys(self, tmp_path):
        config = _config(tmp_path, auth_enabled=True)
        assert mcp_routes._resolve_api_key_role(BOGUS, config) is None

    def test_auth_disabled_and_no_keys_is_still_open_access(self, tmp_path):
        """The default local install is unchanged."""
        config = _config(tmp_path, auth_enabled=False, anonymous_tier=None)
        assert resolve_api_key_role(BOGUS, config) == "admin"
        assert mcp_routes._resolve_api_key_role(BOGUS, config) == "admin"


class TestBogusKeyOverRest:
    def test_bogus_key_does_not_reveal_the_private_kb(self, auth_env):
        """Not admin, and since #1754 not the anonymous visitor either: a key
        that does not verify is refused, even with an anonymous tier."""
        anon = TestClient(auth_env["app"])
        r = anon.get("/api/kbs", headers={"X-API-Key": BOGUS})
        assert r.status_code == 401, r.text
        assert PRIVATE not in r.text

    def test_bogus_key_cannot_read_a_private_entry(self, auth_env):
        anon = TestClient(auth_env["app"])
        r = anon.get(
            "/api/entries/secret-note", params={"kb": PRIVATE}, headers={"X-API-Key": BOGUS}
        )
        assert r.status_code == 401, r.text  # 404 (served as anonymous) before #1754
        assert "hidden" not in r.text

    def test_bogus_key_cannot_write_into_the_private_kb(self, auth_env):
        anon = TestClient(auth_env["app"])
        r = anon.post(
            "/api/entries",
            json={"kb": PRIVATE, "title": "planted", "body": "x", "entry_type": "note"},
            headers={"X-API-Key": BOGUS},
        )
        assert r.status_code in (401, 403, 404), r.text
        assert not list((auth_env["config"].get_kb(PRIVATE).path).rglob("planted*.md"))

    def test_bogus_key_without_anonymous_tier_is_401(self, tmp_path):
        config = _config(tmp_path, auth_enabled=True, anonymous_tier=None)
        r = TestClient(create_app(config=config)).get("/api/kbs", headers={"X-API-Key": BOGUS})
        assert r.status_code == 401, r.text

    def test_bogus_key_beside_a_valid_session_cookie_is_refused(self, auth_env):
        """The key is the credential presented first; it does not verify, so
        the request is refused rather than falling through to the cookie
        (#1754: a failing credential never falls through to a weaker one).
        It never reached past the user's own scope either way."""
        user = TestClient(auth_env["app"])
        r = user.post("/auth/register", json={"username": "peer", "password": "password123"})
        assert r.status_code == 200, r.text
        r = user.get("/api/kbs", headers={"X-API-Key": BOGUS})
        assert r.status_code == 401, r.text
        assert user.get("/api/kbs").status_code == 200  # the cookie alone still works


class TestBogusBearerOverMcp:
    def test_bogus_bearer_is_not_admin_on_mcp_info(self, auth_env):
        r = TestClient(auth_env["app"]).get(
            "/mcp/info", headers={"Authorization": f"Bearer {BOGUS}"}
        )
        # Refused since #1754 (it was shown the unauthenticated view).
        assert r.status_code == 401, r.text
        assert "tools_count" not in r.text

    def test_bogus_x_api_key_is_not_admin_on_mcp_info(self, auth_env):
        r = TestClient(auth_env["app"]).get("/mcp/info", headers={"X-API-Key": BOGUS})
        assert r.status_code == 401, r.text  # the unauthenticated view before #1754
        assert "tools_count" not in r.text

    def test_real_session_token_as_bearer_resolves_the_user_on_mcp(self, auth_env):
        """The session lookup on /mcp used to crash: the routes were handed the
        per-request generator dependency (#131) and called it, getting a
        generator instead of a database. A bogus key hid that by being answered
        "admin" before any lookup; now every non-key bearer reaches it."""
        user = TestClient(auth_env["app"])
        r = user.post("/auth/register", json={"username": "peer", "password": "password123"})
        assert r.status_code == 200, r.text
        token = user.cookies.get("pyrite_session")
        assert token
        r = TestClient(auth_env["app"]).get(
            "/mcp/info", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["tier"] == "read"

    def test_bogus_bearer_is_refused_on_mcp_sse(self, auth_env):
        r = TestClient(auth_env["app"]).get(
            "/mcp/sse", headers={"Authorization": f"Bearer {BOGUS}"}
        )
        assert r.status_code == 401, r.text
