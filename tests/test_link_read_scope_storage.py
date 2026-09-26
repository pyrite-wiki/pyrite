"""Link queries answer only over what the caller can read (P-R4, P-R5).

Private #39/#56 (link lists), #57 (graph), #40/#55 (lookup without a KB).
Surface: the storage layer and `KBService`/`GraphService`, driven with the
readable set `ReadScope.as_set()` hands a handler. The REST and MCP surfaces
have their own modules; this one pins the rule where it is enforced.

`readable_kbs=None` is the unscoped caller (a global admin, an operator key,
local stdio) and must see exactly what it saw before: every such test is a
control, marked as one.
"""

from __future__ import annotations

import json

import pytest

from pyrite.services.graph_service import GraphService
from pyrite.services.kb_service import KBService
from pyrite.storage.backends.overlay_backend import WorktreeDB
from pyrite.storage.database import PyriteDB
from tests.characterization.world import build_world
from tests.link_scope_seed import (
    MISSING_TARGET,
    PRIVATE,
    PRIVATE_ENTRY,
    PRIVATE_SPY,
    PRIVATE_SPY_TITLE,
    READ_ONLY,
    READABLE,
    READABLE_ENTRY,
    READABLE_POINTER,
    SHADOWED,
    seed_links,
)


@pytest.fixture(scope="module")
def w(tmp_path_factory):
    world = build_world(tmp_path_factory, label="link-scope-storage")
    seed_links(world)
    # The private note links on into the readable KB. A missing target has
    # no outlinks, so a walk that reaches the private note as a bare target
    # must not follow its links either.
    _raw_link(world.db, PRIVATE_ENTRY, PRIVATE, "readable-b", READABLE)
    world.db._raw_conn.commit()
    try:
        yield world
    finally:
        world.close()


@pytest.fixture(scope="module")
def scope(w):
    readable = set(w.principals["local_user"].readable_kbs)
    assert READABLE in readable and READ_ONLY in readable and PRIVATE not in readable
    return readable


def _raw_link(db, source, source_kb, target, target_kb, relation="wikilink"):
    db._raw_conn.execute(
        "INSERT INTO link (source_id, source_kb, target_id, target_kb, relation,"
        " inverse_relation) VALUES (?, ?, ?, ?, ?, 'wikilinked_by')",
        (source, source_kb, target, target_kb, relation),
    )


def _raw_entry(db, entry_id, kb_name, title):
    db._raw_conn.execute(
        "INSERT OR IGNORE INTO kb (name, path, kb_type) VALUES (?, ?, 'generic')",
        (kb_name, f"/test/{kb_name}"),
    )
    db._raw_conn.execute(
        "INSERT INTO entry (id, kb_name, entry_type, title, body, created_at, updated_at)"
        " VALUES (?, ?, 'note', ?, '', '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
        (entry_id, kb_name, title),
    )


def _without_id(row: dict) -> str:
    return json.dumps({k: v for k, v in row.items() if k != "id"}, sort_keys=True)


def _outlink(rows, target_id):
    [row] = [r for r in rows if r["id"] == target_id]
    return row


# -- backlinks ---------------------------------------------------------------


def test_backlinks_drop_sources_in_unreadable_kbs(w, scope):
    rows = w.db.get_backlinks(READABLE_ENTRY, READABLE, readable_kbs=scope)
    assert PRIVATE_SPY not in {r["id"] for r in rows}


@pytest.mark.control(reason="unscoped caller keeps every backlink, as before")
def test_backlinks_unscoped_keep_private_sources(w):
    rows = w.db.get_backlinks(READABLE_ENTRY, READABLE, readable_kbs=None)
    assert PRIVATE_SPY in {r["id"] for r in rows}


# -- outlinks ----------------------------------------------------------------


def test_outlink_to_private_target_reads_like_one_to_a_missing_target(w, scope):
    rows = w.db.get_outlinks(READABLE_POINTER, READABLE, readable_kbs=scope)
    private = _outlink(rows, PRIVATE_ENTRY)
    missing = _outlink(rows, MISSING_TARGET)
    assert _without_id(private) == _without_id(missing)
    assert private["title"] is None and private["entry_type"] is None


