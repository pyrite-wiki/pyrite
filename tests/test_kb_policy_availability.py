"""The default_role fix never costs availability.

The access properties (tests/test_default_role_change_takes_effect.py) must
not turn into a blocked admin:

- an admin can change the role of any KB the server manages itself (a
  registry KB, an ephemeral one, a repo-subscribed one); only a KB an
  operator wrote by hand into config.yaml is refused, naming that file;
- a registry update never leaves a window where the KB is missing, nor
  mutates the lookup `all_kbs()` iterates.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")
from fastapi.testclient import TestClient

from pyrite import config as config_module
from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app
from pyrite.services.kb_registry_service import KBRegistryService
from pyrite.storage.database import PyriteDB
from tests.auth_seed import app_db

KEY = "operator-key"
ADMIN = {"X-API-Key": KEY}
YAML_KB, REPO_KB, EPH_KB = "yaml-kb", "repo-kb", "eph-kb"


def _anonymous_reads(config, name: str, anonymous_tier: str = "read") -> bool:
    """What the access policy gives an anonymous visitor on `name`: the one
    rule (`kb_role`, step 5) every anonymous surface resolves through."""
    from pyrite.services.access_policy import kb_role

    kb = config.get_kb(name)
    if kb is None:
        return False
    return kb_role(None, None, lambda: None, kb.default_role, anonymous_tier) is not None


def _entry(eid: str, kb: str, title: str = "") -> dict:
    return {
        "id": eid,
        "kb_name": kb,
        "entry_type": "note",
        "title": title or eid,
        "body": f"body of {eid}",
        "summary": "",
        "tags": [],
        "sources": [],
        "links": [],
        "metadata": {},
    }


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("PYRITE_DATA_DIR", str(tmp_path))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in (YAML_KB, REPO_KB):
        (tmp_path / name).mkdir()
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name=YAML_KB, path=tmp_path / YAML_KB, default_role="read"),
            KBConfig(name=REPO_KB, path=tmp_path / REPO_KB, default_role="read", repo="org/x"),
        ],
        settings=Settings(
            index_path=tmp_path / "index.db",
            workspace_path=workspace,
            api_key=KEY,
            auth=AuthConfig(enabled=True, allow_registration=True, anonymous_tier="read"),
        ),
    )
    config_module.save_config(config)  # the server's own config.yaml, in the test dir
    app = create_app(config=config)
    client = TestClient(app)
    client.get("/health")
    # What `pyrite repo subscribe` leaves behind: the index's repository
    # record, and the KB's row linked to it -- evidence only the server writes.
    db = app_db(app)
    repo = db.register_repo("org/x", str(tmp_path / REPO_KB))
    db.execute_write_sql(
        "UPDATE kb SET repo_id = :r WHERE name = :n", {"r": repo["id"], "n": REPO_KB}
    )
    yield {"app": app, "client": client, "config": config, "tmp": tmp_path, "repo": repo}
    state_db = getattr(app.state, "pyrite_db", None)
    if state_db is not None:
        state_db.close()


def _set_role(client, kb, role):
    return client.put(f"/api/kbs/{kb}/default-role", json={"role": role}, headers=ADMIN)


class TestServerManagedKBsAreEditable:
    def test_a_hand_written_config_kb_is_refused_naming_the_file(self, world):
        r = _set_role(world["client"], YAML_KB, "none")
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "KB_DEFINED_IN_CONFIG"
        assert "config.yaml" in detail["message"]
        assert str(config_module.current_config_file().parent) not in detail["message"]

    def test_a_hand_written_repo_key_is_the_operators_and_the_file_is_untouched(
        self, world, tmp_path
    ):
        """`repo:` is a key anyone can type; without the index's repository
        record behind it the entry is the operator's, and their file is
        never re-serialised."""
        config_file = config_module.current_config_file()
        data = config_module.load_yaml_file(config_file)
        (tmp_path / "typed").mkdir()
        data["knowledge_bases"].append(
            {"name": "typed", "path": str(tmp_path / "typed"), "repo": "org/x"}
        )
        config_module.dump_yaml_file(data, config_file)
        world["config"].add_kb(KBConfig(name="typed", path=tmp_path / "typed", repo="org/x"))
        app_db(world["app"]).register_kb(
            "typed", "generic", str(tmp_path / "typed"), source="config"
        )
        before = config_file.read_bytes()

        r = _set_role(world["client"], "typed", "read")
        assert r.status_code == 409, r.text
        assert config_file.read_bytes() == before
        assert world["config"].get_kb("typed").default_role is None

    def test_a_hand_written_ephemeral_key_outside_the_root_is_the_operators(self, world, tmp_path):
        (tmp_path / "eph-typed").mkdir()
        world["config"].add_kb(
            KBConfig(name="eph-typed", path=tmp_path / "eph-typed", ephemeral=True, ttl=60)
        )
        app_db(world["app"]).register_kb(
            "eph-typed", "generic", str(tmp_path / "eph-typed"), source="config"
        )
        r = _set_role(world["client"], "eph-typed", "read")
        assert r.status_code == 409, r.text

    def test_a_repo_subscribed_kb_is_changed_and_saved(self, world):
        r = _set_role(world["client"], REPO_KB, "none")
        assert r.status_code == 200, r.text
        assert world["config"].get_kb(REPO_KB).default_role == "none"
        assert not _anonymous_reads(world["config"], REPO_KB)
        saved = config_module.load_config()
        assert saved.get_kb(REPO_KB).default_role == "none"

    def test_an_ephemeral_kb_is_changed_and_saved(self, world):
        from pyrite.services.ephemeral_service import EphemeralKBService

        EphemeralKBService(world["config"], app_db(world["app"])).create_ephemeral_kb(EPH_KB)
        r = _set_role(world["client"], EPH_KB, "read")
        assert r.status_code == 200, r.text
        assert _anonymous_reads(world["config"], EPH_KB)
        assert config_module.load_config().get_kb(EPH_KB).default_role == "read"

    def test_a_refused_save_changes_nothing(self, world, monkeypatch):
        from pyrite.exceptions import ConfigSaveRefusedError

        def refuse(*a, **k):
            raise ConfigSaveRefusedError("the file changed")

        monkeypatch.setattr(config_module, "save_config", refuse)
        r = _set_role(world["client"], REPO_KB, "none")
        assert r.status_code == 409, r.text
        assert world["config"].get_kb(REPO_KB).default_role == "read"
        with PyriteDB(world["config"].settings.index_path) as db:
            assert KBRegistryService(world["config"], db).registered_default_role(REPO_KB) == "read"

    def test_the_api_says_which_kbs_are_editable(self, world):
        r = world["client"].get("/api/kbs", headers=ADMIN)
        assert r.status_code == 200, r.text
        editable = {kb["name"]: kb["default_role_editable"] for kb in r.json()["kbs"]}
        assert editable[YAML_KB] is False
        assert editable[REPO_KB] is True


class TestARefusedSaveOnALongLivedSession:
    def test_a_later_commit_does_not_write_the_refused_role(self, tmp_path, monkeypatch):
        """The MCP server and the CLI keep one session for many calls: the
        refused change must not linger in it for the next commit."""
        from pyrite.exceptions import ConfigSaveRefusedError

        (tmp_path / "r").mkdir()
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="r", path=tmp_path / "r", repo="org/r")],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        with PyriteDB(config.settings.index_path) as db:
            registry = KBRegistryService(config, db)
            registry.seed_from_config()
            repo = db.register_repo("org/r", str(tmp_path / "r"))
            db.execute_write_sql("UPDATE kb SET repo_id = :r WHERE name = 'r'", {"r": repo["id"]})

            def refuse(*a, **k):
                raise ConfigSaveRefusedError("the file changed")

            monkeypatch.setattr(config_module, "save_config", refuse)
            with pytest.raises(ConfigSaveRefusedError):
                registry.update_kb("r", default_role="read")
            db.session.commit()
            assert registry.registered_default_role("r") is None


class TestNoGapDuringARegistryUpdate:
    def test_the_kb_stays_visible_and_the_old_lookup_is_never_mutated(self, tmp_path, monkeypatch):
        config = PyriteConfig(settings=Settings(index_path=tmp_path / "index.db"))
        with PyriteDB(config.settings.index_path) as db:
            registry = KBRegistryService(config, db)
            registry.add_kb("k", str(tmp_path / "k"))
            registry.add_kb("other", str(tmp_path / "other"))
            before = config._db_kb_cache
            snapshot = dict(before)
            seen_during = []
            real = PyriteConfig.kb_config_from_registry_row

            def watching(self, row):
                # While the replacement is being built, readers see the old one.
                seen_during.append(
                    (self.get_kb("k") is not None, {kb.name for kb in self.all_kbs()})
                )
                return real(self, row)

            monkeypatch.setattr(PyriteConfig, "kb_config_from_registry_row", watching)
            registry.update_kb("k", default_role="read")
            monkeypatch.undo()

            assert seen_during, "the refresh did not rebuild through the loader"
            assert all(found for found, _ in seen_during), "the KB went missing mid-update"
            assert all(names == {"k", "other"} for _, names in seen_during)
            assert before == snapshot, "the lookup a reader held was mutated"
            assert config._db_kb_cache is not before
            assert config.get_kb("k").default_role == "read"

    def test_removal_swaps_rather_than_mutates(self, tmp_path):
        config = PyriteConfig(settings=Settings(index_path=tmp_path / "index.db"))
        with PyriteDB(config.settings.index_path) as db:
            registry = KBRegistryService(config, db)
            registry.add_kb("k", str(tmp_path / "k"))
            before = config._db_kb_cache
            registry.remove_kb("k")
            assert "k" in before, "the lookup a reader held was mutated"
            assert config.get_kb("k") is None


class TestAKBChangeIsSerialised:
    def test_a_concurrent_save_cannot_persist_a_change_that_rolls_back(self, world, monkeypatch):
        """Thread A changes a server-written KB's role and its save fails
        after a delay; thread B saves the config for its own reason meanwhile.
        B must not write A's pending value, which A then rolls back."""
        import threading

        from pyrite.exceptions import ConfigSaveRefusedError

        real_save = config_module.save_config
        a_in_save = threading.Event()
        release_a = threading.Event()

        def a_save(*args, **kwargs):
            if threading.current_thread().name == "A":
                a_in_save.set()
                release_a.wait(10)
                raise ConfigSaveRefusedError("disk full")
            return real_save(*args, **kwargs)

        monkeypatch.setattr(config_module, "save_config", a_save)
        config, db = world["config"], app_db(world["app"])

        def change():
            with pytest.raises(ConfigSaveRefusedError):
                KBRegistryService(config, db).update_kb(REPO_KB, default_role="none")

        a = threading.Thread(target=change, name="A")
        b = threading.Thread(target=lambda: real_save(config), name="B")
        a.start()
        assert a_in_save.wait(10)
        b.start()
        b.join(2)  # without serialising, B writes A's pending value now
        release_a.set()
        a.join(10)
        b.join(10)

        saved = config_module.load_config().get_kb(REPO_KB).default_role
        assert config.get_kb(REPO_KB).default_role == "read"
        assert saved == "read", "the file kept a value memory and the row rolled back from"


