"""A shortname `kb` is authorized, routed and read as the KB it names (#912).

Authorization used to check the raw string, so a signed-in `read` user (or an
anonymous one) got KB_NOT_FOUND for `kb=<shortname>` of a KB they can read; and
the overlay resolver took the raw string, so a worktree was missed. One mapping,
`PyriteConfig.kb_name_for`, now sits at the boundary (authz, the write tier, the
resolver); a full name wins over another KB's shortname.
"""

import logging
import subprocess

import pytest
from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app, get_config, get_db
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


def test_a_scoped_user_reads_a_readable_kb_by_shortname(world):
    _, client = world
    for kb in ("pub", "pb"):
        r = _get(client, "/api/entries/foo", kb=kb)
        assert r.status_code == 200, (kb, r.text)
        assert (r.json()["kb_name"], r.json()["body"].strip()) == ("pub", "pub body")
        r = _get(client, "/api/entries/resolve", target="foo", kb=kb)
        assert r.json()["entry"]["kb_name"] == "pub", (kb, r.text)


def test_an_anonymous_user_reads_a_readable_kb_by_shortname(world):
    config, _ = world
    anon = TestClient(world[1].app, base_url="http://localhost")
    r = _get(anon, "/api/entries/foo", kb="pb")
    assert r.status_code == 200, r.text


def test_a_private_kbs_shortname_answers_as_an_unknown_name_does(world):
    _, client = world
    for route, extra in (("/api/entries/foo", {}), ("/api/entries/resolve", {"target": "foo"})):
        private = _get(client, route, kb="pv", **extra)
        unknown = _get(client, route, kb="zz", **extra)
        assert private.status_code == unknown.status_code == 404, (route, private.text)
        assert private.text.replace("pv", "zz") == unknown.text
        assert "SECRET" not in private.text and "priv" not in private.text


def test_a_full_name_wins_over_another_kbs_shortname(world):
    config, client = world
    assert config.kb_name_for("beta") == "beta"
    assert config.kb_name_for("pb") == "pub"
    assert config.kb_name_for("nope") == "nope"
    r = _get(client, "/api/entries/x", kb="beta")
    assert r.status_code == 200 and "beta body" in r.json()["body"], r.text
    r = _get(client, "/api/entries/resolve", target="x", kb="beta")
    assert r.json()["entry"]["kb_name"] == "beta"
    r = _get(client, "/api/entries/resolve", target="beta:x")
    assert r.json()["entry"]["kb_name"] == "beta"


def test_a_colliding_shortname_is_reported_and_warned(world, caplog):
    config, _ = world
    assert any("beta" in m for m in config.validate()["shortnames"])
    with caplog.at_level(logging.WARNING, logger="pyrite.config"):
        config._rebuild_index()
    assert "the full name wins" in caplog.text


def test_the_overlay_follows_a_shortname_to_the_users_worktree(tmp_path):
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
    ann, bob = {"id": 7, "username": "ann"}, {"id": 8, "username": "bob"}
    try:
        resolver = WorktreeResolver(config, db, cache)
        # Written through the shortname: the worktree is demo's.
        resolver.get_write_service("dm", ann).update(ENTRY, "demo", {"body": "Ann's pending."})

        def read(user):
            return resolver.get_read_service("dm", user).get_entry("multi", "dm", readable_kbs=None)

        assert read(ann)["body"] == "Ann's pending."
        assert read(bob)["body"] != "Ann's pending."  # no worktree: main
    finally:
        for d in cache.values():
            d.close()
        db.close()
