"""Tests for AuthService: registration, login, sessions, roles."""

import pytest


from pyrite.config import AuthConfig
from pyrite.services.auth_service import AuthService
from pyrite.storage.database import PyriteDB


@pytest.fixture
def auth_env(tmp_path):
    """A fresh DB + AuthService with an operator-created admin ("root"):
    registration is closed until one exists."""
    db_path = tmp_path / "index.db"
    with PyriteDB(db_path) as db:
        config = AuthConfig(enabled=True)
        service = AuthService(db, config)
        service.create_user("root", "rootpass123", role="admin")
        yield service, db


class TestRegister:
    def test_registrant_is_read_not_admin(self, auth_env):
        service, _ = auth_env
        user = service.register("alice", "password123")
        assert user["role"] == "read"
        assert user["username"] == "alice"

    def test_second_user_is_read(self, auth_env):
        service, _ = auth_env
        service.register("alice", "password123")
        user = service.register("bob", "password456")
        assert user["role"] == "read"

    def test_duplicate_username_raises(self, auth_env):
        service, _ = auth_env
        service.register("alice", "password123")
        with pytest.raises(ValueError, match="already taken"):
            service.register("alice", "password456")

    def test_empty_username_raises(self, auth_env):
        service, _ = auth_env
        with pytest.raises(ValueError, match="required"):
            service.register("", "password123")

    def test_short_password_raises(self, auth_env):
        service, _ = auth_env
        with pytest.raises(ValueError, match="at least 8"):
            service.register("alice", "short")

    def test_registration_disabled(self, tmp_path):
        with PyriteDB(tmp_path / "index.db") as db:
            config = AuthConfig(enabled=True, allow_registration=False)
            service = AuthService(db, config)
            with pytest.raises(ValueError, match="disabled"):
                service.register("alice", "password123")

    def test_display_name_stored(self, auth_env):
        service, _ = auth_env
        user = service.register("alice", "password123", display_name="Alice Smith")
        assert user["display_name"] == "Alice Smith"


class TestLogin:
    @pytest.mark.core
    def test_login_success(self, auth_env):
        service, _ = auth_env
        service.register("alice", "password123")
        user, token = service.login("alice", "password123")
        assert user["username"] == "alice"
        assert len(token) > 20

    @pytest.mark.core
    def test_login_wrong_password(self, auth_env):
        service, _ = auth_env
        service.register("alice", "password123")
        with pytest.raises(ValueError, match="Invalid"):
            service.login("alice", "wrongpass")

    def test_login_nonexistent_user(self, auth_env):
        service, _ = auth_env
        with pytest.raises(ValueError, match="Invalid"):
            service.login("nobody", "password123")


class TestSessions:
    @pytest.mark.core
    def test_verify_session_valid(self, auth_env):
        service, _ = auth_env
        service.register("alice", "password123")
        _, token = service.login("alice", "password123")
        user = service.verify_session(token)
        assert user is not None
        assert user["username"] == "alice"

    def test_verify_session_invalid_token(self, auth_env):
        service, _ = auth_env
        assert service.verify_session("bogus-token") is None

    def test_logout_deletes_session(self, auth_env):
        service, _ = auth_env
        service.register("alice", "password123")
        _, token = service.login("alice", "password123")
        assert service.logout(token) is True
        assert service.verify_session(token) is None

    def test_logout_nonexistent_token(self, auth_env):
        service, _ = auth_env
        assert service.logout("bogus") is False

    def test_logout_all(self, auth_env):
        service, _ = auth_env
        reg = service.register("alice", "password123")
        service.login("alice", "password123")
        service.login("alice", "password123")
        count = service.logout_all(reg["id"])
        assert count == 2

    def test_max_sessions_enforced(self, tmp_path):
        with PyriteDB(tmp_path / "index.db") as db:
            config = AuthConfig(enabled=True, max_sessions_per_user=2)
            service = AuthService(db, config)
            service.create_user("alice", "password123")
            _, t1 = service.login("alice", "password123")
            _, t2 = service.login("alice", "password123")
            _, t3 = service.login("alice", "password123")
            # Oldest session (t1) should have been evicted
            assert service.verify_session(t1) is None
            assert service.verify_session(t2) is not None
            assert service.verify_session(t3) is not None


class TestRoles:
    def test_get_user(self, auth_env):
        service, _ = auth_env
        reg = service.register("alice", "password123")
        user = service.get_user(reg["id"])
        assert user["username"] == "alice"

    def test_get_user_not_found(self, auth_env):
        service, _ = auth_env
        assert service.get_user(999) is None

    def test_set_role(self, auth_env):
        service, _ = auth_env
        service.register("alice", "password123")  # "root" is the sole admin
        reg = service.register("bob", "password456")
        assert service.set_role(reg["id"], "write") is True
        user = service.get_user(reg["id"])
        assert user["role"] == "write"

    def test_set_invalid_role(self, auth_env):
        service, _ = auth_env
        reg = service.register("alice", "password123")
        with pytest.raises(ValueError, match="Invalid role"):
            service.set_role(reg["id"], "superadmin")


class TestUsageTier:
    """wire-user-usage-tier-resolution-for-quota-enforcement: usage_tier
    (free/pro/enterprise, a resource/billing axis) is distinct from role
    (read/write/admin, an auth-permission axis). The local_user.usage_tier
    column has existed since migration v10 but nothing read or wrote it."""

    def test_get_user_includes_usage_tier(self, auth_env):
        service, _ = auth_env
        reg = service.register("alice", "password123")
        user = service.get_user(reg["id"])
        assert "usage_tier" in user

    def test_new_user_defaults_to_default_tier(self, auth_env):
        service, _ = auth_env
        reg = service.register("alice", "password123")
        user = service.get_user(reg["id"])
        assert user["usage_tier"] == "default"

    def test_set_usage_tier(self, auth_env):
        service, _ = auth_env
        reg = service.register("alice", "password123")
        assert service.set_usage_tier(reg["id"], "pro") is True
        user = service.get_user(reg["id"])
        assert user["usage_tier"] == "pro"

    def test_set_usage_tier_unknown_user_returns_false(self, auth_env):
        service, _ = auth_env
        assert service.set_usage_tier(999, "pro") is False