@pytest.mark.control(reason="unscoped caller still sees the private target's title")
def test_outlinks_unscoped_resolve_private_titles(w):
    rows = w.db.get_outlinks(READABLE_POINTER, READABLE, readable_kbs=None)
    assert _outlink(rows, PRIVATE_ENTRY)["title"] == "Private note"


# -- graph -------------------------------------------------------------------


def test_graph_centred_on_private_entry_matches_missing_centre(w, scope):
    private = w.db.get_graph_data(center=PRIVATE_SPY, center_kb=PRIVATE, readable_kbs=scope)
    missing = w.db.get_graph_data(center="nope", center_kb=PRIVATE, readable_kbs=scope)
    assert private == missing == {"nodes": [], "edges": []}


def test_graph_walk_never_enters_unreadable_rows(w, scope):
    data = w.db.get_graph_data(center=READABLE_ENTRY, center_kb=READABLE, readable_kbs=scope)
    assert PRIVATE_SPY_TITLE not in json.dumps(data)
    assert all(n["kb_name"] != PRIVATE or n["title"] == n["id"] for n in data["nodes"])
    [centre] = [n for n in data["nodes"] if n["id"] == READABLE_ENTRY]
    assert centre["link_count"] == 0


def test_graph_does_not_follow_links_out_of_an_unreadable_target(w, scope):
    data = w.db.get_graph_data(
        center=READABLE_POINTER, center_kb=READABLE, depth=3, readable_kbs=scope
    )
    assert not [e for e in data["edges"] if e["source_kb"] == PRIVATE]
    assert "readable-b" not in {n["id"] for n in data["nodes"]}


def test_graph_private_outlink_target_node_reads_like_missing(w, scope):
    data = w.db.get_graph_data(center=READABLE_POINTER, center_kb=READABLE, readable_kbs=scope)
    nodes = {n["id"]: n for n in data["nodes"]}
    assert _without_id(nodes[PRIVATE_ENTRY]) == _without_id(nodes[MISSING_TARGET]).replace(
        MISSING_TARGET, PRIVATE_ENTRY
    )


def test_full_graph_counts_only_readable_edges(w, scope):
    data = w.db.get_graph_data(readable_kbs=scope)
    assert {n["kb_name"] for n in data["nodes"]} <= scope
    # The readable note's only link comes from the private spy, so for this
    # caller it has no links at all: not a node, exactly as if the spy did
    # not exist.
    assert READABLE_ENTRY not in {n["id"] for n in data["nodes"]}
    assert all(n["link_count"] == 0 or data["edges"] for n in data["nodes"])
    assert any(e["source_id"] == "readable-a" for e in data["edges"])


@pytest.mark.control(reason="unscoped graph still walks into the private KB")
def test_graph_unscoped_unchanged(w):
    data = w.db.get_graph_data(center=READABLE_ENTRY, center_kb=READABLE, readable_kbs=None)
    assert PRIVATE_SPY in {n["id"] for n in data["nodes"]}
    full = w.db.get_graph_data(readable_kbs=None)
    [note] = [n for n in full["nodes"] if n["id"] == READABLE_ENTRY]
    assert note["link_count"] == 1


def test_graph_service_passes_scope(w, scope):
    data = GraphService(w.db).get_graph(center=PRIVATE_SPY, center_kb=PRIVATE, readable_kbs=scope)
    assert data == {"nodes": [], "edges": []}
    rows = GraphService(w.db).get_backlinks(READABLE_ENTRY, READABLE, readable_kbs=scope)
    assert PRIVATE_SPY not in {r["id"] for r in rows}
    out = GraphService(w.db).get_outlinks(READABLE_POINTER, READABLE, readable_kbs=scope)
    assert _without_id(_outlink(out, PRIVATE_ENTRY)) == _without_id(_outlink(out, MISSING_TARGET))


# -- KBService.get_entry -----------------------------------------------------


def test_get_entry_without_kb_skips_unreadable_twin(w, scope):
    entry = KBService(w.config, w.db).get_entry(SHADOWED, readable_kbs=scope)
    assert entry is not None and entry["kb_name"] == READ_ONLY


