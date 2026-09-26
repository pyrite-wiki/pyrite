"""A KB's `default_role` has one source of truth, applied on the next request.

`PUT /api/kbs/{name}/default-role` wrote the registry row, but the access
policy and `public_kb_names` read the in-memory config first, and the
config's cache of registry KBs (`PyriteConfig._db_kb_cache`) was filled once
and never refreshed. A KB an admin closed stayed readable by anonymous REST,
`/site` and the sitemaps until a restart. For a KB defined in config.yaml the
endpoint reported success and changed nothing, ever.

The properties pinned here (kb/designs/multi-user-threat-model.md P-S1, P-S3,
P-M1, P-F6):

- a change through the endpoint takes effect on the next request on every
  surface -- anonymous REST, the MCP scope, `/site`, `/site/sitemap.xml`,
  the live `/sitemap.xml`, and the `/site` landing page;
- an endpoint that cannot apply a change (a config.yaml KB) refuses it;
- every registry write path (default-role, add, update, remove) leaves the
  config's view of the KB equal to the row.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")
from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app
from pyrite.services.kb_registry_service import KBRegistryService
from pyrite.services.public_kbs import public_kb_names
from pyrite.storage.database import PyriteDB

USER_KB = "open-kb"  # registered in the index, not in config.yaml
YAML_KB = "yaml-kb"  # defined in config.yaml
ENTRY = "doc-1"
BODY = "OCELOT-BODY-TEXT"
KEY = "operator-key"
ADMIN = {"X-API-Key": KEY}


def _entry(eid: str, kb: str) -> dict:
    return {
        "id": eid,
        "kb_name": kb,
        "entry_type": "note",
        "title": f"Title of {eid}",
        "body": BODY,
        "summary": "",
        "tags": [],
        "sources": [],
        "links": [],
        "metadata": {},
    }


@pytest.fixture
def world(tmp_path, monkeypatch):
    # /site serves from $PYRITE_DATA_DIR/site-cache; the renderer writes to
    # <index dir>/site-cache. Point both at tmp_path, as the containers do.
    monkeypatch.setenv("PYRITE_DATA_DIR", str(tmp_path))
    (tmp_path / USER_KB).mkdir()
    (tmp_path / YAML_KB).mkdir()
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name=YAML_KB, path=tmp_path / YAML_KB, kb_type="generic", default_role="read")
        ],
        settings=Settings(
            index_path=tmp_path / "index.db",
            api_key=KEY,
            auth=AuthConfig(enabled=True, allow_registration=True, anonymous_tier="read"),
        ),
    )
    with PyriteDB(config.settings.index_path) as db:
        db.register_kb(YAML_KB, "generic", str(tmp_path / YAML_KB), "yaml", source="config")
        db.register_kb(
            USER_KB,
            "generic",
            str(tmp_path / USER_KB),
            "user kb description",
            source="user",
            default_role="read",
        )
        db.upsert_entry(_entry(ENTRY, USER_KB))
        db.upsert_entry(_entry("yaml-doc", YAML_KB))
    app = create_app(config=config)
    client = TestClient(app)
    yield {"app": app, "client": client, "config": config, "tmp": tmp_path}
    state_db = getattr(app.state, "pyrite_db", None)
    if state_db is not None:
        state_db.close()


def _render(client):
    r = client.post("/api/site/render", headers=ADMIN)
    assert r.status_code == 200, r.text


def _set_role(client, kb, role):
    return client.put(f"/api/kbs/{kb}/default-role", json={"role": role}, headers=ADMIN)


def _anon_rest(client):
    return client.get(f"/api/entries/{ENTRY}", params={"kb": USER_KB})


class TestClosingARegistryKB:
    """`default_role` read -> none, on a KB registered in the index."""

    @pytest.mark.control(
        reason="the precondition the other tests start from; true before and after"
    )
    def test_open_before_the_change(self, world):
        c = world["client"]
        _render(c)
        assert _anon_rest(c).status_code == 200
        assert c.get(f"/site/{USER_KB}/{ENTRY}").status_code == 200
        assert f"/site/{USER_KB}/{ENTRY}" in c.get("/site/sitemap.xml").text

    def test_anonymous_rest_is_404_after_the_change(self, world):
        c = world["client"]
        assert _anon_rest(c).status_code == 200
        assert _set_role(c, USER_KB, "none").status_code == 200
        r = _anon_rest(c)
        assert r.status_code == 404, r.text
        assert BODY not in r.text

    def test_site_page_is_404_after_the_change(self, world):
        c = world["client"]
        _render(c)
        assert _set_role(c, USER_KB, "none").status_code == 200
        r = c.get(f"/site/{USER_KB}/{ENTRY}")
        assert r.status_code == 404
        assert BODY not in r.text
        assert c.get(f"/site/{USER_KB}").status_code == 404

    def test_site_sitemap_drops_the_kb_after_the_change(self, world):
        c = world["client"]
        _render(c)
        assert _set_role(c, USER_KB, "none").status_code == 200
        assert USER_KB not in c.get("/site/sitemap.xml").text

    def test_live_sitemap_drops_the_kb_after_the_change(self, world):
        c = world["client"]
        assert _set_role(c, USER_KB, "none").status_code == 200
        assert USER_KB not in c.get("/sitemap.xml").text

    def test_landing_page_drops_the_kb_after_the_change(self, world):
        """A6-F6: the pre-rendered landing kept the de-publicised KB's card."""
        c = world["client"]
        _render(c)
        assert USER_KB in c.get("/site").text
        assert _set_role(c, USER_KB, "none").status_code == 200
        r = c.get("/site")
        assert USER_KB not in r.text
        assert "user kb description" not in r.text
        # Re-rendered without it, not merely withheld: the site stays up.
        assert r.headers["X-Pyrite-Cache"] == "HIT"
        assert YAML_KB in r.text

    def test_the_closed_kbs_cached_pages_leave_the_disk(self, world):
        """P-F6: a closed KB's pages do not stay at rest in the site cache."""
        c = world["client"]
        _render(c)
        cache = world["tmp"] / "site-cache"
        assert (cache / USER_KB / f"{ENTRY}.html").is_file()
        assert _set_role(c, USER_KB, "none").status_code == 200
        assert not (cache / USER_KB).exists()

    def test_mcp_scope_drops_the_kb_after_the_change(self, world):
        """`/mcp` resolves a connection's readable set with
        `mcp_routes._resolve_bearer_auth` on the app's own config object;
        the stale cache fed it too. A read-role user reads a KB whose
        default_role is read, and loses it when the KB is closed."""
        from starlette.requests import Request

        from pyrite.server.mcp_routes import _resolve_bearer_auth
        from tests.auth_seed import app_db, seed_user, sign_in

        c, app = world["client"], world["app"]
        assert _anon_rest(c).status_code == 200  # the app's PyriteDB exists now
        seed_user(app_db(app), "reader", role="read")
        token = sign_in(app, "reader", "password123")

        def readable():
            request = Request(
                {
                    "type": "http",
                    "headers": [(b"cookie", f"pyrite_session={token}".encode())],
                }
            )
            with app_db(app).request_handle() as db:
                return _resolve_bearer_auth(request, app.state.pyrite_config, db)["readable_kbs"]

        assert USER_KB in readable()
        assert _set_role(c, USER_KB, "none").status_code == 200
        scope = readable()
        assert USER_KB not in scope
        assert YAML_KB in scope


