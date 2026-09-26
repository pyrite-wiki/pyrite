"""Tests for Phase 2: Per-KB Access Control.

Covers:
- Migration v13 (default_role column on kb)
- register_kb with default_role
- update_kb_default_role
- seed_from_config syncs default_role
- AuthService.list_users
- KBRegistryService.update_kb with default_role
- Ephemeral KB listing and force-expire
"""

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.services.auth_service import AuthService
from pyrite.services.kb_registry_service import KBRegistryService
from pyrite.storage.database import PyriteDB
from pyrite.storage.migrations import CURRENT_VERSION, MIGRATIONS, MigrationManager
from tests.auth_seed import seed_user


# =========================================================================
# Migration Tests
# =========================================================================


class TestMigrationV13:
    """Test that v13 migration adds default_role column to kb."""

    def test_current_version_includes_access_control_migration(self):
        """CURRENT_VERSION is at least 13 (default_role migration)."""
        assert CURRENT_VERSION >= 13

    def test_migration_v13_exists(self):
        """Migration v13 for default_role exists."""
        v13 = [m for m in MIGRATIONS if m.version == 13]
        assert len(v13) == 1
        assert "default_role" in v13[0].description.lower()

    def test_migration_adds_default_role_column(self, tmp_path):
        """v13 migration adds default_role column to kb."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))

        # Create kb table as it would exist before v13
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kb (
                name TEXT PRIMARY KEY,
                kb_type TEXT NOT NULL,
                path TEXT NOT NULL,
                description TEXT,
                last_indexed TEXT,
                entry_count INTEGER DEFAULT 0,
                source TEXT DEFAULT 'user',
                repo_id INTEGER,
                repo_subpath TEXT DEFAULT ''
            )
        """)
        conn.commit()

        mgr = MigrationManager(conn)

        # Record that v1-v12 have been applied
        from datetime import UTC, datetime

        for v in range(1, 13):
            conn.execute(
                "INSERT INTO schema_version (version, description, applied_at) VALUES (?, ?, ?)",
                (v, f"Migration v{v}", datetime.now(UTC).isoformat()),
            )
        conn.commit()

        applied = mgr.migrate()
        assert any(m.version == 13 for m in applied)

        # Check that default_role column exists
        columns = {row[1] for row in conn.execute("PRAGMA table_info(kb)").fetchall()}
        assert "default_role" in columns

        # Check that NULL is the default (no default_role set)
        conn.execute(
            "INSERT INTO kb (name, kb_type, path) VALUES ('test-kb', 'generic', '/tmp/test')"
        )
        row = conn.execute("SELECT default_role FROM kb WHERE name = 'test-kb'").fetchone()
        assert row[0] is None

        conn.close()


# =========================================================================
# KB Ops Tests
# =========================================================================


class TestKBDefaultRole:
    """Tests for default_role on KB registration and updates."""

    @pytest.fixture
    def db(self, tmp_path):
        db_path = tmp_path / "test.db"
        db = PyriteDB(str(db_path))
        yield db
        db.close()

    def test_register_kb_with_default_role(self, db):
        """register_kb persists default_role."""
        db.register_kb("test-kb", "generic", "/tmp/test", default_role="read")
        row = db._raw_conn.execute("SELECT default_role FROM kb WHERE name = 'test-kb'").fetchone()
        assert row[0] == "read"

    def test_register_kb_no_default_role(self, db):
        """register_kb without default_role leaves it NULL."""
        db.register_kb("test-kb", "generic", "/tmp/test")
        row = db._raw_conn.execute("SELECT default_role FROM kb WHERE name = 'test-kb'").fetchone()
        assert row[0] is None

    def test_update_kb_default_role(self, db):
        """update_kb_default_role changes the value."""
        db.register_kb("test-kb", "generic", "/tmp/test")
        assert db.update_kb_default_role("test-kb", "write") is True
        row = db._raw_conn.execute("SELECT default_role FROM kb WHERE name = 'test-kb'").fetchone()
        assert row[0] == "write"

    def test_update_kb_default_role_to_none(self, db):
        """update_kb_default_role can set to None."""
        db.register_kb("test-kb", "generic", "/tmp/test", default_role="read")
        db.update_kb_default_role("test-kb", None)
        row = db._raw_conn.execute("SELECT default_role FROM kb WHERE name = 'test-kb'").fetchone()
        assert row[0] is None

    def test_update_kb_default_role_not_found(self, db):
        """update_kb_default_role returns False for missing KB."""
        assert db.update_kb_default_role("nonexistent", "read") is False


