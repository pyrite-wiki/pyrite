"""Wikilink resolution by KB (#912, reversed for `kb` by #952: a shortname is
link syntax only), and no pass
(id, title, alias) silently picks between twins in two KBs."""

import tempfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.server.api import create_app, get_config, get_db
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB


@pytest.fixture
def world():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        kbs = []
        for name, short in (("kb-a", "ka"), ("kb-b", "kbb")):
            (root / name).mkdir()
            kbs.append(
                KBConfig(name=name, path=root / name, kb_type=KBType.GENERIC, shortname=short)
            )
        config = PyriteConfig(knowledge_bases=kbs, settings=Settings(index_path=root / "i.db"))
        db = PyriteDB(root / "i.db")
        svc = KBService(config, db)
        svc.create_entry("kb-b", "foo", "Foo only in b", "note", "body b")
        for kb in ("kb-a", "kb-b"):
            svc.create_entry(kb, f"meeting-{kb}", "Meeting Notes", "note", kb)
            # The alias pass reads metadata.aliases of the index row.
            db.upsert_entry(
                {
                    "id": f"minutes-{kb}",
                    "kb_name": kb,
                    "entry_type": "note",
                    "title": f"Minutes {kb}",
                    "body": kb,
                    "metadata": {"aliases": ["Sync"]},
                    "tags": [],
                    "sources": [],
                    "links": [],
                }
            )
        app = create_app(config)
        app.dependency_overrides[get_config] = lambda: config
        app.dependency_overrides[get_db] = lambda: db
        yield svc, TestClient(app, base_url="http://localhost")
        db.close()


def _resolve(client, target, **params):
    return client.get("/api/entries/resolve", params={"target": target, **params})


def test_a_shortname_kb_is_refused_but_the_link_prefix_maps(world):
    """A shortname is link syntax only (#952, ADR-0049 Q3): `kb=kbb` is
    KB_NOT_FOUND with the full name suggested; `kbb:foo` still means kb-b."""
    _, client = world
    assert _resolve(client, "foo", kb="kb-b").json()["entry"]["kb_name"] == "kb-b"
    r = _resolve(client, "foo", kb="kbb")
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "KB_NOT_FOUND"
    assert r.json()["detail"]["did_you_mean"] == ["kb-b"]
    assert _resolve(client, "kbb:foo").json()["entry"]["kb_name"] == "kb-b"


def test_get_entry_refuses_a_shortname_kb(world):
    _, client = world
    r = client.get("/api/entries/foo", params={"kb": "kbb"})
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["did_you_mean"] == ["kb-b"]
    assert client.get("/api/entries/foo", params={"kb": "kb-b"}).json()["kb_name"] == "kb-b"


def test_an_unknown_kb_is_refused_not_a_miss(world):
    """A `kb` that is not a KB is KB_NOT_FOUND (#952); a KB that does not
    hold the target is a miss."""
    _, client = world
    r = _resolve(client, "foo", kb="nope")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "KB_NOT_FOUND", r.text
    assert _resolve(client, "foo", kb="kb-a").json()["resolved"] is False  # foo is not in kb-a


@pytest.mark.parametrize("target", ["Meeting Notes", "Sync"])
def test_a_title_or_alias_held_by_two_kbs_is_refused(world, target):
    _, client = world
    r = _resolve(client, target)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "AMBIGUOUS"
    assert detail["candidate_kbs"] == ["kb-a", "kb-b"]


@pytest.mark.parametrize("target", ["Meeting Notes", "Sync"])
def test_naming_the_kb_picks_the_twin(world, target):
    _, client = world
    for kb in ("kb-a", "kb-b"):
        assert _resolve(client, target, kb=kb).json()["entry"]["kb_name"] == kb


def test_two_matches_in_one_kb_are_that_kbs_own(world):
    svc, client = world
    svc.create_entry("kb-b", "meeting-again", "Meeting Notes", "note", "x")
    r = _resolve(client, "Meeting Notes", kb="kb-b")
    assert r.json()["entry"]["id"] == "meeting-again"  # first by id, within the one KB


@pytest.mark.parametrize("private_first", [True, False])
def test_a_shared_shortname_prefix_is_the_readable_kb(private_first):
    """A link prefix is looked up among the caller's readable KBs only (#952,
    #965 round 2): with a private `priv` and a readable `pub` sharing the
    shortname `s`, `s:e1` is pub's entry whichever KB config.yaml lists
    first. Looked up across all KBs and then checked, priv would be the one
    found and the answer would be None."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        names = ("priv", "pub") if private_first else ("pub", "priv")
        kbs = []
        for name in names:
            (root / name).mkdir()
            kbs.append(KBConfig(name=name, path=root / name, kb_type=KBType.GENERIC, shortname="s"))
        config = PyriteConfig(knowledge_bases=kbs, settings=Settings(index_path=root / "i.db"))
        db = PyriteDB(root / "i.db")
        svc = KBService(config, db)
        for name in names:
            svc.create_entry(name, "e1", f"E1 in {name}", "note", name)
        try:
            found = svc.resolve_entry("s:e1", readable_kbs={"pub"})
            assert found is not None and found["kb_name"] == "pub"
            # The scoped caller's other link form, `kb_name`, is the full name
            # only; a shortname given there is not a readable KB's name.
            assert svc.resolve_entry("e1", kb_name="pub", readable_kbs={"pub"})["kb_name"] == "pub"
        finally:
            db.close()


@pytest.mark.parametrize("private_first", [True, False])
def test_a_private_kb_named_like_a_readable_shortname_does_not_change_a_link(private_first):
    """The lookup is among the caller's readable KBs by full name and by
    shortname (#965 round 3): readable `team` has shortname `notes`;
    `notes:e1` is team's entry whether or not a private KB named `notes`
    exists. Looked up across all KBs by name first, the private KB would
    make the answer None, and tell a scoped caller the name exists."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        kbs = {}
        for name, short in (("notes", None), ("team", "notes")):
            (root / name).mkdir()
            kbs[name] = KBConfig(
                name=name, path=root / name, kb_type=KBType.GENERIC, shortname=short
            )
        order = ["notes", "team"] if private_first else ["team", "notes"]
        config = PyriteConfig(
            knowledge_bases=[kbs[n] for n in order], settings=Settings(index_path=root / "i.db")
        )
        db = PyriteDB(root / "i.db")
        svc = KBService(config, db)
        for name in order:
            svc.create_entry(name, "e1", f"E1 in {name}", "note", name)
        try:
            found = svc.resolve_entry("notes:e1", readable_kbs={"team"})
            assert found is not None and found["kb_name"] == "team"
            # The unscoped caller: the full name wins over the shortname.
            assert svc.resolve_entry("notes:e1", readable_kbs=None)["kb_name"] == "notes"
        finally:
            db.close()
