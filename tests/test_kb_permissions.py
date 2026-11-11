"""Tests for per-KB permissions: role resolution, grant/revoke, ephemeral KB creation."""

import pytest

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.services.auth_service import AuthService
from pyrite.storage.database import PyriteDB
from tests.auth_seed import seed_user


@pytest.fixture(autouse=True)
def _as_the_local_operator():
    """These tests call ``KBRegistryService.add_kb`` directly, as the
    operator's own process does (``acting.is_local_process``). A network
    caller is held to ``settings.kb_roots``:
    ``tests/test_kb_roots_limit_network_registration.py``."""
    from pyrite.services.acting import local_process

    with local_process(True):
        yield


@pytest.fixture
def tmpdir(tmp_path):
    return tmp_path


@pytest.fixture
def setup(tmpdir):
    """Create DB, config, and auth service with two users."""
    db_path = tmpdir / "index.db"
    kb_path = tmpdir / "kb"
    kb_path.mkdir()

    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="test-kb", path=kb_path, kb_type="generic")],
        settings=Settings(
            index_path=db_path,
            auth=AuthConfig(
                enabled=True,
                anonymous_tier="read",
                ephemeral_min_tier="write",
                ephemeral_max_per_user=1,
            ),
        ),
    )
    with PyriteDB(db_path) as db:
        auth = AuthService(db, config.settings.auth)

        # Seed admin (first user) and a regular user via the operator path.
        admin = seed_user(db, "admin", "password123", role="admin", display_name="Admin User")
        user = seed_user(db, "alice", "password123", role="read", display_name="Alice")

        yield auth, db, config, admin, user


class TestGetKBRole:
    def test_explicit_grant(self, setup):
        auth, db, config, admin, user = setup
        auth.grant_kb_permission(user["id"], "test-kb", "write", admin["id"])
        role = auth.get_kb_role(user["id"], "test-kb")
        assert role == "write"

    def test_kb_default_role(self, setup):
        auth, db, config, admin, user = setup
        # No explicit grant, KB default_role is "read"
        role = auth.get_kb_role(user["id"], "test-kb", kb_default_role="read")
        assert role == "read"

    def test_global_fallback(self, setup):
        auth, db, config, admin, user = setup
        # No grant, no KB default → falls back to user global role
        role = auth.get_kb_role(user["id"], "test-kb")
        assert role == "read"  # alice's global role

    def test_admin_override(self, setup):
        auth, db, config, admin, user = setup
        # Global admin always gets "admin" regardless of KB settings
        role = auth.get_kb_role(admin["id"], "test-kb", kb_default_role="none")
        assert role == "admin"

    def test_private_kb_no_grant(self, setup):
        auth, db, config, admin, user = setup
        # Private KB (default_role="none") without explicit grant → None
        role = auth.get_kb_role(user["id"], "private-kb", kb_default_role="none")
        assert role is None

    def test_private_kb_with_grant(self, setup):
        auth, db, config, admin, user = setup
        auth.grant_kb_permission(user["id"], "private-kb", "write", admin["id"])
        role = auth.get_kb_role(user["id"], "private-kb", kb_default_role="none")
        assert role == "write"

    def test_anonymous_user_public_kb(self, setup):
        auth, db, config, admin, user = setup
        role = auth.get_kb_role(None, "test-kb", kb_default_role="read")
        assert role == "read"

    def test_anonymous_user_private_kb(self, setup):
        auth, db, config, admin, user = setup
        role = auth.get_kb_role(None, "test-kb", kb_default_role="none")
        assert role is None

    def test_anonymous_user_no_default(self, setup):
        auth, db, config, admin, user = setup
        # No KB default → falls back to anonymous_tier from config
        role = auth.get_kb_role(None, "test-kb")
        assert role == "read"  # anonymous_tier in fixture