# =========================================================================
# Registry Service Tests
# =========================================================================


class TestRegistryDefaultRole:
    """Tests for KBRegistryService handling of default_role."""

    @pytest.fixture
    def setup(self, tmp_path):
        db_path = tmp_path / "test.db"
        db = PyriteDB(str(db_path))

        kb_dir = tmp_path / "test-kb"
        kb_dir.mkdir()

        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(
                    name="config-kb",
                    path=kb_dir,
                    kb_type="generic",
                    default_role="read",
                ),
            ],
            settings=Settings(index_path=db_path),
        )

        index_mgr = MagicMock()
        registry = KBRegistryService(config, db, index_mgr)
        return registry, db, config

    def test_seed_syncs_default_role(self, setup):
        """seed_from_config persists default_role from config."""
        registry, db, _ = setup
        registry.seed_from_config()
        row = db._raw_conn.execute(
            "SELECT default_role FROM kb WHERE name = 'config-kb'"
        ).fetchone()
        assert row[0] == "read"

    def test_list_kbs_includes_default_role(self, setup):
        """list_kbs includes default_role in response."""
        registry, _, _ = setup
        registry.seed_from_config()
        kbs = registry.list_kbs()
        assert len(kbs) == 1
        assert kbs[0]["default_role"] == "read"

    def test_get_kb_includes_default_role(self, setup):
        """get_kb includes default_role in response."""
        registry, _, _ = setup
        registry.seed_from_config()
        kb = registry.get_kb("config-kb")
        assert kb is not None
        assert kb["default_role"] == "read"

    def test_update_kb_default_role(self, setup):
        """update_kb with default_role updates the value (a registry KB)."""
        registry, db, _ = setup
        db.register_kb("user-kb", "generic", "/tmp/user", source="user", default_role="read")
        result = registry.update_kb("user-kb", default_role="none")
        assert result["default_role"] == "none"

    def test_update_kb_default_role_to_null(self, setup):
        """update_kb can set default_role to None (a registry KB)."""
        registry, db, _ = setup
        db.register_kb("user-kb", "generic", "/tmp/user", source="user", default_role="read")
        result = registry.update_kb("user-kb", default_role=None)
        assert result["default_role"] is None

    def test_update_kb_default_role_of_a_config_kb_is_refused(self, setup):
        """config.yaml sets a config KB's policy: a registry write would change
        nothing, so it is refused, not reported as done (P-M1)."""
        from pyrite.exceptions import KBDefinedInConfigError

        registry, _, config = setup
        registry.seed_from_config()
        with pytest.raises(KBDefinedInConfigError):
            registry.update_kb("config-kb", default_role="none")
        assert registry.get_kb("config-kb")["default_role"] == "read"
        assert config.get_kb("config-kb").default_role == "read"

    def test_get_kb_config_includes_default_role(self, setup):
        """get_kb_config builds KBConfig with default_role from DB."""
        registry, db, _ = setup
        # Register a user KB directly with a default_role
        db.register_kb("user-kb", "generic", "/tmp/user", source="user", default_role="write")
        cfg = registry.get_kb_config("user-kb")
        assert cfg is not None
        assert cfg.default_role == "write"


# =========================================================================
# AuthService.list_users Tests
# =========================================================================


