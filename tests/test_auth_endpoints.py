"""Tests for auth API endpoints: register, login, logout, session, OAuth."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig, OAuthProviderConfig, PyriteConfig, Settings
from pyrite.server.api import create_app, get_config, get_db
from pyrite.services.oauth_providers import OAuthProfile, OAuthToken
from pyrite.storage.database import PyriteDB
from tests.auth_seed import seed_user


def _make_client(tmpdir, auth_enabled=True, allow_registration=True, providers=None):
    """Create TestClient with auth-enabled config."""
    db_path = tmpdir / "index.db"
    kb_path = tmpdir / "kb"
    kb_path.mkdir(exist_ok=True)

    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="test-kb", path=kb_path, kb_type="generic")],
        settings=Settings(
            index_path=db_path,
            auth=AuthConfig(
                enabled=auth_enabled,
                allow_registration=allow_registration,
                providers=providers or {},
            ),
        ),
    )

    application = create_app(config=config)
    db = PyriteDB(db_path)
    application.dependency_overrides[get_config] = lambda: config
    application.dependency_overrides[get_db] = lambda: db
    if auth_enabled:
        # Sign-up is closed until an operator-created admin exists (#13).
        from pyrite.services.auth_service import AuthService
        from tests.auth_seed import seed_user

        if not AuthService(db, config.settings.auth).admin_exists():
            seed_user(db, "root", "rootpass123", role="admin")

    return TestClient(application), config, db


@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def auth_client(tmpdir):
    """App with auth enabled."""
    client, config, db = _make_client(tmpdir)
    return client


@pytest.fixture
def noauth_client(tmpdir):
    """App with auth disabled (default behavior)."""
    client, config, db = _make_client(tmpdir, auth_enabled=False)
    return client


class TestAuthConfig:
    def test_config_endpoint_auth_enabled(self, auth_client):
        r = auth_client.get("/auth/config")
        assert r.status_code == 200
        data = r.json()
        assert data["enabled"] is True
        assert data["allow_registration"] is True

    def test_config_endpoint_auth_disabled(self, noauth_client):
        r = noauth_client.get("/auth/config")
        assert r.status_code == 200
        assert r.json()["enabled"] is False


class TestRegisterEndpoint:
    def test_register_user(self, auth_client):
        r = auth_client.post(
            "/auth/register",
            json={
                "username": "alice",
                "password": "password123",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["username"] == "alice"
        assert data["role"] == "read"

    def test_register_sets_cookie(self, auth_client):
        r = auth_client.post(
            "/auth/register",
            json={
                "username": "alice",
                "password": "password123",
            },
        )
        assert r.status_code == 200
        assert "pyrite_session" in r.cookies

    def test_register_duplicate(self, auth_client):
        auth_client.post(
            "/auth/register",
            json={
                "username": "alice",
                "password": "password123",
            },
        )
        r = auth_client.post(
            "/auth/register",
            json={
                "username": "alice",
                "password": "password456",
            },
        )
        assert r.status_code == 400

    @pytest.mark.control(
        reason="pins behaviour dev already had (only the registration call, "
        "not the auto-login, is inside try/except ValueError); an earlier "
        "version of this PR bundled both under one except and briefly broke "
        "this, so it is pinned here even though the merge base itself was "
        "never red"
    )
    def test_register_auto_login_value_error_is_not_reported_as_a_registration_failure(
        self, auth_client
    ):
        """A ValueError from the auto-login after a successful registration
        (#440) must not be mapped to 400: the account was already created,
        so that response would say "bad request" about a request that
        succeeded. The registration call is what 400 is for; the auto-login
        is a separate step outside that except, exactly as before this PR
        moved both calls off the event loop -- it propagates unhandled, as
        it always did (TestClient's default re-raises it rather than
        translating it to a response)."""
        with (
            patch(
                "pyrite.services.auth_service.AuthService.login",
                side_effect=ValueError("boom"),
            ),
            pytest.raises(ValueError, match="boom"),
        ):
            auth_client.post(
                "/auth/register",
                json={"username": "alice", "password": "password123"},
            )

    def test_register_disabled(self, tmpdir):
        client, _, _ = _make_client(tmpdir, allow_registration=False)
        r = client.post(
            "/auth/register",
            json={
                "username": "alice",
                "password": "password123",
            },
        )
        assert r.status_code == 400


class TestLoginEndpoint:
    def test_login_success(self, auth_client):
        auth_client.post(
            "/auth/register",
            json={
                "username": "alice",
                "password": "password123",
            },
        )
        r = auth_client.post(
            "/auth/login",
            json={
                "username": "alice",
                "password": "password123",
            },
        )
        assert r.status_code == 200
        assert "pyrite_session" in r.cookies

    def test_login_wrong_password(self, auth_client):
        auth_client.post(
            "/auth/register",
            json={
                "username": "alice",
                "password": "password123",
            },
        )
        r = auth_client.post(
            "/auth/login",
            json={
                "username": "alice",
                "password": "wrong",
            },
        )
        assert r.status_code == 401


class TestMeEndpoint:
    def test_me_authenticated(self, auth_client):
        auth_client.post(
            "/auth/register",
            json={
                "username": "alice",
                "password": "password123",
            },
        )
        # Login to get the cookie
        auth_client.post(
            "/auth/login",
            json={
                "username": "alice",
                "password": "password123",
            },
        )
        r = auth_client.get("/auth/me")
        assert r.status_code == 200
        assert r.json()["username"] == "alice"

    def test_me_unauthenticated(self, auth_client):
        r = auth_client.get("/auth/me")
        assert r.status_code == 401


class TestLogoutEndpoint:
    def test_logout_clears_session(self, auth_client):
        auth_client.post(
            "/auth/register",
            json={
                "username": "alice",
                "password": "password123",
            },
        )
        auth_client.post(
            "/auth/login",
            json={
                "username": "alice",
                "password": "password123",
            },
        )
        r = auth_client.post("/auth/logout")
        assert r.status_code == 200
        # After logout, /auth/me should fail
        r = auth_client.get("/auth/me")
        assert r.status_code == 401


class TestAPIWithAuth:
    def test_api_requires_auth_when_enabled(self, auth_client):
        """API endpoints return 401 when auth enabled and no session/key."""
        r = auth_client.get("/api/kbs")
        assert r.status_code == 401

    def test_api_works_with_session(self, auth_client):
        """API works when authenticated via session cookie."""
        auth_client.post(
            "/auth/register",
            json={
                "username": "alice",
                "password": "password123",
            },
        )
        auth_client.post(
            "/auth/login",
            json={
                "username": "alice",
                "password": "password123",
            },
        )
        r = auth_client.get("/api/kbs")
        assert r.status_code == 200

    def test_api_works_without_auth_when_disabled(self, noauth_client):
        """Default behavior preserved: no auth = admin access."""
        r = noauth_client.get("/api/kbs")
        assert r.status_code == 200


class TestOAuthEndpoints:
    @pytest.fixture
    def oauth_client(self, tmpdir):
        providers = {
            "github": OAuthProviderConfig(
                client_id="test-client-id",
                client_secret="test-client-secret",
            )
        }
        client, config, db = _make_client(tmpdir, providers=providers)
        return client

    def test_github_redirect(self, oauth_client):
        r = oauth_client.get("/auth/github", follow_redirects=False)
        assert r.status_code == 302
        assert "github.com/login/oauth/authorize" in r.headers["location"]
        assert "client_id=test-client-id" in r.headers["location"]

    def test_github_not_configured(self, auth_client):
        r = auth_client.get("/auth/github")
        assert r.status_code == 404

    def test_callback_invalid_state(self, oauth_client):
        r = oauth_client.get(
            "/auth/github/callback?code=abc&state=invalid",
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "error=oauth_failed" in r.headers["location"]

    def test_callback_success(self, tmpdir):
        providers = {
            "github": OAuthProviderConfig(
                client_id="test-client-id",
                client_secret="test-client-secret",
            )
        }
        client, config, db = _make_client(tmpdir, providers=providers)

        # First, get a valid state by hitting /auth/github
        r = client.get("/auth/github", follow_redirects=False)
        location = r.headers["location"]
        # Extract state from URL
        import urllib.parse

        parsed = urllib.parse.urlparse(location)
        qs = urllib.parse.parse_qs(parsed.query)
        state = qs["state"][0]

        mock_token = OAuthToken(access_token="gho_test")
        mock_profile = OAuthProfile(
            provider="github",
            provider_id="12345",
            username="testuser",
            display_name="Test User",
            email="test@example.com",
            avatar_url="https://example.com/avatar.png",
            orgs=[],
        )

        with (
            patch(
                "pyrite.server.auth_endpoints.GitHubOAuthProvider.exchange_code",
                new_callable=AsyncMock,
                return_value=mock_token,
            ),
            patch(
                "pyrite.server.auth_endpoints.GitHubOAuthProvider.get_user_profile",
                new_callable=AsyncMock,
                return_value=mock_profile,
            ),
        ):
            r = client.get(
                f"/auth/github/callback?code=testcode&state={state}",
                follow_redirects=False,
            )

        assert r.status_code == 302
        assert r.headers["location"] == "/"
        assert "pyrite_session" in r.cookies

    def test_auth_config_includes_providers(self, oauth_client):
        r = oauth_client.get("/auth/config")
        assert r.status_code == 200
        data = r.json()
        assert "github" in data["providers"]

    def test_auth_config_no_providers(self, auth_client):
        r = auth_client.get("/auth/config")
        assert r.status_code == 200
        data = r.json()
        assert data["providers"] == []

    def test_oauth_state_survives_process_restart(self, tmpdir):
        """oauth-state-store-persistence: OAuth CSRF state must be stored
        in the DB, not an in-memory dict, so a process restart between
        /auth/github and /auth/github/callback doesn't fail the login.

        A real process restart can't be simulated within one pytest
        process — a module-level dict would trivially "survive" here
        because it's the same Python process regardless of which DB file
        backs each client. So this test explicitly clears any in-memory
        state store between the two client constructions (imitating what
        a process restart does to module globals) to prove the state was
        actually persisted to the DB, not merely still sitting in memory."""
        providers = {
            "github": OAuthProviderConfig(
                client_id="test-client-id",
                client_secret="test-client-secret",
            )
        }
        client_a, _config_a, db_a = _make_client(tmpdir, providers=providers)

        r = client_a.get("/auth/github", follow_redirects=False)
        assert r.status_code == 302
        import urllib.parse

        parsed = urllib.parse.urlparse(r.headers["location"])
        state = urllib.parse.parse_qs(parsed.query)["state"][0]
        # The browser outlives the server process: it still holds the
        # binding cookie set when the flow started.
        binding = client_a.cookies.get("pyrite_oauth_binding")
        assert binding
        db_a.close()

        # Simulate a process restart: wipe any in-memory state store (what
        # a real restart does to module globals) before the fresh instance.
        import pyrite.server.auth_endpoints as auth_endpoints_module

        if hasattr(auth_endpoints_module, "_oauth_states"):
            auth_endpoints_module._oauth_states.clear()

        client_b, _config_b, db_b = _make_client(tmpdir, providers=providers)
        client_b.cookies.set("pyrite_oauth_binding", binding, path="/auth/github")

        mock_token = OAuthToken(access_token="gho_test")
        mock_profile = OAuthProfile(
            provider="github",
            provider_id="12345",
            username="testuser",
            display_name="Test User",
            email="test@example.com",
            avatar_url="https://example.com/avatar.png",
            orgs=[],
        )
        with (
            patch(
                "pyrite.server.auth_endpoints.GitHubOAuthProvider.exchange_code",
                new_callable=AsyncMock,
                return_value=mock_token,
            ),
            patch(
                "pyrite.server.auth_endpoints.GitHubOAuthProvider.get_user_profile",
                new_callable=AsyncMock,
                return_value=mock_profile,
            ),
        ):
            r = client_b.get(
                f"/auth/github/callback?code=testcode&state={state}",
                follow_redirects=False,
            )

        assert r.status_code == 302
        assert r.headers["location"] == "/", (
            f"expected successful login redirect, got {r.headers.get('location')} "
            "-- state did not survive the simulated restart"
        )
        assert "pyrite_session" in r.cookies
        db_b.close()

    def test_me_includes_kb_permissions(self, tmpdir):
        client, config, db = _make_client(tmpdir)
        # Register and login as admin
        seed_user(db, "admin", "password123", role="admin")
        client.post(
            "/auth/login",
            json={
                "username": "admin",
                "password": "password123",
            },
        )

        # Grant a KB permission via service
        from pyrite.services.auth_service import AuthService

        auth = AuthService(db, config.settings.auth)
        # Get admin user id
        user = auth.verify_session(client.cookies.get("pyrite_session"))
        auth.grant_kb_permission(user["id"], "test-kb", "admin", user["id"])

        r = client.get("/auth/me")
        assert r.status_code == 200
        data = r.json()
        assert "kb_permissions" in data
        assert data["kb_permissions"]["test-kb"] == "admin"

    def test_me_includes_avatar(self, tmpdir):
        providers = {
            "github": OAuthProviderConfig(
                client_id="test-client-id",
                client_secret="test-client-secret",
            )
        }
        client, config, db = _make_client(tmpdir, providers=providers)

        # Get valid state
        r = client.get("/auth/github", follow_redirects=False)
        import urllib.parse

        parsed = urllib.parse.urlparse(r.headers["location"])
        qs = urllib.parse.parse_qs(parsed.query)
        state = qs["state"][0]

        mock_token = OAuthToken(access_token="gho_test")
        mock_profile = OAuthProfile(
            provider="github",
            provider_id="99999",
            username="avataruser",
            display_name="Avatar User",
            avatar_url="https://example.com/avatar.png",
            orgs=[],
        )

        with (
            patch(
                "pyrite.server.auth_endpoints.GitHubOAuthProvider.exchange_code",
                new_callable=AsyncMock,
                return_value=mock_token,
            ),
            patch(
                "pyrite.server.auth_endpoints.GitHubOAuthProvider.get_user_profile",
                new_callable=AsyncMock,
                return_value=mock_profile,
            ),
        ):
            client.get(
                f"/auth/github/callback?code=testcode&state={state}",
                follow_redirects=False,
            )

        r = client.get("/auth/me")
        assert r.status_code == 200
        data = r.json()
        assert data["avatar_url"] == "https://example.com/avatar.png"
        assert data["auth_provider"] == "github"


class TestEphemeralKBEndpoint:
    def test_ephemeral_kb_create(self, tmpdir):
        client, config, db = _make_client(tmpdir)
        # Seed an admin (the operator path; admin >= write)
        seed_user(db, "admin", "password123", role="admin")
        client.post(
            "/auth/login",
            json={
                "username": "admin",
                "password": "password123",
            },
        )

        r = client.post("/api/kbs/ephemeral", json={})
        assert r.status_code == 200
        data = r.json()
        assert data["created"] is True
        assert data["ephemeral"] is True

        listing = client.get("/api/kbs/ephemeral")
        assert listing.status_code == 200
        assert listing.json()["count"] == 1
        assert listing.json()["ephemeral_kbs"][0]["name"] == data["name"]

    def test_ephemeral_kb_limit(self, tmpdir):
        client, config, db = _make_client(tmpdir)
        # Seed an admin
        seed_user(db, "admin", "password123", role="admin")
        client.post(
            "/auth/login",
            json={
                "username": "admin",
                "password": "password123",
            },
        )

        # First should succeed
        r = client.post("/api/kbs/ephemeral", json={})
        assert r.status_code == 200

        # Second should fail (limit=1 from default config)
        r = client.post("/api/kbs/ephemeral", json={})
        assert r.status_code == 403

    def test_ephemeral_kb_unauthenticated(self, tmpdir):
        client, config, db = _make_client(tmpdir)
        r = client.post("/api/kbs/ephemeral", json={})
        assert r.status_code == 401


class TestKBPermissionsCRUD:
    def _setup_admin(self, tmpdir):
        client, config, db = _make_client(tmpdir)
        seed_user(db, "admin", "password123", role="admin")
        client.post(
            "/auth/login",
            json={
                "username": "admin",
                "password": "password123",
            },
        )
        return client, config, db

    def test_list_permissions(self, tmpdir):
        client, config, db = self._setup_admin(tmpdir)
        r = client.get("/api/kbs/test-kb/permissions")
        assert r.status_code == 200
        data = r.json()
        assert data["kb_name"] == "test-kb"
        assert data["permissions"] == []

    def test_grant_and_list(self, tmpdir):
        client, config, db = self._setup_admin(tmpdir)

        # Register a second user
        from pyrite.services.auth_service import AuthService

        auth = AuthService(db, config.settings.auth)
        user2 = auth.register("bob", "password123", "Bob")

        # Grant permission
        r = client.post(
            "/api/kbs/test-kb/permissions",
            json={
                "user_id": user2["id"],
                "role": "write",
            },
        )
        assert r.status_code == 200
        assert r.json()["granted"] is True

        # List and verify
        r = client.get("/api/kbs/test-kb/permissions")
        perms = r.json()["permissions"]
        assert len(perms) == 1
        assert perms[0]["role"] == "write"

    def test_revoke_permission(self, tmpdir):
        client, config, db = self._setup_admin(tmpdir)

        from pyrite.services.auth_service import AuthService

        auth = AuthService(db, config.settings.auth)
        user2 = auth.register("bob", "password123", "Bob")

        # Grant then revoke
        client.post(
            "/api/kbs/test-kb/permissions",
            json={
                "user_id": user2["id"],
                "role": "write",
            },
        )
        r = client.post(
            "/api/kbs/test-kb/permissions",
            json={
                "user_id": user2["id"],
                "revoke": True,
            },
        )
        assert r.status_code == 200
        assert r.json()["revoked"] is True

        # Verify empty
        r = client.get("/api/kbs/test-kb/permissions")
        assert r.json()["permissions"] == []


@pytest.mark.control(
    reason="characterization for #380: pins behaviour before it moves behind a service, so it passes with and without the change by design"
)
class TestKBPermissionsForKBAdmins:
    """The non-global-admin branch of /api/kbs/{name}/permissions: a KB admin
    by grant or by the KB's default role may manage grants, anyone else gets
    403. Characterizes #380's move of AuthService and the default-role lookup
    to providers."""

    def _clients(self, tmpdir):
        client, config, db = _make_client(tmpdir)
        client.post("/auth/register", json={"username": "admin", "password": "password123"})
        from pyrite.services.auth_service import AuthService

        auth = AuthService(db, config.settings.auth)
        users = {}
        for name in ("bob", "carol"):
            c = TestClient(client.app)
            r = c.post("/auth/register", json={"username": name, "password": "password123"})
            auth.set_role(r.json()["id"], "write")
            users[name] = (c, r.json()["id"])
        return client, config, auth, users

    def test_kb_admin_by_grant_manages_permissions(self, tmpdir):
        admin, _config, auth, users = self._clients(tmpdir)
        bob, bob_id = users["bob"]
        _carol, carol_id = users["carol"]
        auth.grant_kb_permission(bob_id, "test-kb", "admin", None)

        r = bob.get("/api/kbs/test-kb/permissions")
        assert r.status_code == 200, r.text
        assert [p["role"] for p in r.json()["permissions"]] == ["admin"]
        r = bob.post("/api/kbs/test-kb/permissions", json={"user_id": carol_id, "role": "read"})
        assert r.status_code == 200, r.text

    def test_no_grant_is_refused(self, tmpdir):
        _admin, _config, _auth, users = self._clients(tmpdir)
        carol, carol_id = users["carol"]
        for r in (
            carol.get("/api/kbs/test-kb/permissions"),
            carol.post("/api/kbs/test-kb/permissions", json={"user_id": carol_id, "role": "admin"}),
        ):
            # test-kb has no default_role, so carol (no global access, no
            # grant) cannot read it: the KB is resolved before any role is
            # looked at and a KB the caller cannot read answers like a
            # missing one (ADR-0037 §4, the one resolver #952), not 403.
            assert r.status_code == 404, r.text
            assert r.json()["detail"]["code"] == "KB_NOT_FOUND"

    def test_kb_default_role_admin_counts(self, tmpdir):
        _admin, config, auth, users = self._clients(tmpdir)
        carol, carol_id = users["carol"]
        # Since #467 a KB's default_role reaches past read only for a user an
        # admin gave global access.
        auth.set_role(carol_id, "write", global_access=True)
        config.knowledge_bases[0].default_role = "admin"
        assert carol.get("/api/kbs/test-kb/permissions").status_code == 200
