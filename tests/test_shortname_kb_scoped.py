"""A shortname is link syntax only; everywhere else the full name (#952, ADR-0049 Q3).

#912 mapped a shortname `kb` to its KB at the REST boundary (authz, the write
tier, the overlay resolver, `KBService.get_entry`). The maintainer reversed
that on 2026-10-15: `[[short:id]]` keeps working, and a `kb` parameter, a
`-k`, a `kb_name` argument or a URL takes the **full name only**. A shortname
there is refused with `KB_NOT_FOUND` and, when the caller may read the KB it
names, `did_you_mean` carries the full name. A private KB's shortname answers
as an unknown name does. The rule is `services/kb_resolver.py`; this file
pins it on REST, where #912's mapping lived.
"""

import logging
import subprocess

import pytest
from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app, get_config, get_db
from pyrite.services.kb_resolver import readable_kb_named
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from tests.auth_seed import seed_and_sign_in


@pytest.fixture
def world(tmp_path):
    for n in ("pub", "priv", "beta", "alpha"):
        (tmp_path / n).mkdir()
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name="pub", path=tmp_path / "pub", default_role="read", shortname="pb"),
            KBConfig(name="priv", path=tmp_path / "priv", default_role="none", shortname="pv"),
            KBConfig(name="beta", path=tmp_path / "beta", default_role="read"),
            # A private KB whose shortname is another KB's full name.
            KBConfig(name="alpha", path=tmp_path / "alpha", default_role="none", shortname="beta"),
        ],
        settings=Settings(
            index_path=tmp_path / "index.db",
            auth=AuthConfig(enabled=True, allow_registration=True, anonymous_tier="read"),
        ),
    )
    app = create_app(config=config)
    db = PyriteDB(config.settings.index_path)
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_db] = lambda: db
    svc = KBService(config, db)
    svc.create_entry("pub", "foo", "Foo", "note", "pub body")
    svc.create_entry("priv", "foo", "Foo", "note", "SECRET body")
    svc.create_entry("beta", "x", "X in beta", "note", "beta body")
    svc.create_entry("alpha", "x", "X in alpha", "note", "ALPHA SECRET")
    client = TestClient(app, base_url="http://localhost")
    seed_and_sign_in(client, "peer", role="read")
    yield config, client
    db.close()


def _get(client, path, **params):
    return client.get(path, params=params)


ROUTES = (("/api/entries/foo", {}), ("/api/entries/resolve", {"target": "foo"}))


def test_the_full_name_is_read(world):
    _, client = world
    r = _get(client, "/api/entries/foo", kb="pub")
    assert r.status_code == 200, r.text
    assert (r.json()["kb_name"], r.json()["body"].strip()) == ("pub", "pub body")


@pytest.mark.parametrize(("route", "extra"), ROUTES)
def test_a_readable_kbs_shortname_is_refused_with_the_full_name(world, route, extra):
    _, client = world
    r = _get(client, route, kb="pb", **extra)
    assert r.status_code == 404, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "KB_NOT_FOUND"
    assert detail["did_you_mean"] == ["pub"]
    assert "pub" in detail["hint"] and "[[pb:" in detail["hint"]
    assert "pub body" not in r.text


def test_an_anonymous_user_gets_the_same_refusal(world):
    _, client = world
    anon = TestClient(client.app, base_url="http://localhost")
    r = _get(anon, "/api/entries/foo", kb="pb")
    assert r.status_code == 404 and r.json()["detail"]["did_you_mean"] == ["pub"], r.text


def test_a_write_names_the_full_name_too(world):
    """The write tier resolves the same way: a read-only user naming a
    readable KB's shortname is told the full name, never 403 or 'created'."""
    _, client = world
    r = client.post("/api/entries", json={"kb": "pb", "title": "T", "entry_type": "note"})
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["did_you_mean"] == ["pub"]