class TestListUsers:
    """Tests for AuthService.list_users."""

    @pytest.fixture
    def setup(self, tmp_path):
        db_path = tmp_path / "test.db"
        db = PyriteDB(str(db_path))
        auth = AuthService(db, AuthConfig(enabled=True, allow_registration=True))
        return auth, db

    def test_list_users_returns_all(self, setup):
        """list_users returns all local users."""
        auth, db = setup
        # Seed two users via the operator path (the first is admin).
        seed_user(db, "alice", "password123", display_name="Alice")
        seed_user(db, "bob", "password456", display_name="Bob")
        users = auth.list_users()
        usernames = {u["username"] for u in users}
        assert "alice" in usernames
        assert "bob" in usernames

    def test_list_users_excludes_password(self, setup):
        """list_users does not include password_hash."""
        auth, db = setup
        seed_user(db, "alice", "secret12345", display_name="Alice")
        users = auth.list_users()
        for u in users:
            assert "password_hash" not in u

    def test_list_users_includes_expected_fields(self, setup):
        """list_users returns expected field set."""
        auth, db = setup
        seed_user(db, "alice", "secret12345", display_name="Alice")
        users = auth.list_users()
        alice = next(u for u in users if u["username"] == "alice")
        assert "id" in alice
        assert "display_name" in alice
        assert "role" in alice

    def test_set_role_changes_user_role(self, setup):
        """set_role updates a user's global role."""
        auth, db = setup
        user = seed_user(db, "alice", "password123", display_name="Alice")
        user_id = user["id"]
        assert auth.set_role(user_id, "admin") is True
        users = auth.list_users()
        alice = next(u for u in users if u["id"] == user_id)
        assert alice["role"] == "admin"

    def test_set_role_rejects_invalid_role(self, setup):
        """set_role raises ValueError for invalid roles."""
        auth, db = setup
        user = seed_user(db, "alice", "password123", display_name="Alice")
        with pytest.raises(ValueError, match="Invalid role"):
            auth.set_role(user["id"], "superadmin")

    def test_get_user_kb_permissions(self, setup):
        """get_user_kb_permissions returns explicit grants."""
        auth, db = setup
        user = seed_user(db, "alice", "password123", display_name="Alice")
        auth.grant_kb_permission(user["id"], "test", "write", user["id"])
        perms = auth.get_user_kb_permissions(user["id"])
        assert perms == {"test": "write"}


# =========================================================================
# Ephemeral KB Tests
# =========================================================================


class TestEphemeralKBAdmin:
    """Tests for ephemeral KB listing and force-expire."""

    @pytest.fixture
    def setup(self, tmp_path):
        from pyrite.services.ephemeral_service import EphemeralKBService

        db_path = tmp_path / "test.db"
        db = PyriteDB(str(db_path))

        config = PyriteConfig(
            settings=Settings(
                index_path=db_path,
                workspace_path=tmp_path / "workspace",
            ),
        )
        (tmp_path / "workspace").mkdir(exist_ok=True)

        svc = EphemeralKBService(config, db)
        return svc, config, db

    def test_list_ephemeral_empty(self, setup):
        """list_ephemeral_kbs returns empty when none exist."""
        svc, _, _ = setup
        assert svc.list_ephemeral_kbs() == []

    def test_list_ephemeral_with_kbs(self, setup):
        """list_ephemeral_kbs returns created ephemeral KBs."""
        svc, _, _ = setup
        with patch("pyrite.services.ephemeral_service.save_config"):
            svc.create_ephemeral_kb("test-eph", ttl=3600)
        kbs = svc.list_ephemeral_kbs()
        assert len(kbs) == 1
        assert kbs[0]["name"] == "test-eph"
        assert kbs[0]["ttl"] == 3600
        assert kbs[0]["expired"] is False

    def test_force_expire_removes_kb(self, setup):
        """force_expire_kb removes an ephemeral KB."""
        svc, config, _ = setup
        with patch("pyrite.services.ephemeral_service.save_config"):
            svc.create_ephemeral_kb("test-eph", ttl=3600)
        assert len(svc.list_ephemeral_kbs()) == 1

        with patch("pyrite.services.ephemeral_service.save_config"):
            result = svc.force_expire_kb("test-eph")
        assert result is True
        assert svc.list_ephemeral_kbs() == []

    def test_force_expire_non_ephemeral_fails(self, setup):
        """force_expire_kb returns False for non-ephemeral KB."""
        svc, config, db = setup
        # Register a normal KB
        config.add_kb(KBConfig(name="normal-kb", path=Path("/tmp/normal"), kb_type="generic"))
        result = svc.force_expire_kb("normal-kb")
        assert result is False

    def test_force_expire_nonexistent_fails(self, setup):
        """force_expire_kb returns False for nonexistent KB."""
        svc, _, _ = setup
        assert svc.force_expire_kb("no-such-kb") is False