class TestGrantRevokePermissions:
    def test_grant_permission(self, setup):
        auth, db, config, admin, user = setup
        auth.grant_kb_permission(user["id"], "test-kb", "write", admin["id"])
        perms = auth.list_kb_permissions("test-kb")
        assert len(perms) == 1
        assert perms[0]["user_id"] == user["id"]
        assert perms[0]["role"] == "write"

    def test_grant_updates_existing(self, setup):
        auth, db, config, admin, user = setup
        auth.grant_kb_permission(user["id"], "test-kb", "read", admin["id"])
        auth.grant_kb_permission(user["id"], "test-kb", "write", admin["id"])
        perms = auth.list_kb_permissions("test-kb")
        assert len(perms) == 1
        assert perms[0]["role"] == "write"

    def test_revoke_permission(self, setup):
        auth, db, config, admin, user = setup
        auth.grant_kb_permission(user["id"], "test-kb", "write", admin["id"])
        ok = auth.revoke_kb_permission(user["id"], "test-kb")
        assert ok is True
        perms = auth.list_kb_permissions("test-kb")
        assert len(perms) == 0

    def test_revoke_nonexistent(self, setup):
        auth, db, config, admin, user = setup
        ok = auth.revoke_kb_permission(user["id"], "test-kb")
        assert ok is False

    def test_list_permissions_empty(self, setup):
        auth, db, config, admin, user = setup
        perms = auth.list_kb_permissions("nonexistent-kb")
        assert perms == []

    def test_invalid_role(self, setup):
        auth, db, config, admin, user = setup
        with pytest.raises(ValueError, match="Invalid role"):
            auth.grant_kb_permission(user["id"], "test-kb", "superadmin", admin["id"])

    def test_get_user_kb_permissions(self, setup):
        auth, db, config, admin, user = setup
        auth.grant_kb_permission(user["id"], "kb1", "read", admin["id"])
        auth.grant_kb_permission(user["id"], "kb2", "write", admin["id"])
        perms = auth.get_user_kb_permissions(user["id"])
        assert perms == {"kb1": "read", "kb2": "write"}


class TestEphemeralKBCreation:
    def test_create_ephemeral_kb(self, setup, tmpdir):
        auth, db, config, admin, user = setup
        # Give alice write role so she can create ephemeral KBs
        auth.set_role(user["id"], "write")

        from pyrite.services.ephemeral_service import EphemeralKBService

        eph_svc = EphemeralKBService(config, db)
        result = auth.create_user_ephemeral_kb(user["id"], eph_svc)
        assert result["ephemeral"] is True
        assert "name" in result

        # Verify admin grant was created
        perms = auth.list_kb_permissions(result["name"])
        assert len(perms) == 1
        assert perms[0]["user_id"] == user["id"]
        assert perms[0]["role"] == "admin"

    def test_ephemeral_kb_limit_exceeded(self, setup, tmpdir):
        auth, db, config, admin, user = setup
        auth.set_role(user["id"], "write")

        from pyrite.services.ephemeral_service import EphemeralKBService

        eph_svc = EphemeralKBService(config, db)

        # Create first (allowed)
        auth.create_user_ephemeral_kb(user["id"], eph_svc)

        # Second should fail (limit=1)
        with pytest.raises(ValueError, match="limit reached"):
            auth.create_user_ephemeral_kb(user["id"], eph_svc)

    def test_ephemeral_kb_insufficient_tier(self, setup, tmpdir):
        auth, db, config, admin, user = setup
        # alice has 'read' role, needs 'write' for ephemeral

        from pyrite.services.ephemeral_service import EphemeralKBService

        eph_svc = EphemeralKBService(config, db)

        with pytest.raises(ValueError, match="Insufficient tier"):
            auth.create_user_ephemeral_kb(user["id"], eph_svc)

    def test_admin_can_create_ephemeral(self, setup, tmpdir):
        auth, db, config, admin, user = setup

        from pyrite.services.ephemeral_service import EphemeralKBService

        eph_svc = EphemeralKBService(config, db)
        result = auth.create_user_ephemeral_kb(admin["id"], eph_svc)
        assert result["ephemeral"] is True


