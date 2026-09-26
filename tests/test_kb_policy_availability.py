"""The default_role fix never costs availability.

The access properties (tests/test_default_role_change_takes_effect.py) must
not turn into a dark public site or a blocked admin:

- removing a KB by any path keeps the `/site` landing served;
- an admin can change the role of any KB the server manages itself (a
  registry KB, an ephemeral one, a repo-subscribed one); only a KB an
  operator wrote by hand into config.yaml is refused, naming that file;
- an unrendered public entry answers `/site` exactly as a private or a
  missing one does;
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
from pyrite.services.public_kbs import public_kb_names
from pyrite.services.site_cache import LANDING_MANIFEST, SiteCacheService, landing_is_current
from pyrite.storage.database import PyriteDB
from tests.auth_seed import app_db

KEY = "operator-key"
ADMIN = {"X-API-Key": KEY}
YAML_KB, REPO_KB, EPH_KB = "yaml-kb", "repo-kb", "eph-kb"


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
    yield {"app": app, "client": client, "config": config, "tmp": tmp_path}
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
        assert str(config_module.current_config_file()) in detail["message"]

    def test_a_repo_subscribed_kb_is_changed_and_saved(self, world):
        r = _set_role(world["client"], REPO_KB, "none")
        assert r.status_code == 200, r.text
        assert world["config"].get_kb(REPO_KB).default_role == "none"
        assert REPO_KB not in public_kb_names(world["config"])
        saved = config_module.load_config()
        assert saved.get_kb(REPO_KB).default_role == "none"

    def test_an_ephemeral_kb_is_changed_and_saved(self, world):
        from pyrite.services.ephemeral_service import EphemeralKBService

        EphemeralKBService(world["config"], app_db(world["app"])).create_ephemeral_kb(EPH_KB)
        r = _set_role(world["client"], EPH_KB, "read")
        assert r.status_code == 200, r.text
        assert EPH_KB in public_kb_names(world["config"])
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

            def refuse(*a, **k):
                raise ConfigSaveRefusedError("the file changed")

            monkeypatch.setattr(config_module, "save_config", refuse)
            with pytest.raises(ConfigSaveRefusedError):
                registry.update_kb("r", default_role="read")
            db.session.commit()
            assert registry.registered_default_role("r") is None


class TestRemovingAKBKeepsTheLandingUp:
    """Every removal path drops the KB's pages and re-renders the landing,
    so the manifest never names a KB that is gone."""

    def _render_with(self, world, kb):
        db = app_db(world["app"])
        db.upsert_entry(_entry("doc", kb))
        SiteCacheService(world["config"], db).render_all()
        cache = world["tmp"] / "site-cache"
        assert (cache / kb).is_dir()
        return cache

    def _assert_landing_up_without(self, world, cache, kb):
        public = set(public_kb_names(world["config"]))
        assert not (cache / kb).exists()
        assert landing_is_current(cache, public)
        assert kb not in (cache / LANDING_MANIFEST).read_text()
        r = world["client"].get("/site")
        assert r.headers["X-Pyrite-Cache"] == "HIT"
        assert YAML_KB in r.text

    def test_expiring_a_public_ephemeral_kb(self, world):
        from pyrite.services.ephemeral_service import EphemeralKBService

        svc = EphemeralKBService(world["config"], app_db(world["app"]))
        svc.create_ephemeral_kb(EPH_KB, default_role="read")
        cache = self._render_with(world, EPH_KB)
        assert svc.force_expire_kb(EPH_KB) is True
        self._assert_landing_up_without(world, cache, EPH_KB)

    def test_unsubscribing_a_public_repo_kb(self, world):
        from unittest.mock import patch

        from pyrite.services.repo_service import RepoService

        db = app_db(world["app"])
        repo = db.register_repo("org/x", str(world["tmp"] / REPO_KB))
        db.execute_write_sql(
            "UPDATE kb SET repo_id = :r WHERE name = :n", {"r": repo["id"], "n": REPO_KB}
        )
        db.add_workspace_repo(db.get_local_user()["id"], repo["id"])
        cache = self._render_with(world, REPO_KB)
        with patch("pyrite.services.repo_service.save_config"):
            assert RepoService(world["config"], db).unsubscribe("org/x")["success"] is True
        self._assert_landing_up_without(world, cache, REPO_KB)


class TestOnDemandRendering:
    def test_never_renders_where_site_does_not_serve_from(self, world, monkeypatch, tmp_path):
        """/site serves $PYRITE_DATA_DIR/site-cache; the renderer writes
        beside the index. When they differ, a stale landing is withheld, not
        rendered into a directory nobody serves."""
        served = tmp_path / "served"
        (served / "site-cache").mkdir(parents=True)
        (served / "site-cache" / "index.html").write_text("<html>old landing</html>")
        monkeypatch.setenv("PYRITE_DATA_DIR", str(served))
        app = create_app(config=world["config"])
        r = TestClient(app).get("/site")
        assert r.headers["X-Pyrite-Cache"] == "MISS"
        assert not (world["tmp"] / "site-cache").exists()


class TestAnUnrenderedPublicEntry:
    def test_answers_exactly_as_a_private_or_missing_entry(self, world):
        c = world["client"]
        db = app_db(world["app"])
        db.register_kb(
            "closed-kb", "generic", str(world["tmp"]), source="user", default_role="none"
        )
        db.upsert_entry(_entry("rendered", YAML_KB))
        db.upsert_entry(_entry("secret", "closed-kb"))
        SiteCacheService(world["config"], db).render_all()
        db.upsert_entry(_entry("not-yet", YAML_KB))  # public, never rendered

        answers = [
            c.get(f"/site/{YAML_KB}/not-yet"),
            c.get(f"/site/{YAML_KB}/no-such-entry"),
            c.get("/site/closed-kb/secret"),
            c.get("/site/no-such-kb/x"),
        ]
        assert [r.status_code for r in answers] == [404] * 4
        assert len({r.text for r in answers}) == 1
        assert c.get(f"/site/{YAML_KB}/rendered").status_code == 200

    def test_in_a_public_kb_never_rendered_at_all(self, world):
        c = world["client"]
        app_db(world["app"]).upsert_entry(_entry("not-yet", YAML_KB))
        r = c.get(f"/site/{YAML_KB}/not-yet")
        assert r.status_code == 404
        assert r.text == c.get("/site/no-such-kb/x").text


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
