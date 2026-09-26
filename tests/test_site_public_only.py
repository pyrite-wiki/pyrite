"""The pre-rendered /site must never serve a KB that is not public.

`SiteCacheService.render_all()` rendered every KB, including private
(`default_role: none`) ones, and `/site/*` serves the cache with no
authentication and CDN-cacheable headers. After an admin render, an
anonymous visitor could read every private entry, and `/site/sitemap.xml`
advertised them to crawlers.

The rule for "public" is one function (`pyrite.services.public_kbs`), shared
with the live `/sitemap.xml`: `default_role == "read"`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")
from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app
from pyrite.services.site_cache import SiteCacheService
from pyrite.storage.database import PyriteDB

PUBLIC, PRIVATE, UNSET = "public-kb", "private-kb", "unset-kb"
SECRET = "ZEBRA-SECRET-BODY"
SECRET_TITLE = "Classified Title"


def _entry(eid: str, kb: str, title: str, body: str, links=None, tags=None) -> dict:
    return {
        "id": eid,
        "kb_name": kb,
        "entry_type": "note",
        "title": title,
        "body": body,
        "summary": "",
        "tags": tags or [],
        "sources": [],
        "links": links or [],
        "metadata": {},
    }


@pytest.fixture
def site_env(tmp_path, monkeypatch):
    # /site serves from $PYRITE_DATA_DIR/site-cache; the renderer writes to
    # <index dir>/site-cache. Point both at tmp_path, as the containers do.
    monkeypatch.setenv("PYRITE_DATA_DIR", str(tmp_path))
    kbs = []
    for name, role in ((PUBLIC, "read"), (PRIVATE, "none"), (UNSET, None)):
        (tmp_path / name).mkdir()
        kbs.append(KBConfig(name=name, path=tmp_path / name, kb_type="generic", default_role=role))
    config = PyriteConfig(
        knowledge_bases=kbs,
        settings=Settings(
            index_path=tmp_path / "index.db",
            auth=AuthConfig(enabled=True, allow_registration=True),
        ),
    )
    db = PyriteDB(config.settings.index_path)
    for kb in kbs:
        db.register_kb(kb.name, "generic", str(kb.path), f"{kb.name} description")
    db.upsert_entry(
        _entry(
            "pub-1",
            PUBLIC,
            "Public One",
            "Open body.",
            # Outlink to a private entry: its title must not appear.
            links=[{"target": "secret-1", "kb": PRIVATE, "relation": "related_to"}],
        )
    )
    db.upsert_entry(
        _entry(
            "secret-1",
            PRIVATE,
            SECRET_TITLE,
            SECRET,
            # Backlink into the public entry: its title must not appear either.
            links=[{"target": "pub-1", "kb": PUBLIC, "relation": "related_to"}],
        )
    )
    db.upsert_entry(_entry("unset-1", UNSET, "Unset One", "Unset body " + SECRET))
    svc = SiteCacheService(config, db)
    yield {"config": config, "db": db, "svc": svc, "cache_dir": svc.cache_dir, "tmp": tmp_path}
    db.close()


def _client(env) -> TestClient:
    return TestClient(create_app(config=env["config"]))


class TestAnonymousSiteLeak:
    def test_anonymous_cannot_read_private_entry_after_render(self, site_env):
        site_env["svc"].render_all()
        client = _client(site_env)
        r = client.get(f"/site/{PRIVATE}/secret-1")
        assert SECRET not in r.text
        assert r.status_code == 404

    def test_public_entry_still_served(self, site_env):
        site_env["svc"].render_all()
        r = _client(site_env).get(f"/site/{PUBLIC}/pub-1")
        assert r.status_code == 200
        assert "Open body." in r.text


class TestRenderOnlyPublic:
    def test_private_and_unset_kbs_not_rendered(self, site_env):
        stats = site_env["svc"].render_all()
        cache = site_env["cache_dir"]
        assert stats["kbs"] == 1
        assert (cache / PUBLIC / "pub-1.html").is_file()
        assert not (cache / PRIVATE).exists()
        assert not (cache / UNSET).exists()

    def test_landing_lists_only_public(self, site_env):
        site_env["svc"].render_all()
        landing = (site_env["cache_dir"] / "index.html").read_text()
        assert PUBLIC in landing
        assert PRIVATE not in landing
        assert UNSET not in landing

    def test_entry_page_omits_links_to_and_from_private_kbs(self, site_env):
        site_env["svc"].render_all()
        html = (site_env["cache_dir"] / PUBLIC / "pub-1.html").read_text()
        assert SECRET_TITLE not in html
        assert f"/site/{PRIVATE}/" not in html

    def test_render_entry_by_id_refuses_private_kb(self, site_env):
        assert site_env["svc"].render_entry_by_id("secret-1", PRIVATE) is False
        assert not (site_env["cache_dir"] / PRIVATE / "secret-1.html").exists()

    def test_public_kb_renders_same_files(self, site_env):
        site_env["svc"].render_all()
        files = sorted(
            str(p.relative_to(site_env["cache_dir"]))
            for p in site_env["cache_dir"].rglob("*")
            if p.is_file()
        )
        assert files == [
            # The KBs the landing was rendered with (site_cache.LANDING_MANIFEST).
            ".landing-kbs.json",
            "index.html",
            f"{PUBLIC}/index.html",
            f"{PUBLIC}/page/1.html",
            f"{PUBLIC}/pub-1.html",
        ]


def _plant_stale(cache_dir: Path) -> None:
    """A cache rendered by an earlier version, private KB included."""
    stale = cache_dir / PRIVATE
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "secret-1.html").write_text(f"<html>{SECRET}</html>")
    (stale / "index.html").write_text(f"<html>{SECRET}</html>")


class TestStaleCache:
    def test_stale_private_entry_not_served(self, site_env):
        site_env["svc"].render_all()
        _plant_stale(site_env["cache_dir"])
        client = _client(site_env)
        for path in (f"/site/{PRIVATE}/secret-1", f"/site/{PRIVATE}", f"/site/{PRIVATE}/"):
            r = client.get(path)
            assert r.status_code == 404, path
            assert SECRET not in r.text, path

    @pytest.mark.parametrize(
        "path",
        [
            # Dot segments after a public KB name, percent-encoded so the
            # client does not normalise them away before the request.
            f"/site/{PUBLIC}/%2e%2e/{PRIVATE}/secret-1",
            f"/site/{PUBLIC}/%2E%2E/{PRIVATE}/secret-1",
            f"/site/{PUBLIC}/.%2e/{PRIVATE}/secret-1",
            f"/site/{PUBLIC}/%2e%2e/{PRIVATE}/index",
            f"/site/{PUBLIC}/%2e/%2e%2e/{PRIVATE}/secret-1",
            # Encoded separators: one URL segment that decodes to several.
            f"/site/{PUBLIC}/..%2F{PRIVATE}%2Fsecret-1",
            f"/site/{PUBLIC}/..%2f{PRIVATE}%2fsecret-1",
            f"/site/{PUBLIC}%2F..%2F{PRIVATE}%2Fsecret-1",
            f"/site/{PUBLIC}%2f%2e%2e%2f{PRIVATE}",
        ],
    )
    def test_dot_segment_after_public_kb_cannot_reach_private(self, site_env, path):
        site_env["svc"].render_all()
        _plant_stale(site_env["cache_dir"])
        r = _client(site_env).get(path)
        assert SECRET not in r.text, path
        assert r.status_code == 404, (path, r.status_code)

    @pytest.mark.parametrize(
        "path",
        [f"/site/{PUBLIC}/%2e/pub-1", f"/site/{PUBLIC}/.%2Fpub-1"],
    )
    def test_single_dot_segment_is_refused(self, site_env, path):
        """`.` segments are refused outright, not normalised."""
        site_env["svc"].render_all()
        r = _client(site_env).get(path)
        assert r.status_code == 404, (path, r.status_code)

    def test_file_symlink_from_public_into_private_is_refused(self, site_env):
        """The URL names a public KB; the file it resolves to does not."""
        site_env["svc"].render_all()
        _plant_stale(site_env["cache_dir"])
        cache = site_env["cache_dir"]
        (cache / PUBLIC / "leak.html").symlink_to(cache / PRIVATE / "secret-1.html")
        r = _client(site_env).get(f"/site/{PUBLIC}/leak")
        assert SECRET not in r.text
        assert r.status_code == 404

    def test_directory_symlink_from_public_into_private_is_refused(self, site_env):
        site_env["svc"].render_all()
        _plant_stale(site_env["cache_dir"])
        cache = site_env["cache_dir"]
        (cache / PUBLIC / "sub").symlink_to(cache / PRIVATE, target_is_directory=True)
        r = _client(site_env).get(f"/site/{PUBLIC}/sub/secret-1")
        assert SECRET not in r.text
        assert r.status_code == 404

    def test_public_kb_dir_replaced_by_link_to_private_is_refused(self, site_env):
        import shutil

        site_env["svc"].render_all()
        _plant_stale(site_env["cache_dir"])
        cache = site_env["cache_dir"]
        shutil.rmtree(cache / PUBLIC)
        (cache / PUBLIC).symlink_to(cache / PRIVATE, target_is_directory=True)
        client = _client(site_env)
        for path in (f"/site/{PUBLIC}/secret-1", f"/site/{PUBLIC}", f"/site/{PUBLIC}/"):
            r = client.get(path)
            assert SECRET not in r.text, path
            assert r.status_code == 404, path

    def test_unknown_first_segment_is_404(self, site_env):
        r = _client(site_env).get("/site/no-such-kb/x")
        assert r.status_code == 404

    def test_site_sitemap_excludes_private_kb(self, site_env):
        site_env["svc"].render_all()
        _plant_stale(site_env["cache_dir"])
        r = _client(site_env).get("/site/sitemap.xml")
        assert r.status_code == 200
        assert f"/site/{PUBLIC}/pub-1" in r.text
        assert PRIVATE not in r.text
        assert "secret-1" not in r.text


class TestOnePublicRule:
    def test_sitemap_service_and_site_cache_share_the_rule(self, site_env):
        from pyrite.services import public_kbs
        from pyrite.services.sitemap_service import SitemapService

        assert public_kbs.public_kb_names(site_env["config"]) == [PUBLIC]
        assert SitemapService(site_env["config"], site_env["db"])._public_kb_names() == [PUBLIC]
        assert public_kbs.is_public_kb(site_env["config"], PUBLIC)
        assert not public_kbs.is_public_kb(site_env["config"], PRIVATE)
        assert not public_kbs.is_public_kb(site_env["config"], UNSET)
        assert not public_kbs.is_public_kb(site_env["config"], "no-such-kb")