class TestRemovingARegistryKB:
    def test_its_site_pages_leave_the_disk_and_the_landing(self, world):
        c = world["client"]
        _render(c)
        cache = world["tmp"] / "site-cache"
        assert (cache / USER_KB).is_dir()
        assert c.delete(f"/api/kbs/{USER_KB}", headers=ADMIN).status_code == 200
        assert not (cache / USER_KB).exists()
        assert USER_KB not in c.get("/site").text
        assert _anon_rest(c).status_code == 404


class TestALandingRenderedUnderAnOlderPolicy:
    """A landing that lists a KB not public now is never served as it is,
    whatever path closed the KB -- here config.yaml edited and the server
    restarted, which no endpoint sees (A6-F6). Nor does the site go dark:
    the landing is rendered again on the request that finds it stale."""

    def test_a_kb_closed_outside_the_endpoint_leaves_the_landing(self, world):
        c = world["client"]
        _render(c)
        assert YAML_KB in c.get("/site").text
        world["config"].get_kb(YAML_KB).default_role = "none"
        r = c.get("/site")
        assert YAML_KB not in r.text
        assert r.headers["X-Pyrite-Cache"] == "HIT"
        assert USER_KB in r.text

    def test_a_cache_from_before_the_manifest_is_rendered_not_darkened(self, world):
        """Upgrading: a landing rendered by an earlier version carries no
        manifest. The first visit renders it; the site stays up."""
        c = world["client"]
        _render(c)
        manifest = world["tmp"] / "site-cache" / ".landing-kbs.json"
        manifest.unlink()
        r = c.get("/site")
        assert r.headers["X-Pyrite-Cache"] == "HIT"
        assert USER_KB in r.text and YAML_KB in r.text
        assert manifest.is_file()

    def test_a_landing_that_cannot_be_rendered_again_is_withheld(self, world, monkeypatch):
        """Fail closed: if the re-render fails, the stale page is not served."""
        from pyrite.services.site_cache import SiteCacheService

        c = world["client"]
        _render(c)
        world["config"].get_kb(YAML_KB).default_role = "none"

        def broken(self):
            raise OSError("read-only cache")

        monkeypatch.setattr(SiteCacheService, "render_landing", broken)
        r = c.get("/site")
        assert YAML_KB not in r.text
        assert r.headers["X-Pyrite-Cache"] == "MISS"

    @pytest.mark.control(reason="a current landing was always served")
    def test_a_current_landing_is_served(self, world):
        c = world["client"]
        _render(c)
        r = c.get("/site")
        assert r.headers["X-Pyrite-Cache"] == "HIT"
        assert USER_KB in r.text and YAML_KB in r.text


