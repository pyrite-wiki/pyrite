"""Resolution uses complete indexed aliases, not substrings or LIKE patterns."""

import json

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import AmbiguousEntryError
from pyrite.services.access_policy import UNSCOPED
from pyrite.services.wikilink_service import WikilinkService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager


@pytest.fixture
def aliases(tmp_path):
    opened = []

    def build(first="Sync", second="Sync Weekly", *, legacy=False):
        kbs = []
        for name, alias in [("alpha", first), ("beta", second)]:
            folder = tmp_path / name
            folder.mkdir()
            fields = {"id": name, "title": f"A field report from {name}", "type": "note"}
            fields["metadata" if legacy else "aliases"] = (
                {"aliases": [alias]} if legacy else [alias]
            )
            (folder / f"{name}.md").write_text(
                "---\n" + json.dumps(fields) + "\n---\nA stub field report.\n", encoding="utf-8"
            )
            kbs.append(KBConfig(name=name, path=folder, kb_type="generic"))
        config = PyriteConfig(
            knowledge_bases=kbs,
            settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
        )
        db = PyriteDB(config.settings.index_path)
        opened.append(db)
        IndexManager(db, config).index_all()
        return WikilinkService(config, db)

    yield build
    for db in opened:
        db.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_complete_alias_wins_over_a_longer_alias(aliases, legacy):
    svc = aliases(legacy=legacy)
    assert svc.resolve_entry("sync", readable_kbs=UNSCOPED)["id"] == "alpha"
    assert svc.resolve_entry("Syn", readable_kbs=UNSCOPED) is None


@pytest.mark.parametrize("target", ["100%", "meeting_notes"])
def test_alias_percent_and_underscore_are_literal(aliases, target):
    svc = aliases(target, "100 percent" if target == "100%" else "meeting-notes")
    assert svc.resolve_entry(target, readable_kbs=UNSCOPED)["id"] == "alpha"


def test_same_exact_alias_is_still_ambiguous(aliases):
    svc = aliases("Sync", "Sync")
    with pytest.raises(AmbiguousEntryError):
        svc.resolve_entry("Sync", readable_kbs=UNSCOPED)


def test_scope_does_not_turn_a_partial_alias_into_an_exact_match(aliases):
    svc = aliases()
    assert svc.resolve_entry("Sync", readable_kbs={"beta"}) is None
    assert svc.resolve_entry("Sync", readable_kbs={"alpha"})["id"] == "alpha"
