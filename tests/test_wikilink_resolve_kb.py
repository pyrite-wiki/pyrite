"""Wikilink resolution by KB (#912): a shortname names its KB, and no pass
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


def test_a_shortname_in_kb_resolves_like_its_full_name(world):
    _, client = world
    for kb in ("kbb", "kb-b"):
        r = _resolve(client, "foo", kb=kb)
        assert r.json()["entry"]["kb_name"] == "kb-b", (kb, r.text)
    assert _resolve(client, "kbb:foo").json()["entry"]["kb_name"] == "kb-b"


def test_get_entry_accepts_a_shortname_kb(world):
    _, client = world
    r = client.get("/api/entries/foo", params={"kb": "kbb"})
    assert r.status_code == 200, r.text
    assert r.json()["kb_name"] == "kb-b"


def test_an_unknown_kb_is_still_a_miss(world):
    _, client = world
    assert _resolve(client, "foo", kb="nope").json()["resolved"] is False
    assert _resolve(client, "foo", kb="ka").json()["resolved"] is False  # foo is not in kb-a


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
    for kb, expect in (("ka", "kb-a"), ("kb-b", "kb-b"), ("kbb", "kb-b")):
        assert _resolve(client, target, kb=kb).json()["entry"]["kb_name"] == expect


def test_two_matches_in_one_kb_are_that_kbs_own(world):
    svc, client = world
    svc.create_entry("kb-b", "meeting-again", "Meeting Notes", "note", "x")
    r = _resolve(client, "Meeting Notes", kb="kb-b")
    assert r.json()["entry"]["id"] == "meeting-again"  # first by id, within the one KB
