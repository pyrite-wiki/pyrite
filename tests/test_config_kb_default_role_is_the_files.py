"""A config KB's `default_role` is exactly what `config.yaml` says, validated once.

Properties:

- **One validator.** A KB's `default_role` is None (absent: the instance
  default), "none", "read" or "write", from every source: the config file,
  the file the environment names, direct `KBConfig` construction,
  `PyriteConfig.from_dict`, the index's registry row, the registry service
  and the REST admin endpoint. Anything else is refused where it enters,
  never carried through as a role (`access_policy.kb_role` step 3 hands a
  KB's default_role to every user with global access as their role).
- **The file is the source.** For a KB declared in config.yaml the effective
  role is the file's value; a removed line means the instance default, never
  the value an earlier load stored in the registry row.
- **Fail closed.** A KB the load skips (its path cannot be resolved, its row
  holds a value the validator refuses) resolves to "none" in the role check
  itself, not only through each route's existence check.

Every test runs with HOME, PYRITE_CONFIG_DIR and PYRITE_DATA_DIR in tmp_path.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig
from pyrite.exceptions import ConfigError, ValidationError
from pyrite.services.access_policy import AccessPolicy, Principal
from pyrite.services.auth_service import AuthService
from pyrite.storage.database import PyriteDB

KB = "doc-kb"
ENTRY = "doc-1"
KEY = "operator-key"
ADMIN = {"X-API-Key": KEY}

#: Values no source may set. "admin" would make every user with global access
#: an admin of the KB; the rest are a typo, a wrong case, an empty string and
#: YAML's spellings of a boolean or a number.
INVALID = ["admin", "Read", "public", "", True, 0]


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """Throwaway HOME, config and data dirs; the data dir holds config.yaml
    (PYRITE_DATA_DIR wins over PYRITE_CONFIG_DIR when both are set)."""
    home, data = tmp_path / "home", tmp_path / "data"
    home.mkdir()
    data.mkdir()
    (tmp_path / KB).mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PYRITE_CONFIG_DIR", str(data))
    monkeypatch.setenv("PYRITE_DATA_DIR", str(data))
    # The suite pins the config file (conftest `_isolate_global_config`); pin
    # it to this world's.
    import pyrite.config as config_module

    monkeypatch.setattr(config_module, "CONFIG_DIR", data)
    monkeypatch.setattr(config_module, "CONFIG_FILE", data / "config.yaml")
    return tmp_path


def _write_config(root: Path, role_line: str, *, anonymous_tier: str | None = "read") -> None:
    tier = f"\n    anonymous_tier: {anonymous_tier}" if anonymous_tier else ""
    (root / "data" / "config.yaml").write_text(
        f"""knowledge_bases:
  - name: {KB}
    path: {root / KB}
{role_line}
settings:
  api_key: {KEY}
  allowed_hosts: [testserver]
  auth:
    enabled: true
    allow_registration: true{tier}