class TestOAuthStateStore:
    """oauth-state-store-persistence: CSRF state moved from an in-memory
    dict to the DB (oauth_state table, migration v22) so a process restart
    or multi-replica deploy doesn't invalidate an in-flight OAuth login.

    Each state is bound to a browser: ``create_oauth_state`` returns the
    public state and a binding value the caller puts in an HttpOnly cookie;
    ``verify_oauth_state`` needs both."""

    def test_create_and_verify_roundtrip(self, auth_env):
        service, _ = auth_env
        state, binding = service.create_oauth_state(flow="login")
        assert isinstance(state, str) and len(state) > 20
        assert isinstance(binding, str) and len(binding) > 20
        assert binding != state

        data = service.verify_oauth_state(state, binding)
        assert data is not None
        assert data["flow"] == "login"
        assert data["user_id"] is None

    def test_verify_consumes_state(self, auth_env):
        """A state token is single-use: verifying it once succeeds, a
        second verify of the same token must fail (already consumed)."""
        service, _ = auth_env
        state, binding = service.create_oauth_state(flow="login")
        assert service.verify_oauth_state(state, binding) is not None
        assert service.verify_oauth_state(state, binding) is None

    def test_verify_unknown_state_returns_none(self, auth_env):
        service, _ = auth_env
        assert service.verify_oauth_state("nonexistent-state-token", "x") is None

    def test_verify_with_another_binding_returns_none_and_keeps_the_state(self, auth_env):
        service, _ = auth_env
        state, binding = service.create_oauth_state(flow="login")
        _other_state, other_binding = service.create_oauth_state(flow="login")
        assert service.verify_oauth_state(state, other_binding) is None
        assert service.verify_oauth_state(state, "") is None
        # The mismatched attempts did not consume the real one.
        assert service.verify_oauth_state(state, binding) is not None

    def test_verify_expired_state_returns_none(self, auth_env):
        service, _ = auth_env
        state, binding = service.create_oauth_state(flow="login", ttl_seconds=-1)
        assert service.verify_oauth_state(state, binding) is None

    def test_create_stores_user_id_for_connect_flow(self, auth_env):
        service, _ = auth_env
        state, binding = service.create_oauth_state(flow="connect", user_id=42)
        data = service.verify_oauth_state(state, binding)
        assert data["flow"] == "connect"
        assert data["user_id"] == 42

    def test_concurrent_verify_succeeds_at_most_once(self, auth_env, monkeypatch):
        """Two callbacks racing on one state: the second verify runs to
        completion between the first one's read and its delete. Only one
        may succeed."""
        service, db = auth_env
        state, binding = service.create_oauth_state(flow="login")

        real_execute_sql = db.execute_sql
        results: list = []
        raced = {"done": False}

        def interleaving_execute_sql(sql, params=None):
            rows = real_execute_sql(sql, params)
            if "FROM oauth_state" in sql and not raced["done"]:
                raced["done"] = True
                results.append(service.verify_oauth_state(state, binding))
            return rows

        monkeypatch.setattr(db, "execute_sql", interleaving_execute_sql)
        results.append(service.verify_oauth_state(state, binding))

        assert raced["done"], "the race was not staged: verify no longer reads first"
        assert sum(r is not None for r in results) == 1, results

    def test_cleanup_expired_states_removes_only_expired(self, auth_env):
        service, db = auth_env
        fresh = service.create_oauth_state(flow="login")
        service.create_oauth_state(flow="login", ttl_seconds=-1)

        removed = service.cleanup_expired_oauth_states()
        assert removed >= 1

        rows = db.execute_sql("SELECT state FROM oauth_state")
        assert len(rows) == 1
        assert service.verify_oauth_state(*fresh) is not None

    def test_create_probabilistically_sweeps_expired_states(self, auth_env, monkeypatch):
        """create_oauth_state does opportunistic cleanup on a 1-in-10 draw
        (same pattern as verify_session's session cleanup), so expired rows
        don't accumulate forever without a dedicated background task."""
        service, db = auth_env
        service.create_oauth_state(flow="login", ttl_seconds=-1)
        assert len(db.execute_sql("SELECT state FROM oauth_state")) == 1

        # Force the probabilistic branch to fire on the next call.
        monkeypatch.setattr("pyrite.services.auth_service.secrets.randbelow", lambda _n: 0)
        service.create_oauth_state(flow="login")

        rows = db.execute_sql("SELECT state FROM oauth_state")
        assert len(rows) == 1, "expired state should have been swept by the probabilistic cleanup"

    def test_state_persists_across_service_instances_same_db(self, auth_env):
        """The whole point of the DB-backed store: state created on one
        AuthService instance must be verifiable from a second, independent
        instance backed by the same DB — simulating a process restart or
        a second replica reading the same DB."""
        service_a, db = auth_env
        state, binding = service_a.create_oauth_state(flow="login")

        from pyrite.config import AuthConfig
        from pyrite.services.auth_service import AuthService

        service_b = AuthService(db, AuthConfig(enabled=True))
        data = service_b.verify_oauth_state(state, binding)
        assert data is not None
        assert data["flow"] == "login"