class TestOpeningARegistryKB:
    def test_none_to_read_takes_effect_at_once(self, world):
        c = world["client"]
        assert _set_role(c, USER_KB, "none").status_code == 200
        assert _anon_rest(c).status_code == 404
        assert _set_role(c, USER_KB, "read").status_code == 200
        assert _anon_rest(c).status_code == 200
        assert USER_KB in public_kb_names(world["config"])


class TestAConfigYamlKB:
    def test_the_endpoint_refuses_rather_than_reporting_success(self, world):
        c = world["client"]
        assert c.get("/api/entries/yaml-doc", params={"kb": YAML_KB}).status_code == 200
        with PyriteDB(world["config"].settings.index_path) as db:
            row_before = KBRegistryService(world["config"], db).registered_default_role(YAML_KB)
        r = _set_role(c, YAML_KB, "none")
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "KB_DEFINED_IN_CONFIG"
        assert "config.yaml" in detail["message"]
        # Nothing changed anywhere.
        assert c.get("/api/entries/yaml-doc", params={"kb": YAML_KB}).status_code == 200
        with PyriteDB(world["config"].settings.index_path) as db:
            row_after = KBRegistryService(world["config"], db).registered_default_role(YAML_KB)
        assert row_after == row_before == "read"
        assert world["config"].get_kb(YAML_KB).default_role == "read"

    @pytest.mark.control(
        reason="an unknown KB was always 404; pins that the refusal did not replace it"
    )
    def test_an_unknown_kb_is_still_404(self, world):
        assert _set_role(world["client"], "no-such-kb", "none").status_code == 404


class TestEveryRegistryWriteRefreshesTheConfigView:
    """Module surface: the config the policy reads equals the row after each write."""

    @pytest.fixture
    def reg(self, tmp_path):
        config = PyriteConfig(settings=Settings(index_path=tmp_path / "index.db"))
        db = PyriteDB(config.settings.index_path)
        db.merge_registered_kbs(config)
        yield KBRegistryService(config, db), config, tmp_path
        db.close()

    @pytest.mark.control(reason="add_kb already updated the cache; pinned so the refresh keeps it")
    def test_add_is_visible(self, reg):
        registry, config, tmp = reg
        registry.add_kb("k", str(tmp / "k"))
        assert config.get_kb("k") is not None

    def test_default_role_update_is_visible(self, reg):
        registry, config, tmp = reg
        registry.add_kb("k", str(tmp / "k"))
        registry.update_kb("k", default_role="read")
        assert config.get_kb("k").default_role == "read"
        assert public_kb_names(config) == ["k"]
        registry.update_kb("k", default_role="none")
        assert config.get_kb("k").default_role == "none"
        assert public_kb_names(config) == []

    def test_metadata_update_is_visible(self, reg):
        registry, config, tmp = reg
        registry.add_kb("k", str(tmp / "k"))
        registry.update_kb("k", description="new words", kb_type="software")
        kb = config.get_kb("k")
        assert (kb.description, kb.kb_type) == ("new words", "software")

    def test_remove_is_visible(self, reg):
        registry, config, tmp = reg
        registry.add_kb("k", str(tmp / "k"))
        registry.update_kb("k", default_role="read")
        registry.remove_kb("k")
        assert config.get_kb("k") is None
        assert "k" not in [kb.name for kb in config.all_kbs()]
        assert public_kb_names(config) == []

    def test_a_row_the_loader_now_refuses_is_not_kept_in_its_old_form(self, reg):
        """The refresh re-reads through the one registry loader; a row it
        refuses (here a path that no longer resolves) must not leave the old
        copy -- with its old, public, default_role -- behind."""
        registry, config, tmp = reg
        registry.add_kb("k", str(tmp / "k"))
        registry.update_kb("k", default_role="read")
        (tmp / "k").rmdir()
        (tmp / "k").symlink_to(tmp / "k")  # a loop: unresolvable
        registry.update_kb("k", default_role="none")
        assert "k" not in public_kb_names(config)
        assert config.get_kb("k") is None

    @pytest.mark.control(
        reason="add registered None already; pins that remove+add inherits no policy"
    )
    def test_a_removed_and_re_added_kb_is_not_public(self, reg):
        """The next KB under the same name must not inherit the old policy."""
        registry, config, tmp = reg
        registry.add_kb("k", str(tmp / "k"))
        registry.update_kb("k", default_role="read")
        registry.remove_kb("k")
        registry.add_kb("k", str(tmp / "k2"))
        assert public_kb_names(config) == []