"""
    )


def _load():
    from pyrite.config import load_config

    return load_config()


# =============================================================================
# One validator, every source
# =============================================================================


class TestEverySourceRefusesAnInvalidRole:
    @pytest.mark.parametrize("value", ["admin", "Read", "''", "true", "0"])
    def test_the_config_file_refuses_it_at_load(self, dirs, value):
        _write_config(dirs, f"    default_role: {value}")
        with pytest.raises(ConfigError, match=rf"KB '{KB}'.*default_role"):
            _load()

    def test_the_file_the_environment_names_refuses_it_at_load(self, dirs, monkeypatch):
        # No environment variable carries a KB's role; the environment's
        # source is the file PYRITE_CONFIG_DIR names, resolved at the call
        # (CONFIG_DIR left at ~/.pyrite, and without PYRITE_DATA_DIR, which
        # would take precedence).
        import pyrite.config as config_module

        home_dir = Path(os.environ["HOME"]) / ".pyrite"
        monkeypatch.setattr(config_module, "CONFIG_DIR", home_dir.resolve())
        monkeypatch.setattr(config_module, "CONFIG_FILE", home_dir.resolve() / "config.yaml")
        monkeypatch.delenv("PYRITE_DATA_DIR")
        _write_config(dirs, "    default_role: admin")
        assert config_module.current_config_file() == dirs / "data" / "config.yaml"
        with pytest.raises(ConfigError, match=rf"KB '{KB}'.*default_role"):
            _load()

    @pytest.mark.parametrize("value", INVALID)
    def test_direct_construction_refuses_it(self, tmp_path, value):
        with pytest.raises(ConfigError, match="default_role"):
            KBConfig(name=KB, path=tmp_path / KB, default_role=value)

    @pytest.mark.parametrize("value", INVALID)
    def test_from_dict_refuses_it(self, tmp_path, value):
        data = {"knowledge_bases": [{"name": KB, "path": str(tmp_path), "default_role": value}]}
        with pytest.raises(ConfigError, match="default_role"):
            PyriteConfig.from_dict(data)

    @pytest.mark.parametrize("value", [None, "none", "read", "write"])
    @pytest.mark.control(reason="the valid values load unchanged, before and after the validator")
    def test_every_valid_value_is_kept(self, tmp_path, value):
        assert KBConfig(name=KB, path=tmp_path, default_role=value).default_role == value

    def test_the_registry_service_refuses_it(self, dirs):
        config = PyriteConfig(settings=_settings(dirs))
        with PyriteDB(config.settings.index_path) as db:
            db.register_kb(KB, "generic", str(dirs / KB), source="user")
            from pyrite.services.kb_registry_service import KBRegistryService

            registry = KBRegistryService(config, db)
            with pytest.raises(ValidationError, match="default_role"):
                registry.update_kb(KB, default_role="admin")
            assert registry.registered_default_role(KB) is None

    @pytest.mark.parametrize("value", ["admin", "Read", "", "public"])
    def test_the_storage_write_refuses_it(self, dirs, value):
        """`update_kb_default_role` is the storage layer's own write: it goes
        through the validator too, so nothing stores a value reads refuse."""
        with PyriteDB(dirs / "data" / "index.db") as db:
            db.register_kb(KB, "generic", str(dirs / KB), source="user")
            with pytest.raises(ConfigError, match="default_role"):
                db.update_kb_default_role(KB, value)
            rows = db.execute_sql("SELECT default_role FROM kb WHERE name = :n", {"n": KB})
            assert rows[0]["default_role"] is None

    @pytest.mark.parametrize("value", ["admin", "Read", "", "public"])
    @pytest.mark.control(
        reason="the endpoint checked inline before; this pins that it now asks the "
        "shared validator, and fails when validate_kb_default_role accepts these"
    )
    def test_the_rest_admin_endpoint_refuses_it(self, rest_world, value):
        client = rest_world["client"]
        r = client.put(f"/api/kbs/{USER_KB}/default-role", json={"role": value}, headers=ADMIN)
        assert r.status_code == 400, r.text
        assert "role must be" in r.text
        reg = rest_world["registry"]
        assert reg.registered_default_role(USER_KB) == "read"

    @pytest.mark.parametrize("value", ["admin", "Read", "public"])
    def test_an_invalid_registry_row_gives_no_access(self, dirs, value):
        """A row an older version or another tool wrote: never a role. The
        KB is left out of the load (fail closed), and the role check itself
        answers "none" for it."""
        config = PyriteConfig(settings=_settings(dirs, anonymous_tier="read"))
        with PyriteDB(config.settings.index_path) as db:
            db.register_kb(KB, "generic", str(dirs / KB), source="user")
            db.execute_write_sql(
                "UPDATE kb SET default_role = :r WHERE name = :n", {"r": value, "n": KB}
            )
            db.merge_registered_kbs(config)
            assert config.get_kb(KB) is None
            policy = AccessPolicy(config, db)
            assert policy.kb_default_role(KB) == "none"
            vetted = AuthService(db, config.settings.auth).create_user(
                "vetted", "password123", role="read"
            )
            assert policy.effective_kb_role(Principal.user(vetted["id"], "read"), KB) is None
            assert policy.effective_kb_role(Principal("anonymous", "read"), KB) is None

    def test_a_row_made_invalid_after_the_load_gives_no_access(self, dirs):
        """The resolver reads a loaded registry KB's row when the load had no
        value; that read goes through the validator too."""
        config = PyriteConfig(settings=_settings(dirs, anonymous_tier="read"))
        with PyriteDB(config.settings.index_path) as db:
            db.register_kb(KB, "generic", str(dirs / KB), source="user")
            db.merge_registered_kbs(config)
            assert config.get_kb(KB) is not None
            db.execute_write_sql("UPDATE kb SET default_role = 'admin' WHERE name = :n", {"n": KB})
            policy = AccessPolicy(config, db)
            assert policy.kb_default_role(KB) == "none"
            vetted = AuthService(db, config.settings.auth).create_user(
                "vetted", "password123", role="read"
            )
            assert policy.effective_kb_role(Principal.user(vetted["id"], "read"), KB) is None


# =============================================================================
# The file is the source: a removed line is the instance default
# =============================================================================


def _settings(root: Path, anonymous_tier: str | None = "read"):
    from pyrite.config import AuthConfig, Settings

    return Settings(
        index_path=root / "data" / "index.db",
        api_key=KEY,
        allowed_hosts=["testserver"],
        auth=AuthConfig(enabled=True, allow_registration=True, anonymous_tier=anonymous_tier),
    )


def _boot(*, seed_entry: bool = False):
    """Load config.yaml and build the app, as a server restart does."""
    pytest.importorskip("fastapi", reason="fastapi not installed")
    from fastapi.testclient import TestClient

    from pyrite.server.api import create_app
    from tests.auth_seed import app_db

    app = create_app(config=_load())
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    db = app_db(app)
    if seed_entry:
        db.upsert_entry(
            {
                "id": ENTRY,
                "kb_name": KB,
                "entry_type": "note",
                "title": "t",
                "body": "b",
                "summary": "",
                "tags": [],
                "sources": [],
                "links": [],
                "metadata": {},
            }
        )
    return app, client, db


def _read(client, cookie: str | None = None):
    client.cookies.clear()
    if cookie:
        client.cookies.set("pyrite_session", cookie)
    return client.get(f"/api/entries/{ENTRY}", params={"kb": KB})


class TestARemovedLineIsTheInstanceDefault:
    def _restart_without_the_line(self, dirs, first: str = "read"):
        """`default_role: <first>` in the file, a self-registered user, then the
        line removed and the server restarted."""
        from tests.auth_seed import sign_in

        _write_config(dirs, f"    default_role: {first}")
        app, client, db = _boot(seed_entry=True)
        auth = AuthService(db, app.state.pyrite_config.settings.auth)
        auth.create_user("root", "password123", role="admin")
        auth.register("selfreg", "password123")
        before = _read(client, sign_in(app, "selfreg")).status_code
        db.close()

        _write_config(dirs, "")
        app, client, db = _boot()
        return app, client, db, before

    def test_a_self_registered_user_loses_the_kb_after_restart(self, dirs):
        """The access the line gave is gone with the line: a self-registered
        user reads a KB only through its default_role, and the instance
        default for one gives them nothing (`kb_role` step 4)."""
        from tests.auth_seed import sign_in

        app, client, db, before = self._restart_without_the_line(dirs)
        try:
            assert before == 200
            r = _read(client, sign_in(app, "selfreg"))
            assert r.status_code == 404, r.text
        finally:
            db.close()

    def test_the_seed_stores_the_files_value(self, dirs):
        """The registry row follows the file at every seed, a removed line
        included, so nothing that reads the row sees a stale role."""
        from pyrite.services.kb_registry_service import KBRegistryService

        app, client, db, _ = self._restart_without_the_line(dirs)
        try:
            config = app.state.pyrite_config
            assert KBRegistryService(config, db).registered_default_role(KB) is None
        finally:
            db.close()

    @pytest.mark.control(
        reason="a removed `read` was always the instance default for anonymous; "
        "the stored-`none` case is TestAnUpgradeNeverOpensAClosedKB"
    )
    def test_anonymous_gets_the_instance_default(self, dirs):
        """Absent means the instance default when no `none` is stored: a removed
        `default_role: read` leaves anonymous at `anonymous_tier` (read here)."""
        app, client, db, _ = self._restart_without_the_line(dirs, first="read")
        try:
            assert _read(client).status_code == 200
        finally:
            db.close()

    def test_the_policy_never_falls_back_to_a_stale_row(self, dirs):
        """The role check reads the file's value for a config KB even when the
        row disagrees (a seed that failed, or a row from an older version)."""
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name=KB, path=dirs / KB)],
            settings=_settings(dirs, anonymous_tier="read"),
        )
        with PyriteDB(config.settings.index_path) as db:
            db.register_kb(KB, "generic", str(dirs / KB), source="config")
            db.execute_write_sql("UPDATE kb SET default_role = 'read' WHERE name = :n", {"n": KB})
            policy = AccessPolicy(config, db)
            assert policy.kb_default_role(KB) is None
            auth = AuthService(db, config.settings.auth)
            auth.create_user("root", "password123", role="admin")
            selfreg = auth.register("selfreg", "password123")
            assert policy.effective_kb_role(Principal.user(selfreg["id"], "read"), KB) is None


# =============================================================================
# An upgrade never opens a KB that was closed (maintainer decision D1 = b)
# =============================================================================


def _kept_closed_lines(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING and KB in r.getMessage() and "keep" in r.getMessage()
    ]


class TestAnUpgradeNeverOpensAClosedKB:
    """A config.yaml KB with no `default_role` line over a stored `none` stays
    closed until config.yaml itself sets a `default_role`; the seed keeps the
    stored `none` and says so once, naming the line to add."""

    def _config_over_a_stored_none(self, dirs, db):
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name=KB, path=dirs / KB)],
            settings=_settings(dirs, anonymous_tier="read"),
        )
        # What an earlier version left: the row of a config KB, closed.
        db.register_kb(KB, "generic", str(dirs / KB), source="config", default_role="none")
        return config

    def test_the_seed_keeps_the_stored_none_and_says_so_once(self, dirs, caplog):
        from pyrite.services.kb_registry_service import KBRegistryService

        with PyriteDB(dirs / "data" / "index.db") as db:
            config = self._config_over_a_stored_none(dirs, db)
            registry = KBRegistryService(config, db)
            with caplog.at_level(logging.WARNING):
                registry.seed_from_config()
                registry.seed_from_config()  # a second seed in the same process
            assert registry.registered_default_role(KB) == "none"
            policy = AccessPolicy(config, db)
            assert policy.kb_default_role(KB) == "none"
            assert policy.effective_kb_role(Principal.anonymous("read"), KB) is None
            # The operator's file is not rewritten in memory either.
            assert config.yaml_kb(KB).default_role is None
        lines = _kept_closed_lines(caplog)
        assert len(lines) == 1, lines
        assert "default_role: none" in lines[0]
        assert "config.yaml" in lines[0]

    @pytest.mark.control(
        reason="dev's fallback to the row also kept it closed; red on 5e7aeac2, which "
        "read the file only, so it guards the D1 rule in the policy itself"
    )
    def test_the_role_check_keeps_it_closed_without_a_seed(self, dirs):
        """A seed that failed (create_app logs and goes on) must not open it."""
        with PyriteDB(dirs / "data" / "index.db") as db:
            config = self._config_over_a_stored_none(dirs, db)
            policy = AccessPolicy(config, db)
            assert policy.kb_default_role(KB) == "none"
            assert policy.effective_kb_role(Principal.anonymous("read"), KB) is None

    def test_it_stays_closed_over_rest_until_the_file_sets_a_role(self, dirs, caplog):
        _write_config(dirs, "    default_role: none")
        app, client, db = _boot(seed_entry=True)
        assert _read(client).status_code == 404
        db.close()

        _write_config(dirs, "")  # the upgrade: the line is gone, the row says none
        with caplog.at_level(logging.WARNING):
            app, client, db = _boot()
        try:
            assert _read(client).status_code == 404
        finally:
            db.close()
        assert len(_kept_closed_lines(caplog)) == 1

        _write_config(dirs, "    default_role: read")  # the file decides
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            app, client, db = _boot()
        try:
            assert _read(client).status_code == 200
        finally:
            db.close()
        assert _kept_closed_lines(caplog) == []

    @pytest.mark.parametrize("stored", [None, "read", "write"])
    @pytest.mark.control(reason="only a stored `none` is kept; anything else is the file's")
    def test_only_a_stored_none_is_kept(self, dirs, stored, caplog):
        from pyrite.services.kb_registry_service import KBRegistryService

        config = PyriteConfig(
            knowledge_bases=[KBConfig(name=KB, path=dirs / KB)],
            settings=_settings(dirs, anonymous_tier="read"),
        )
        with PyriteDB(config.settings.index_path) as db:
            db.register_kb(KB, "generic", str(dirs / KB), source="config", default_role=stored)
            registry = KBRegistryService(config, db)
            with caplog.at_level(logging.WARNING):
                registry.seed_from_config()
            assert registry.registered_default_role(KB) is None
            assert AccessPolicy(config, db).kb_default_role(KB) is None
        assert _kept_closed_lines(caplog) == []


# =============================================================================
# A KB the load skips resolves to no access
# =============================================================================


def _loop(root: Path) -> Path:
    """A path that cannot be resolved: a symlink loop."""
    a, b = root / "loop-a", root / "loop-b"
    a.symlink_to(b)
    b.symlink_to(a)
    return a


class TestASkippedKBHasNoAccess:
    @pytest.mark.parametrize("path", ["unknown-user", "loop"])
    @pytest.mark.parametrize("stored", [None, "read", "write"])
    def test_the_role_check_answers_none(self, dirs, stored, path):
        config = PyriteConfig(settings=_settings(dirs, anonymous_tier="read"))
        where = "~no-such-user-t7/kb" if path == "unknown-user" else str(_loop(dirs))
        with PyriteDB(config.settings.index_path) as db:
            db.register_kb(KB, "generic", where, source="user", default_role=stored)
            db.merge_registered_kbs(config)
            assert config.get_kb(KB) is None  # skipped at load
            policy = AccessPolicy(config, db)
            assert policy.kb_default_role(KB) == "none"
            auth = AuthService(db, config.settings.auth)
            auth.create_user("root", "password123", role="admin")
            vetted = auth.create_user("vetted", "password123", role="write")
            selfreg = auth.register("selfreg", "password123")
            for principal in (
                Principal("anonymous", "read"),
                Principal.user(vetted["id"], "write"),
                Principal.user(selfreg["id"], "read"),
            ):
                assert policy.effective_kb_role(principal, KB) is None, principal

    @pytest.mark.parametrize(
        "path",
        [
            "unknown-user",
            pytest.param(
                "loop",
                marks=pytest.mark.control(
                    reason="a symlink loop never reached the second warning on 3.13"
                ),
            ),
        ],
    )
    def test_the_load_says_one_thing_about_it(self, dirs, caplog, path):
        """One accurate warning: not "loading it private" and then "not loading it"."""
        config = PyriteConfig(settings=_settings(dirs))
        where = "~no-such-user-t7/kb" if path == "unknown-user" else str(_loop(dirs))
        with PyriteDB(config.settings.index_path) as db:
            db.register_kb(KB, "generic", where, source="user")
            with caplog.at_level(logging.WARNING):
                db.merge_registered_kbs(config)
        about = [r.getMessage() for r in caplog.records if KB in r.getMessage()]
        assert len(about) == 1, about
        assert "not loading it" in about[0]


# =============================================================================
# REST world for the admin endpoint
# =============================================================================

USER_KB = "open-kb"


@pytest.fixture
def rest_world(dirs):
    pytest.importorskip("fastapi", reason="fastapi not installed")
    from fastapi.testclient import TestClient

    from pyrite.server.api import create_app
    from pyrite.services.kb_registry_service import KBRegistryService
    from tests.auth_seed import app_db

    (dirs / USER_KB).mkdir()
    config = PyriteConfig(settings=_settings(dirs))
    with PyriteDB(config.settings.index_path) as db:
        db.register_kb(USER_KB, "generic", str(dirs / USER_KB), source="user", default_role="read")
    app = create_app(config=config)
    client = TestClient(app)
    client.get("/health")
    yield {"client": client, "registry": KBRegistryService(config, app_db(app))}
    state_db = getattr(app.state, "pyrite_db", None)
    if state_db is not None:
        state_db.close()