@pytest.mark.parametrize(("route", "extra"), ROUTES)
def test_a_private_kbs_shortname_answers_as_an_unknown_name_does(world, route, extra):
    _, client = world
    private = _get(client, route, kb="pv", **extra)
    unknown = _get(client, route, kb="zz", **extra)
    assert private.status_code == unknown.status_code == 404, (route, private.text)
    assert private.text.replace("pv", "zz") == unknown.text
    assert "SECRET" not in private.text and "priv" not in private.text


def test_a_full_name_wins_over_another_kbs_shortname(world):
    config, client = world
    r = _get(client, "/api/entries/x", kb="beta")
    assert r.status_code == 200 and "beta body" in r.json()["body"], r.text
    r = _get(client, "/api/entries/resolve", target="x", kb="beta")
    assert r.json()["entry"]["kb_name"] == "beta"
    # In link syntax the full name wins as well (the unscoped caller; a
    # scoped one is looked up among its readable KBs only).
    assert readable_kb_named(config, "beta", None).name == "beta"
    r = _get(client, "/api/entries/resolve", target="beta:x")
    assert r.json()["entry"]["kb_name"] == "beta"


def test_link_syntax_still_maps_a_shortname(world):
    """`[[pb:foo]]` means pub's foo (ADR-0049 Q3): the prefix is the one
    place a shortname is a name."""
    config, client = world
    assert readable_kb_named(config, "pb", None).name == "pub"
    assert not hasattr(config, "kb_name_for")  # the config maps nothing itself
    r = _get(client, "/api/entries/resolve", target="pb:foo")
    assert r.status_code == 200, r.text
    assert r.json()["entry"]["kb_name"] == "pub"


def test_a_colliding_shortname_is_reported_and_warned(world, caplog):
    config, _ = world
    assert any("beta" in m for m in config.validate()["shortnames"])
    with caplog.at_level(logging.WARNING, logger="pyrite.config"):
        config._rebuild_index()
    assert "the full name wins" in caplog.text


def test_a_registry_kbs_name_that_is_a_config_shortname_is_reported(world, caplog):
    """#949 item 3: `kb add`-registered KBs live in the registry cache, not
    `knowledge_bases`, and the collision check used to look only at the
    latter. The full name still wins (`readable_kb_named`)."""
    config, _ = world
    with caplog.at_level(logging.WARNING, logger="pyrite.config"):
        added = config.register_db_kbs(
            [{"name": "pb", "path": "/nonexistent/pb", "kb_type": "generic"}]
        )
    assert added == 1
    assert any("'pb'" in m and "pub" in m for m in config.validate()["shortnames"])
    assert "'pb's full name" in caplog.text and "the full name wins" in caplog.text
    assert readable_kb_named(config, "pb", None).name == "pb"


def test_the_overlay_takes_the_full_name_only(tmp_path):
    """The worktree resolver no longer maps a shortname: it is handed the
    full name the route resolved, and a shortname reaches no worktree."""
    from pyrite.server.worktree_resolver import WorktreeResolver
    from tests.test_get_reads_the_file import ENTRY, ORIGINAL, _index
    from tests.test_worktree_service import _clean_git_env, _init_git_repo

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    kb_path = repo / "kb"
    (kb_path / "notes").mkdir(parents=True)
    (kb_path / "notes" / f"{ENTRY}.md").write_text(ORIGINAL)
    for cmd in (["git", "add", "."], ["git", "commit", "-qm", "kb"]):
        subprocess.run(
            cmd, cwd=repo, env=_clean_git_env(), check=True, capture_output=True, timeout=120
        )
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="demo", path=kb_path, kb_type="generic", shortname="dm")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    _index(config)
    db = PyriteDB(config.settings.index_path)
    cache: dict = {}
    ann = {"id": 7, "username": "ann"}
    try:
        resolver = WorktreeResolver(config, db, cache)
        resolver.get_write_service("demo", ann).update(ENTRY, "demo", {"body": "Ann's pending."})
        by_name = resolver.get_read_service("demo", ann).get_entry(ENTRY, "demo", readable_kbs=None)
        assert by_name["body"] == "Ann's pending."
        assert resolver.get_read_db("dm", ann) is db, "a shortname names no worktree"
    finally:
        for d in cache.values():
            d.close()
        db.close()
