"""After a render, `/site` holds exactly the current public entry set (P-S3).

`SiteCacheService.render_all` only ever wrote pages: an entry deleted since
the last render kept its page, its line in `/site/sitemap.xml` and its row
in its KB's index pages, served to anonymous visitors. The invalidation
helpers that existed had no callers and joined the raw entry id where the
renderer writes `sanitize_filename(id)`.

Pinned here:

- a render prunes every cached page that is not a current public entry;
- deleting an entry removes its page and its sitemap line at once;
- the invalidation path uses the renderer's file name.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")
from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app
from pyrite.services.site_cache import SiteCacheService
from pyrite.storage.database import PyriteDB

KB = "pub-kb"
KEY = "operator-key"
ADMIN = {"X-API-Key": KEY}


def _entry(eid: str, title: str) -> dict:
    return {
        "id": eid,
        "kb_name": KB,
        "entry_type": "note",
        "title": title,
        "body": f"body of {eid}",
        "summary": "",
        "tags": [],
        "sources": [],
        "links": [],
        "metadata": {},
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PYRITE_DATA_DIR", str(tmp_path))
    (tmp_path / KB).mkdir()
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name=KB, path=tmp_path / KB, kb_type="generic", default_role="read")
        ],
        settings=Settings(
            index_path=tmp_path / "index.db",
            api_key=KEY,
            auth=AuthConfig(enabled=True, allow_registration=True, anonymous_tier="read"),
        ),
    )
    db = PyriteDB(config.settings.index_path)
    db.register_kb(KB, "generic", str(tmp_path / KB), "public", source="config")
    yield {"config": config, "db": db, "cache": tmp_path / "site-cache", "tmp": tmp_path}
    db.close()


class TestRenderPrunes:
    def test_a_render_removes_the_page_of_an_entry_gone_from_the_index(self, env):
        db = env["db"]
        db.upsert_entry(_entry("keep", "Keep Me"))
        db.upsert_entry(_entry("gone", "Gone Title"))
        SiteCacheService(env["config"], db).render_all()
        assert (env["cache"] / KB / "gone.html").is_file()

        db.delete_entry("gone", KB)
        SiteCacheService(env["config"], db).render_all()

        assert not (env["cache"] / KB / "gone.html").exists()
        assert (env["cache"] / KB / "keep.html").is_file()
        index = (env["cache"] / KB / "index.html").read_text()
        assert "Gone Title" not in index
        client = TestClient(create_app(config=env["config"]))
        assert client.get(f"/site/{KB}/gone").status_code == 404
        assert f"/site/{KB}/gone" not in client.get("/site/sitemap.xml").text

    def test_a_render_removes_a_kb_that_is_no_longer_public(self, env):
        db = env["db"]
        db.upsert_entry(_entry("keep", "Keep Me"))
        SiteCacheService(env["config"], db).render_all()
        env["config"].get_kb(KB).default_role = "none"
        SiteCacheService(env["config"], db).render_all()
        assert not (env["cache"] / KB).exists()
        assert KB not in (env["cache"] / "index.html").read_text()

    def test_a_render_removes_surplus_pagination_pages(self, env):
        db = env["db"]
        for i in range(3):
            db.upsert_entry(_entry(f"e{i}", f"Entry {i}"))
        svc = SiteCacheService(env["config"], db)
        svc.render_all()
        # A stale page 2 from a render when the KB was larger.
        (env["cache"] / KB / "page" / "2.html").write_text("<html>Old Title</html>")
        svc.render_all()
        assert not (env["cache"] / KB / "page" / "2.html").exists()

    def test_a_sanitised_page_name_is_kept_while_its_entry_exists(self, env):
        """The renderer writes `sanitize_filename(id)`; pruning must compare
        against that name, not the raw id, or it deletes live pages."""
        db = env["db"]
        db.upsert_entry(_entry("odd..id", "Odd"))
        SiteCacheService(env["config"], db).render_all()
        SiteCacheService(env["config"], db).render_all()
        assert (env["cache"] / KB / "oddid.html").is_file()


class TestDeleteRemovesThePage:
    def test_rest_delete_removes_the_page_and_its_sitemap_line(self, env):
        client = TestClient(create_app(config=env["config"]))
        for title in ("Stays Here", "Goes Away"):
            r = client.post("/api/entries", json={"kb": KB, "title": title}, headers=ADMIN)
            assert r.status_code == 200, r.text
        gone_id = "goes-away"
        r = client.post("/api/site/render", headers=ADMIN)
        assert r.status_code == 200, r.text
        assert client.get(f"/site/{KB}/{gone_id}").status_code == 200
        assert f"/site/{KB}/{gone_id}" in client.get("/site/sitemap.xml").text

        r = client.delete(f"/api/entries/{gone_id}", params={"kb": KB}, headers=ADMIN)
        assert r.status_code == 200, r.text

        assert client.get(f"/site/{KB}/{gone_id}").status_code == 404
        assert f"/site/{KB}/{gone_id}" not in client.get("/site/sitemap.xml").text
        assert "Goes Away" not in client.get(f"/site/{KB}").text
        assert client.get(f"/site/{KB}/stays-here").status_code == 200
        assert "1 entries" in client.get("/site").text

    def test_the_invalidation_uses_the_sanitised_file_name(self, env):
        db = env["db"]
        db.upsert_entry(_entry("odd..id", "Odd"))
        svc = SiteCacheService(env["config"], db)
        svc.render_all()
        assert (env["cache"] / KB / "oddid.html").is_file()
        db.delete_entry("odd..id", KB)
        svc.invalidate_entry("odd..id", KB)
        assert not (env["cache"] / KB / "oddid.html").exists()

    def test_the_invalidation_never_leaves_the_kb_directory(self, env):
        db = env["db"]
        db.upsert_entry(_entry("keep", "Keep"))
        svc = SiteCacheService(env["config"], db)
        svc.render_all()
        outside = env["cache"] / "index.html"
        assert outside.is_file()
        svc.invalidate_entry("../index", KB)
        assert outside.is_file()

    def test_a_kb_name_never_reaches_outside_the_cache(self, env):
        db = env["db"]
        db.upsert_entry(_entry("keep", "Keep"))
        svc = SiteCacheService(env["config"], db)
        svc.render_all()
        for name in ("..", ".", "a/../..", ""):
            svc.invalidate_kb(name)
            svc.invalidate_entry("keep", name)
        assert (env["tmp"] / "index.db").is_file()
        assert (env["cache"] / KB / "keep.html").is_file()

    def test_a_delete_in_a_kb_that_is_no_longer_public_renders_nothing_for_it(self, env):
        """The KB closed since the last render (config.yaml edited, server
        restarted): the delete must not re-render the closed KB's index."""
        db = env["db"]
        db.upsert_entry(_entry("keep", "Keep"))
        db.upsert_entry(_entry("gone", "Gone"))
        svc = SiteCacheService(env["config"], db)
        svc.render_all()
        env["config"].get_kb(KB).default_role = "none"
        db.delete_entry("gone", KB)
        svc.invalidate_entry("gone", KB)
        assert not (env["cache"] / KB).exists()
