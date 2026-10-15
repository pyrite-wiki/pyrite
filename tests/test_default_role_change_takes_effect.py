"""A KB's `default_role` has one source of truth, applied on the next request.

`PUT /api/kbs/{name}/default-role` wrote the registry row, but the access
policy read the in-memory config first, and the
config's cache of registry KBs (`PyriteConfig._db_kb_cache`) was filled once
and never refreshed. A KB an admin closed stayed readable by anonymous REST
(and, until #953 removed it, the public `/site`) until a restart. For a KB
defined in config.yaml the endpoint reported success and changed nothing, ever.

The properties pinned here (kb/designs/multi-user-threat-model.md P-S1, P-S3,
P-M1, P-F6):

- a change through the endpoint takes effect on the next request on every
  surface -- anonymous REST and the MCP scope;
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
from pyrite.storage.database import PyriteDB

USER_KB = "open-kb"  # registered in the index, not in config.yaml
YAML_KB = "yaml-kb"  # defined in config.yaml
ENTRY = "doc-1"
BODY = "OCELOT-BODY-TEXT"
KEY = "operator-key"
ADMIN = {"X-API-Key": KEY}


def _anonymous_reads(config, name: str, anonymous_tier: str = "read") -> bool:
    """What the access policy gives an anonymous visitor on `name`: the one
    rule (`kb_role`, step 5) every anonymous surface resolves through."""
    from pyrite.services.access_policy import kb_role

    kb = config.get_kb(name)
    if kb is None:
        return False
    return kb_role(None, None, lambda: None, kb.default_role, anonymous_tier) is not None


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
        assert _anon_rest(world["client"]).status_code == 200

    def test_anonymous_rest_is_404_after_the_change(self, world):
        c = world["client"]
        assert _anon_rest(c).status_code == 200
        assert _set_role(c, USER_KB, "none").status_code == 200
        r = _anon_rest(c)
        assert r.status_code == 404, r.text
        assert BODY not in r.text

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
    def test_anonymous_rest_loses_it_at_once(self, world):
        c = world["client"]
        assert _anon_rest(c).status_code == 200
        assert c.delete(f"/api/kbs/{USER_KB}", headers=ADMIN).status_code == 200
        assert _anon_rest(c).status_code == 404


class TestOpeningARegistryKB:
    def test_none_to_read_takes_effect_at_once(self, world):
        c = world["client"]
        assert _set_role(c, USER_KB, "none").status_code == 200
        assert _anon_rest(c).status_code == 404
        assert _set_role(c, USER_KB, "read").status_code == 200
        assert _anon_rest(c).status_code == 200
        assert _anonymous_reads(world["config"], USER_KB)


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
        assert _anonymous_reads(config, "k")
        registry.update_kb("k", default_role="none")
        assert config.get_kb("k").default_role == "none"
        assert not _anonymous_reads(config, "k")

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
        assert not _anonymous_reads(config, "k")

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
        assert not _anonymous_reads(config, "k")
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
        assert config.get_kb("k").default_role is None
        assert not _anonymous_reads(config, "k", anonymous_tier=None)
