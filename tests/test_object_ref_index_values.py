"""An object-ref value indexes as the entry it names, in four spellings (#1693, slice 1).

The property: a value in a field kb.yaml declares ``object-ref`` is indexed as
the entry it names. ``x`` and ``[[x]]`` give one ``entry_ref`` row with target
``(x, this KB)``; ``other:x`` and ``[[other:x]]`` give one row with target
``(x, <the KB the prefix "other" resolves to>)``, by the rule wikilinks use
(full name, then shortname). A prefix is a KB only when it names a registered
KB, the check ``resolve_entry`` makes; otherwise the colon is part of the id and
the whole value (``doi:10.1000-x``, ``https://ex.org/x``) is the id, in this KB,
as on dev. Lists and scalars alike. A full rebuild and an incremental sync give the same rows.

Expected rows are written by hand from that property, not computed by the code.
"""

from __future__ import annotations

import sqlite3

import pytest

from pyrite.config import KBConfig
from pyrite.storage.index import IndexManager
from tests._world import build_world

KB_YAML = """name: {name}
types:
  beat:
    fields: {{}}
  finding:
    fields:
      hubs: {{type: object-ref, target_type: beat}}
      related: {{type: object-ref}}
"""

HUB = "---\nid: x\ntitle: Hub X\ntype: beat\n---\nHub in {kb}.\n"


def _finding(entry_id: str, value: str) -> str:
    """A finding whose two object-ref fields (one with target_type, one
    without) carry the same raw YAML ``value``."""
    return (
        f"---\nid: {entry_id}\ntitle: {entry_id}\ntype: finding\n"
        f"hubs: {value}\nrelated: {value}\n---\nBody.\n"
    )


# (entry id, raw YAML value, expected targets as (target_id, target_kb)).
# Each target applies to both fields: ``hubs`` (target_type beat) and ``related``.
MAIN, OTHER = "main", "other-kb"
CASES = [
    ("scalar-plain", "x", [("x", MAIN)]),
    ("scalar-wiki", "'[[x]]'", [("x", MAIN)]),
    ("scalar-prefixed", "other-kb:x", [("x", OTHER)]),
    ("scalar-wiki-prefixed", "'[[other-kb:x]]'", [("x", OTHER)]),
    ("scalar-shortname", "oth:x", [("x", OTHER)]),
    ("scalar-wiki-shortname", "'[[oth:x]]'", [("x", OTHER)]),
    ("scalar-spaces", "'[[ x ]]'", [("x", MAIN)]),
    ("scalar-spaces-prefixed", "'[[ other-kb:x ]]'", [("x", OTHER)]),
    ("list-plain", "[x]", [("x", MAIN)]),
    ("list-wiki", "['[[x]]']", [("x", MAIN)]),
    ("list-prefixed", "[other-kb:x]", [("x", OTHER)]),
    ("list-wiki-prefixed", "['[[other-kb:x]]']", [("x", OTHER)]),
    ("list-mixed", "[x, '[[x]]', 'other-kb:x', '[[other-kb:x]]']", [("x", MAIN), ("x", OTHER)]),
    # A prefix is a KB only when it names a registered KB (the check
    # `resolve_entry` makes). Otherwise the colon is part of the id, as on dev:
    # the whole value is the id, in the source KB.
    ("scalar-unknown-prefix", "nope:x", [("nope:x", MAIN)]),
    ("scalar-wiki-unknown-prefix", "'[[nope:x]]'", [("nope:x", MAIN)]),
    ("scalar-doi", "doi:10.1000-x", [("doi:10.1000-x", MAIN)]),
    ("scalar-wiki-doi", "'[[doi:10.1000-x]]'", [("doi:10.1000-x", MAIN)]),
    ("scalar-url", "'https://ex.org/x'", [("https://ex.org/x", MAIN)]),
    (
        "list-doi-and-url",
        "['doi:10.1000-x', 'https://ex.org/x']",
        [("doi:10.1000-x", MAIN), ("https://ex.org/x", MAIN)],
    ),
    # No name, no row.
    ("scalar-empty-brackets", "'[[]]'", []),
    ("scalar-blank-brackets", "'[[  ]]'", []),
    ("list-empty-brackets", "['[[]]']", []),
    ("list-empty-and-real", "['[[]]', '[[x]]']", [("x", MAIN)]),
]


