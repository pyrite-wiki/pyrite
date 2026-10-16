"""A completed sync publishes the actual public cache and its render outcome."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app
from pyrite.services.index_worker import IndexWorker
from pyrite.services.site_cache import SiteCacheService
from pyrite.storage.database import PyriteDB


@pytest.fixture
def world(tmp_path, monkeypatch):
    kb = tmp_path / "public"
    kb.mkdir()
    index = tmp_path / "index-home"
    index.mkdir()
    monkeypatch.setenv("PYRITE_DATA_DIR", str(tmp_path / "different-data-home"))
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="public", path=kb, default_role="read")],
        settings=Settings(index_path=index / "index.db"),
    )
    app = create_app(config=config)
    with TestClient(app) as client:
        yield config, app, client, index / "site-cache"


def write(config, title):
    path = config.get_kb("public").path / "note.md"
    path.write_text(f"---\nid: note\ntype: note\ntitle: {title}\n---\nCurrent body\n")
    return path


def sync(app, client, wait=False):
    response = client.post("/api/index/sync", params={"wait": wait})
    assert response.status_code == 200, response.text
    if wait:
        return response.json()
    app.state.pyrite_index_worker.wait_for_idle(timeout=10)
    job = client.get(f"/api/index/jobs/{response.json()['job_id']}")
    assert job.status_code == 200
    assert job.json()["status"] == "completed", job.text
    return job.json()


@pytest.mark.parametrize("wait", [False, True])
def test_sync_and_serving_use_the_configured_cache_home(world, wait):
    config, app, client, cache = world
    write(config, "First published title")
    result = sync(app, client, wait)
    assert result["site_cache"]["rendered"] is True
    assert (cache / "public/note.html").is_file()
    response = client.get("/site/public/note")
    assert response.status_code == 200
    assert "First published title" in response.text
    # The cache is actually served, rather than an independently generated fallback.
    (cache / "public/note.html").write_text("<html>cached-page-marker</html>")
    assert "cached-page-marker" in client.get("/site/public/note").text


def test_background_sync_refreshes_edits_and_prunes_deleted_pages(world):
    config, app, client, cache = world
    path = write(config, "Before edit")
    sync(app, client)
    write(config, "After external edit with a longer title")
    result = sync(app, client)
    assert result["updated"] == 1
    assert "After external edit" in client.get("/site/public/note").text
    path.unlink()
    result = sync(app, client)
    assert result["removed"] == 1
    assert not (cache / "public/note.html").exists()
    assert client.get("/site/public/note").status_code == 404
    assert "/site/public/note" not in client.get("/site/sitemap.xml").text


def test_background_rebuild_publishes_its_current_cache(world):
    config, app, client, cache = world
    write(config, "Before rebuild")
    sync(app, client, wait=True)
    write(config, "New title from a complete rebuild")
    job_id = app.state.pyrite_index_worker.submit_rebuild("public")
    app.state.pyrite_index_worker.wait_for_idle(timeout=10)
    job = client.get(f"/api/index/jobs/{job_id}").json()
    assert job["status"] == "completed"
    assert job["site_cache"]["rendered"] is True
    assert "New title from a complete rebuild" in (cache / "public/note.html").read_text()


def test_render_failure_does_not_undo_sync_and_is_persisted(world, monkeypatch):
    config, app, client, _ = world
    write(config, "Committed index entry")

    def fail(_self):
        raise RuntimeError("private-server-path")

    monkeypatch.setattr(SiteCacheService, "render_all", fail)
    result = sync(app, client)
    assert result["added"] == 1
    assert result["error"] is None
    assert result["site_cache"] == {
        "rendered": False,
        "errors": 0,
        "error": "site cache render failed; see server log",
    }
    # Opening another worker against the same DB retains the public render outcome.
    db = PyriteDB(config.settings.index_path)
    try:
        worker = IndexWorker(db, config)
        assert worker.get_job(result["job_id"])["site_cache"] == result["site_cache"]
        assert db.get_entry("note", "public") is not None
    finally:
        db.close()


@pytest.mark.parametrize("wait", [False, True])
def test_zero_change_retry_repairs_a_failed_render(world, monkeypatch, wait):
    config, app, client, cache = world
    write(config, "Recovered cache")
    original = SiteCacheService.render_all

    def fail(_self):
        raise RuntimeError("temporary render failure")

    monkeypatch.setattr(SiteCacheService, "render_all", fail)
    assert sync(app, client, wait)["site_cache"]["rendered"] is False
    monkeypatch.setattr(SiteCacheService, "render_all", original)
    result = sync(app, client, wait)
    assert result["added"] + result["updated"] + result["removed"] == 0
    assert result["site_cache"]["rendered"] is True
    assert (cache / "public/note.html").is_file()


@pytest.mark.control(
    reason="Pre-existing completed jobs must remain readable without a fabricated render outcome"
)
def test_legacy_index_jobs_survive_worker_upgrade(world):
    config, _app, _client, _cache = world
    db = PyriteDB(config.settings.index_path)
    try:
        db._raw_conn.execute("""CREATE TABLE index_job (
            job_id TEXT PRIMARY KEY, kb_name TEXT, operation TEXT, status TEXT,
            progress_current INTEGER, progress_total INTEGER, added INTEGER,
            updated INTEGER, removed INTEGER, error TEXT, created_at TEXT,
            completed_at TEXT)""")
        db._raw_conn.execute("""INSERT INTO index_job
            (job_id, operation, status, created_at) VALUES ('old', 'sync', 'completed', 'old')""")
        db._raw_conn.commit()
        for _ in range(2):
            worker = IndexWorker(db, config)
            job = worker.get_job("old")
            assert job["status"] == "completed"
            assert job.get("site_cache") is None
    finally:
        db.close()


def test_full_renderers_of_one_cache_directory_do_not_overlap(world, monkeypatch):
    config, app, client, cache = world
    write(config, "Concurrent page")
    sync(app, client, wait=True)
    state_lock = threading.Lock()
    both_entered = threading.Event()
    ready = threading.Barrier(2)
    active = maximum = 0
    original = SiteCacheService._render_landing

    def observed(self, kbs):
        nonlocal active, maximum
        with state_lock:
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                both_entered.set()
        try:
            both_entered.wait(timeout=0.15)
            return original(self, kbs)
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(SiteCacheService, "_render_landing", observed)

    def render():
        db = PyriteDB(config.settings.index_path)
        try:
            service = SiteCacheService(config, db)
            ready.wait(timeout=5)
            return service.render_all()
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: render(), range(2)))
    assert maximum == 1
    assert all(result["errors"] == 0 for result in results)
    assert "Concurrent page" in (cache / "public/note.html").read_text()