def test_get_entry_without_kb_private_only_is_a_miss(w, scope):
    svc = KBService(w.config, w.db)
    assert svc.get_entry(PRIVATE_SPY, readable_kbs=scope) is None


def test_get_entry_in_a_named_unreadable_kb_is_a_miss(w, scope):
    svc = KBService(w.config, w.db)
    assert svc.get_entry(PRIVATE_SPY, kb_name=PRIVATE, readable_kbs=scope) is None


def test_get_entry_links_are_scoped(w, scope):
    svc = KBService(w.config, w.db)
    note = svc.get_entry(READABLE_ENTRY, kb_name=READABLE, readable_kbs=scope)
    assert PRIVATE_SPY not in json.dumps(note["backlinks"])
    pointer = svc.get_entry(READABLE_POINTER, kb_name=READABLE, readable_kbs=scope)
    assert "Private note" not in json.dumps(pointer["outlinks"])


@pytest.mark.control(reason="unscoped lookup keeps config order and unfiltered links")
def test_get_entry_unscoped_unchanged(w):
    svc = KBService(w.config, w.db)
    assert svc.get_entry(SHADOWED)["kb_name"] == PRIVATE
    note = svc.get_entry(READABLE_ENTRY, kb_name=READABLE)
    assert PRIVATE_SPY in {r["id"] for r in note["backlinks"]}


# -- overlay (worktree reads) ------------------------------------------------


@pytest.fixture
def overlay(w, tmp_path):
    """Main index plus a per-user diff that has cross-KB links of its own.

    The diff is a real `PyriteDB`; rows are written to it directly so both
    halves of the overlay carry a private source and a private target, and
    each half's scoping shows on its own.
    """
    diff = PyriteDB(tmp_path / "diff.db")
    _raw_entry(diff, "diff-spy", PRIVATE, "Diff spy dossier")
    _raw_link(diff, "diff-spy", PRIVATE, READABLE_ENTRY, READABLE)
    _raw_entry(diff, READABLE_ENTRY, READABLE, "Readable note")
    _raw_entry(diff, "diff-pointer", READABLE, "Diff pointer")
    _raw_entry(diff, "diff-private-target", PRIVATE, "Diff private target")
    _raw_link(diff, "diff-pointer", READABLE, "diff-private-target", PRIVATE)
    _raw_link(diff, "diff-pointer", READABLE, "diff-missing-target", PRIVATE)
    diff._raw_conn.commit()
    try:
        yield WorktreeDB(w.db, diff)
    finally:
        diff.close()


def test_overlay_links_are_scoped(w, scope, overlay):
    rows = overlay.get_backlinks(READABLE_ENTRY, READABLE, readable_kbs=scope)
    assert PRIVATE_SPY not in {r["id"] for r in rows}
    out = overlay.get_outlinks(READABLE_POINTER, READABLE, readable_kbs=scope)
    assert _without_id(_outlink(out, PRIVATE_ENTRY)) == _without_id(_outlink(out, MISSING_TARGET))


def test_overlay_diff_links_are_scoped(w, scope, overlay):
    rows = overlay.get_backlinks(READABLE_ENTRY, READABLE, readable_kbs=scope)
    assert "diff-spy" not in {r["id"] for r in rows}
    out = overlay.get_outlinks("diff-pointer", READABLE, readable_kbs=scope)
    assert _without_id(_outlink(out, "diff-private-target")) == _without_id(
        _outlink(out, "diff-missing-target")
    )


def test_overlay_get_entry_links_are_scoped(w, scope, overlay):
    note = KBService(w.config, overlay).get_entry(
        READABLE_ENTRY, kb_name=READABLE, readable_kbs=scope
    )
    assert PRIVATE_SPY not in {r["id"] for r in note["backlinks"]}


@pytest.mark.control(reason="unscoped overlay read keeps private backlinks")
def test_overlay_unscoped_unchanged(w, overlay):
    rows = overlay.get_backlinks(READABLE_ENTRY, READABLE)
    assert {PRIVATE_SPY, "diff-spy"} <= {r["id"] for r in rows}
    out = overlay.get_outlinks("diff-pointer", READABLE)
    assert _outlink(out, "diff-private-target")["title"] == "Diff private target"
