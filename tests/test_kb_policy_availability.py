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
from pyrite.services.site_cache import SiteCacheService
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
        assert "typed" not in public_kb_names(world["config"])

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
            repo = db.register_repo("org/r", str(tmp_path / "r"))
            db.execute_write_sql("UPDATE kb SET repo_id = :r WHERE name = 'r'", {"r": repo["id"]})

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
        import json

        public = set(public_kb_names(world["config"]))
        assert not (cache / kb).exists()
        listed = json.loads((cache / ".landing-kbs.json").read_text())  # LANDING_MANIFEST
        assert kb not in listed
        assert set(listed) <= public
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
        db.add_workspace_repo(db.get_local_user()["id"], world["repo"]["id"])
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


class TestOneRenderPerStalePage:
    """A stale landing costs one render, however many visitors find it, and
    a failing render is not retried on every anonymous GET."""

    @pytest.fixture
    def stale(self, world):
        db = app_db(world["app"])
        db.upsert_entry(_entry("doc", YAML_KB))
        SiteCacheService(world["config"], db).render_all()
        (world["tmp"] / "site-cache" / ".landing-kbs.json").unlink()  # stale: no manifest
        return world

    def test_concurrent_visitors_produce_one_render(self, stale, monkeypatch):
        import time

        import anyio
        import httpx

        calls = []
        real = SiteCacheService.render_landing

        def slow(self):
            calls.append(1)
            time.sleep(0.3)
            return real(self)

        monkeypatch.setattr(SiteCacheService, "render_landing", slow)
        app = stale["app"]

        async def visit_all():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as http:
                results = []

                async def one():
                    results.append(await http.get("/site"))

                async with anyio.create_task_group() as tg:
                    for _ in range(8):
                        tg.start_soon(one)
                return results

        with TestClient(app) as c:
            results = c.portal.call(visit_all)
        assert len(calls) == 1
        assert {r.status_code for r in results} == {200}
        assert app.state.site_refresh.renders == 1

    def test_the_render_re_checks_freshness_under_the_lock(self, stale):
        refresher = stale["app"].state.site_refresh
        served = stale["tmp"] / "site-cache"
        refresher.refresh(served, "landing", None)
        refresher.refresh(served, "landing", None)  # saw it stale too; it is fresh now
        assert refresher.renders == 1

    def test_a_failing_render_is_not_retried_on_every_visit(self, stale, monkeypatch):
        def broken(self):
            raise OSError("disk full")

        monkeypatch.setattr(SiteCacheService, "render_landing", broken)
        refresher = stale["app"].state.site_refresh
        for _ in range(5):
            assert stale["client"].get("/site").headers["X-Pyrite-Cache"] == "MISS"
        assert refresher.renders == 1

        refresher.backoff_seconds = 0.0  # the backoff has passed
        stale["client"].get("/site")
        assert refresher.renders == 2

    def test_a_visitor_during_a_render_is_answered_without_waiting(self, stale):
        import threading

        refresher = stale["app"].state.site_refresh
        answers = []
        with refresher.lock:  # a render is running
            t = threading.Thread(target=lambda: answers.append(stale["client"].get("/site")))
            t.start()
            t.join(10)
            assert not t.is_alive(), "the visitor waited for the render"
        assert answers[0].headers["X-Pyrite-Cache"] == "MISS"
        assert refresher.renders == 0


class TestRefresherGuards:
    @pytest.fixture
    def refresher(self, world):
        db = app_db(world["app"])
        db.upsert_entry(_entry("doc", YAML_KB))
        SiteCacheService(world["config"], db).render_all()
        return world["app"].state.site_refresh, world["tmp"] / "site-cache"

    def test_a_stale_kb_index_is_rendered_once(self, refresher):
        r, served = refresher
        (served / YAML_KB / ".index-stale").touch()
        r.refresh(served, "kb_index", YAML_KB)
        r.refresh(served, "kb_index", YAML_KB)
        assert r.renders == 1

    def test_refresh_never_waits_for_a_running_render(self, refresher):
        import threading

        r, served = refresher
        (served / ".landing-kbs.json").unlink()
        done = threading.Event()
        with r.lock:
            t = threading.Thread(target=lambda: (r.refresh(served, "landing", None), done.set()))
            t.start()
            assert done.wait(10), "refresh waited for the lock"
        assert r.renders == 0

    def test_may_try_is_false_while_a_render_runs(self, refresher):
        r, _ = refresher
        with r.lock:
            assert r.may_try("landing", None) is False
        assert r.may_try("landing", None) is True

    def test_a_queued_visitor_does_not_retry_a_render_that_just_failed(
        self, refresher, monkeypatch
    ):
        """Visitors already past `may_try` when the render failed must not
        each render again."""
        r, served = refresher
        (served / ".landing-kbs.json").unlink()

        def broken(self):
            raise OSError("disk full")

        monkeypatch.setattr(SiteCacheService, "render_landing", broken)
        r.refresh(served, "landing", None)
        r.refresh(served, "landing", None)
        assert r.renders == 1


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