def _expected(entry_id: str) -> list[tuple[str, str, str, str]]:
    """(source, field, target_id, target_kb) rows for one case, by hand."""
    targets = next(t for i, _v, t in CASES if i == entry_id)
    return sorted((entry_id, f, tid, tkb) for f in ("hubs", "related") for tid, tkb in targets)


def _write_kbs(root, findings: dict[str, str]) -> list[KBConfig]:
    main_dir, other_dir = root / "main", root / "other"
    for name, path in ((MAIN, main_dir), (OTHER, other_dir)):
        path.mkdir(parents=True, exist_ok=True)
        (path / "kb.yaml").write_text(KB_YAML.format(name=name), encoding="utf-8")
        (path / "x.md").write_text(HUB.format(kb=name), encoding="utf-8")
    for entry_id, value in findings.items():
        (main_dir / f"{entry_id}.md").write_text(_finding(entry_id, value), encoding="utf-8")
    return [
        KBConfig(name=MAIN, path=main_dir, kb_type="generic"),
        KBConfig(name=OTHER, path=other_dir, kb_type="generic", shortname="oth"),
    ]


def _rows(world) -> list[tuple[str, str, str, str]]:
    path = world.config.settings.index_path
    with sqlite3.connect(path) as conn:
        return sorted(
            conn.execute(
                "SELECT source_id, field_name, target_id, target_kb FROM entry_ref "
                "WHERE source_kb = ?",
                (MAIN,),
            ).fetchall()
        )


@pytest.fixture
def world(tmp_path):
    kbs = _write_kbs(tmp_path, {i: v for i, v, _t in CASES})
    w = build_world(tmp_path / "w", kbs=kbs, rest=False, settings={"auto_embed": False})
    yield w
    w.close()


@pytest.mark.parametrize("entry_id", [c[0] for c in CASES])
def test_each_spelling_indexes_as_the_entry_it_names(world, entry_id):
    got = [r for r in _rows(world) if r[0] == entry_id]
    assert got == _expected(entry_id)


def test_get_refs_to_returns_the_source_in_each_case(world):
    in_main = {r["id"] for r in world.db.get_refs_to("x", MAIN) if r["kb_name"] == MAIN}
    in_other = {r["id"] for r in world.db.get_refs_to("x", OTHER)}
    want_main = {i for i, _v, t in CASES if ("x", MAIN) in t}
    want_other = {i for i, _v, t in CASES if ("x", OTHER) in t}
    assert in_main == want_main
    assert in_other == want_other
    # An unregistered prefix is part of the id: these name `nope:x`, `doi:...`,
    # not `x`; the empty brackets name nothing.
    unnamed = {"scalar-unknown-prefix", "scalar-doi", "scalar-url", "scalar-empty-brackets"}
    assert not (unnamed & (in_main | in_other))
    # ...and they are found by the id they do name.
    for target, source in (("nope:x", "scalar-unknown-prefix"), ("doi:10.1000-x", "scalar-doi")):
        assert {r["id"] for r in world.db.get_refs_to(target, MAIN)} >= {source}


def test_get_refs_from_names_the_entry_not_the_spelling(world):
    got = world.db.get_refs_from("scalar-wiki-prefixed", MAIN)
    assert sorted((r["id"], r["kb_name"], r["title"]) for r in got) == [
        ("x", OTHER, "Hub X"),
        ("x", OTHER, "Hub X"),
    ]


def test_a_full_rebuild_equals_an_incremental_sync(tmp_path):
    # Session A: every finding names `x` plainly, then each is hand-edited to its
    # spelling and picked up by `sync_incremental`.
    kbs = _write_kbs(tmp_path, {i: "x" for i, _v, _t in CASES})
    a = build_world(tmp_path / "a", kbs=kbs, rest=False, settings={"auto_embed": False})
    try:
        for entry_id, value, _t in CASES:
            (tmp_path / "main" / f"{entry_id}.md").write_text(
                _finding(entry_id, value), encoding="utf-8"
            )
        IndexManager(a.db, a.config).sync_incremental(MAIN)
        incremental = _rows(a)
    finally:
        a.close()
    # Session B: a fresh index of the same files.
    b = build_world(tmp_path / "b", kbs=kbs, rest=False, settings={"auto_embed": False})
    try:
        rebuilt = _rows(b)
    finally:
        b.close()
    want = sorted(r for i, _v, _t in CASES for r in _expected(i))
    assert rebuilt == want
    assert incremental == want