class TestEphemeralKBRemovalDropsGrants:
    """An ephemeral KB's per-KB grants go with it.

    `create_user_ephemeral_kb` records an admin grant for the creator, and an
    admin may grant others on it. Expiry and removal dropped the KB but left
    every `kb_permission` row behind, so a later KB registered under the same
    name -- ephemeral or not -- inherited them: the former grantees could read
    or administer a KB nobody had granted them.
    """

    def _make(self, setup):
        auth, db, config, admin, user = setup
        auth.set_role(user["id"], "write")
        from pyrite.services.ephemeral_service import EphemeralKBService

        eph_svc = EphemeralKBService(config, db)
        name = auth.create_user_ephemeral_kb(user["id"], eph_svc)["name"]
        auth.grant_kb_permission(admin["id"], name, "read", admin["id"])
        assert len(auth.list_kb_permissions(name)) == 2
        return auth, db, eph_svc, name

    def _grant_rows(self, db, name):
        return db.execute_sql("SELECT user_id FROM kb_permission WHERE kb_name = :kb", {"kb": name})

    def test_force_expire_deletes_the_grants(self, setup):
        auth, db, eph_svc, name = self._make(setup)
        assert eph_svc.force_expire_kb(name) is True
        assert self._grant_rows(db, name) == []

    def test_gc_of_an_expired_kb_deletes_the_grants(self, setup):
        auth, db, eph_svc, name = self._make(setup)
        kb = eph_svc.config.get_kb(name)
        kb.created_at_ts = kb.created_at_ts - kb.ttl - 1  # expired
        assert eph_svc.gc_ephemeral_kbs() == [name]
        assert self._grant_rows(db, name) == []

    def test_a_kb_later_registered_under_the_same_name_inherits_nothing(self, setup):
        auth, db, eph_svc, name = self._make(setup)
        user_id = auth.list_kb_permissions(name)[0]["user_id"]
        eph_svc.force_expire_kb(name)
        assert auth.get_kb_role(user_id, name, "none") is None

    def test_gc_leaves_a_live_kbs_grants_alone(self, setup):
        auth, db, eph_svc, name = self._make(setup)
        assert eph_svc.gc_ephemeral_kbs() == []
        assert len(self._grant_rows(db, name)) == 2


class TestAnyKBRemovalDropsGrants:
    """Every way a KB is deleted takes its per-KB grants with it, not only
    ephemeral expiry: a grant outliving its KB is inherited by the next KB
    registered under the same name."""

    def _grants(self, db, name):
        return db.execute_sql("SELECT user_id FROM kb_permission WHERE kb_name = :kb", {"kb": name})

    def test_registry_remove_deletes_the_grants(self, setup, tmpdir):
        """The path behind DELETE /api/kbs/{name}, MCP kb_registry_remove and
        `pyrite kb remove`."""
        auth, db, config, admin, user = setup
        from pyrite.services.kb_registry_service import KBRegistryService
        from pyrite.storage.index import IndexManager

        (tmpdir / "user-kb").mkdir()
        registry = KBRegistryService(config, db, IndexManager(db, config))
        registry.add_kb("user-kb", str(tmpdir / "user-kb"))
        auth.grant_kb_permission(user["id"], "user-kb", "admin", admin["id"])

        registry.remove_kb("user-kb")

        assert self._grants(db, "user-kb") == []
        assert auth.get_kb_role(user["id"], "user-kb", "none") is None

    def test_repo_unsubscribe_deletes_the_grants_of_its_kbs(self, setup, tmpdir):
        auth, db, config, admin, user = setup
        from unittest.mock import patch

        from pyrite.services.repo_service import RepoService

        repo = db.register_repo("owner/repo", str(tmpdir / "clone"))
        db.register_kb("repo-kb", "generic", str(tmpdir / "clone"))
        db.link_kb_to_repo("repo-kb", repo["id"], "")
        auth.grant_kb_permission(user["id"], "repo-kb", "write", admin["id"])

        svc = RepoService(config, db)
        with patch.object(svc.user_service, "get_current_user", return_value=admin):
            assert svc.unsubscribe("owner/repo")["success"] is True

        assert self._grants(db, "repo-kb") == []

    def test_grants_on_other_kbs_are_kept(self, setup, tmpdir):
        auth, db, config, admin, user = setup
        db.register_kb("doomed", "generic", str(tmpdir))
        auth.grant_kb_permission(user["id"], "doomed", "read", admin["id"])
        auth.grant_kb_permission(user["id"], "test-kb", "write", admin["id"])

        db.unregister_kb("doomed")

        assert auth.get_user_kb_permissions(user["id"]) == {"test-kb": "write"}