class TestSaveEvidenceAndSequencing:
    def test_a_repo_key_not_matching_its_linked_record_is_the_operators(self, world, tmp_path):
        (tmp_path / "mismatch").mkdir()
        world["config"].add_kb(
            KBConfig(name="mismatch", path=tmp_path / "mismatch", repo="org/other")
        )
        db = app_db(world["app"])
        db.register_kb("mismatch", "generic", str(tmp_path / "mismatch"), source="config")
        db.execute_write_sql(
            "UPDATE kb SET repo_id = :r WHERE name = 'mismatch'", {"r": world["repo"]["id"]}
        )
        assert _set_role(world["client"], "mismatch", "read").status_code == 409

    def test_a_later_save_does_not_reapply_an_earlier_change_over_the_file(self, world):
        """After a save, the file is the baseline: an operator's edit made
        since is kept unless memory changes that key again."""
        config = config_module.load_config()
        config.settings.ai_model = "first"
        config_module.save_config(config)
        path = config_module.current_config_file()
        data = config_module.load_yaml_file(path)
        data["settings"]["ai_model"] = "operator-edit"
        config_module.dump_yaml_file(data, path)
        config.settings.ai_provider = "other-provider"
        config_module.save_config(config)
        assert config_module.load_yaml_file(path)["settings"]["ai_model"] == "operator-edit"

    def test_an_ephemeral_create_and_a_concurrent_save_are_serialised(self, world, monkeypatch):
        import threading

        from pyrite.exceptions import ConfigSaveRefusedError
        from pyrite.services import ephemeral_service
        from pyrite.services.ephemeral_service import EphemeralKBService

        real_save = config_module.save_config
        a_in_save = threading.Event()
        release_a = threading.Event()

        def a_save(*args, **kwargs):
            if threading.current_thread().name == "A":
                a_in_save.set()
                release_a.wait(10)
                raise ConfigSaveRefusedError("disk full")
            return real_save(*args, **kwargs)

        monkeypatch.setattr(ephemeral_service, "save_config", a_save)
        config, db = world["config"], app_db(world["app"])

        def create():
            with pytest.raises(ConfigSaveRefusedError):
                EphemeralKBService(config, db).create_ephemeral_kb("eph-race")

        a = threading.Thread(target=create, name="A")
        b = threading.Thread(target=lambda: real_save(config), name="B")
        a.start()
        assert a_in_save.wait(10)
        b.start()
        b.join(2)
        release_a.set()
        a.join(10)
        b.join(10)
        assert config.get_kb("eph-race") is None
        assert config_module.load_config().get_kb("eph-race") is None
