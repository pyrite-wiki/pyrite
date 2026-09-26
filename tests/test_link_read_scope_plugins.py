"""Plugin tools learn nothing about a KB the caller cannot read (P-R4, P-R5).

Private #39 names `zettel_graph`, `investigation_network`, `cascade_network`
and the software-kb tools that read link data (`sw_check_ready`,
`sw_context_for_item`, `sw_epic_detail`, ...). Each takes the named KB
through the MCP chokepoint, which checks it, but the links it follows lead
into other KBs; and the software-kb helpers looked up each link's far end
with `db.get_entry`, reading a private item's title and status.

The rule on this surface is the storage one: a source the caller cannot
read is dropped, and a target it cannot read reads exactly like a missing
one -- for a blocker that means "status unknown, unresolved", the same
answer a dangling `blocked_by` gets. Surface: the real `_dispatch_tool`
chokepoint, with `local_user`'s readable set; `None` is the unscoped
control.
"""

from __future__ import annotations

import json

import pytest

from tests.characterization.world import build_world
from tests.link_scope_seed import (
    MISSING_TARGET,
    PRIVATE,
    PRIVATE_ENTRY,
    PRIVATE_SPY,
    PRIVATE_SPY_TITLE,
    READABLE,
    READABLE_ENTRY,
    READABLE_POINTER,
    seed_links,
)

PRIVATE_TITLE_MARK = "Private sw"


def _insert(db, kb, entry_id, title, status, meta, entry_type="backlog_item"):
    db._raw_conn.execute(
        "INSERT INTO entry (id, kb_name, entry_type, title, body, status, priority, metadata,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, '', ?, ?, ?,"
        " '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
        (entry_id, kb, entry_type, title, status, meta.get("priority"), json.dumps(meta)),
    )


def _link(db, source, source_kb, target, target_kb, relation, inverse):
    db._raw_conn.execute(
        "INSERT INTO link (source_id, source_kb, target_id, target_kb, relation,"
        " inverse_relation) VALUES (?, ?, ?, ?, ?, ?)",
        (source, source_kb, target, target_kb, relation, inverse),
    )


def _seed_software(db):
    feature = {"kind": "feature", "effort": "S"}
    # Readable items.
    _insert(db, READABLE, "sw-item", "Readable item", "accepted", {**feature, "priority": "high"})
    _insert(
        db, READABLE, "sw-item2", "Readable item 2", "accepted", {**feature, "priority": "critical"}
    )
    _insert(db, READABLE, "sw-free", "Readable free", "accepted", {**feature, "priority": "low"})
    _insert(db, READABLE, "sw-epic", "Readable epic", "accepted", {"kind": "epic"})
    # Private items: one open blocker, one resolved, subtasks and an epic.
    _insert(
        db, PRIVATE, "private-blocker-open", f"{PRIVATE_TITLE_MARK} open", "in_progress", feature
    )
    _insert(db, PRIVATE, "private-blocker-done", f"{PRIVATE_TITLE_MARK} done", "done", feature)
    _insert(
        db, PRIVATE, "private-dependent", f"{PRIVATE_TITLE_MARK} dependent", "accepted", feature
    )
    _insert(db, PRIVATE, "private-sub", f"{PRIVATE_TITLE_MARK} sub", "done", feature)
    _insert(db, PRIVATE, "private-sub2", f"{PRIVATE_TITLE_MARK} sub2", "done", feature)
    _insert(db, PRIVATE, "private-epic", f"{PRIVATE_TITLE_MARK} epic", "accepted", {"kind": "epic"})
    # sw-item: blocked by an open private item and by one that does not exist.
    _link(db, "sw-item", READABLE, "private-blocker-open", PRIVATE, "blocked_by", "blocks")
    _link(db, "sw-item", READABLE, "ghost-blocker", PRIVATE, "blocked_by", "blocks")
    # sw-item2: blocked only by a private item that is done.
    _link(db, "sw-item2", READABLE, "private-blocker-done", PRIVATE, "blocked_by", "blocks")
    # A private item blocked by sw-item: sw-item "blocks" it.
    _link(db, "private-dependent", PRIVATE, "sw-item", READABLE, "blocked_by", "blocks")
    # The readable epic's subtasks, both private: one each direction.
    _link(db, "sw-epic", READABLE, "private-sub", PRIVATE, "has_subtask", "subtask_of")
    _link(db, "private-sub2", PRIVATE, "sw-epic", READABLE, "subtask_of", "has_subtask")
    # A readable item whose epic is private.
    _link(db, "sw-free", READABLE, "private-epic", PRIVATE, "subtask_of", "has_subtask")
    # A readable milestone tracking a private item.
    _insert(db, READABLE, "sw-milestone", "Readable milestone", "open", {}, "milestone")
    _link(db, "sw-milestone", READABLE, "private-sub", PRIVATE, "tracks", "tracked_by")
    # A readable item sharing its id with the private subtask that names
    # sw-epic as its epic: not a subtask of sw-epic as far as the reader sees.
    _insert(db, READABLE, "private-sub2", "Readable namesake", "accepted", feature)
    # sw_backlog's epic filter reads a backlink whose relation reads
    # "subtask_of" from the epic's side as membership; one from a private
    # item must not pull in a readable item that shares its id.
    _insert(db, PRIVATE, "private-sub3", f"{PRIVATE_TITLE_MARK} sub3", "accepted", feature)
    _link(db, "private-sub3", PRIVATE, "sw-epic", READABLE, "has_subtask", "subtask_of")
    _insert(db, READABLE, "private-sub3", "Readable namesake 3", "accepted", feature)
    # A private item that says it blocks sw-item (the other spelling of the
    # relation, which lands in sw-item's "blocks" list).
    _insert(db, PRIVATE, "private-blocked", f"{PRIVATE_TITLE_MARK} blocked", "accepted", feature)
    _link(db, "private-blocked", PRIVATE, "sw-item", READABLE, "blocks", "blocked_by")
    # A private ADR every unblocked readable item links to: pull_next's
    # context preview must not count it.
    _insert(db, PRIVATE, "private-adr", f"{PRIVATE_TITLE_MARK} adr", "accepted", {}, "adr")
    for item in ("sw-free", "private-sub2", "sw-epic"):
        _link(db, item, READABLE, "private-adr", PRIVATE, "related_to", "related_to")
    db._raw_conn.commit()


@pytest.fixture(scope="module")
def w(tmp_path_factory):
    world = build_world(tmp_path_factory, label="link-scope-plugins")
    seed_links(world)
    _seed_software(world.db)
    try:
        yield world
    finally:
        world.close()


@pytest.fixture(scope="module")
def scope(w):
    readable = set(w.principals["local_user"].readable_kbs)
    assert PRIVATE not in readable
    return readable


def _call(w, tool, args, readable, writable=None):
    return w.dispatch_tool(
        tool, args, client_kind="local", readable_kbs=readable, writable_kbs=writable
    )


def _without_id(row):
    return json.dumps({k: v for k, v in row.items() if k != "id"}, sort_keys=True)


def _by_id(rows, entry_id):
    [row] = [r for r in rows if r["id"] == entry_id]
    return row


# -- graph-shaped tools ------------------------------------------------------


@pytest.mark.parametrize("tool", ["zettel_graph", "investigation_network", "cascade_network"])
def test_network_tools_drop_private_backlinks(w, scope, tool):
    result = _call(w, tool, {"entry_id": READABLE_ENTRY, "kb_name": READABLE}, scope)
    assert "error" not in result, result
    text = json.dumps(result)
    assert PRIVATE_SPY not in text and PRIVATE_SPY_TITLE not in text


@pytest.mark.parametrize("tool", ["zettel_graph", "investigation_network", "cascade_network"])
def test_network_tools_private_target_reads_like_missing(w, scope, tool):
    result = _call(w, tool, {"entry_id": READABLE_POINTER, "kb_name": READABLE}, scope)
    rows = result["outlinks"]
    assert _without_id(_by_id(rows, PRIVATE_ENTRY)) == _without_id(_by_id(rows, MISSING_TARGET))


def test_investigation_network_totals_count_only_readable_links(w, scope):
    result = _call(
        w, "investigation_network", {"entry_id": READABLE_ENTRY, "kb_name": READABLE}, scope
    )
    assert result["totals"]["backlinks"] == 0


def test_zettel_graph_neighbours_stay_readable(w, scope):
    result = _call(
        w, "zettel_graph", {"entry_id": READABLE_POINTER, "kb_name": READABLE, "depth": 2}, scope
    )
    assert "Private note" not in json.dumps(result)


@pytest.mark.control(reason="unscoped network tools still see the private backlink")
@pytest.mark.parametrize("tool", ["zettel_graph", "investigation_network", "cascade_network"])
def test_network_tools_unscoped_unchanged(w, tool):
    result = _call(w, tool, {"entry_id": READABLE_ENTRY, "kb_name": READABLE}, None)
    assert PRIVATE_SPY in json.dumps(result)


# -- software-kb: dependencies -----------------------------------------------


def _gate_blocker_message(result):
    [crit] = [c for c in result["gate"]["criteria"] if c["text"] == "No unresolved blockers"]
    return crit


def test_check_ready_resolved_private_blocker_reads_as_unknown(w, scope):
    result = _call(w, "sw_check_ready", {"item_id": "sw-item2", "kb_name": READABLE}, scope)
    crit = _gate_blocker_message(result)
    assert crit["passed"] is False
    assert "private-blocker-done" in crit["message"]


@pytest.mark.control(reason="unscoped caller sees the private blocker is done")
def test_check_ready_unscoped_unchanged(w):
    result = _call(w, "sw_check_ready", {"item_id": "sw-item2", "kb_name": READABLE}, None)
    assert _gate_blocker_message(result)["passed"] is True


def test_refine_uses_the_same_scoped_gate(w, scope):
    result = _call(w, "sw_refine", {"kb_name": READABLE}, scope)
    items = {i["id"]: i for i in result["items"]}
    crit = _gate_blocker_message(items["sw-item2"])
    assert crit["passed"] is False


def test_context_for_item_dependencies_read_like_missing(w, scope):
    result = _call(w, "sw_context_for_item", {"item_id": "sw-item", "kb_name": READABLE}, scope)
    text = json.dumps(result)
    assert PRIVATE_TITLE_MARK not in text
    assert "private-dependent" not in text
    deps = result["dependencies"]["blocked_by"]
    assert _without_id(_by_id(deps, "private-blocker-open")) == _without_id(
        _by_id(deps, "ghost-blocker")
    )


def test_claim_refusal_lists_private_blocker_like_a_missing_one(w, scope):
    result = _call(
        w,
        "sw_claim",
        {"item_id": "sw-item", "kb_name": READABLE, "assignee": "someone"},
        scope,
        writable={READABLE},
    )
    assert result["claimed"] is False, result
    deps = result["unresolved_dependencies"]
    assert PRIVATE_TITLE_MARK not in json.dumps(result)
    assert _without_id(_by_id(deps, "private-blocker-open")) == _without_id(
        _by_id(deps, "ghost-blocker")
    )


def test_pull_next_treats_private_blocker_as_unresolved(w, scope):
    result = _call(w, "sw_pull_next", {"kb_name": READABLE}, scope)
    blocked = {b["id"] for b in result.get("blocked_items", [])}
    assert "sw-item2" in blocked
    assert result["recommendation"]["id"] != "sw-item2"
    # Its only ADR link is to a private ADR: counted as a missing target.
    assert result["context_preview"]["adr_count"] == 0


def test_dependencies_leave_out_private_items_this_one_blocks(w, scope):
    result = _call(w, "sw_context_for_item", {"item_id": "sw-item", "kb_name": READABLE}, scope)
    assert "private-blocked" not in json.dumps(result)


@pytest.mark.control(reason="unscoped pull_next knows the private blocker is done")
def test_pull_next_unscoped_unchanged(w):
    result = _call(w, "sw_pull_next", {"kb_name": READABLE}, None)
    assert result["recommendation"]["id"] == "sw-item2"


# -- software-kb: epics ------------------------------------------------------


def test_epic_detail_counts_no_private_subtasks(w, scope):
    result = _call(w, "sw_epic_detail", {"epic_id": "sw-epic", "kb_name": READABLE}, scope)
    assert result["total"] == 0 and PRIVATE_TITLE_MARK not in json.dumps(result)


def test_epics_rollup_counts_no_private_subtasks(w, scope):
    result = _call(w, "sw_epics", {"kb_name": READABLE}, scope)
    assert _by_id(result["epics"], "sw-epic")["total"] == 0


def test_backlog_grouped_by_epic_names_no_private_epic(w, scope):
    result = _call(w, "sw_backlog", {"kb_name": READABLE, "group_by": "epic"}, scope)
    assert PRIVATE_TITLE_MARK not in json.dumps(result)


def test_milestones_count_no_private_items(w, scope):
    result = _call(w, "sw_milestones", {"kb_name": READABLE}, scope)
    assert _by_id(result["milestones"], "sw-milestone")["total_items"] == 0


def test_backlog_epic_filter_ignores_private_subtask_links(w, scope):
    result = _call(w, "sw_backlog", {"kb_name": READABLE, "epic": "sw-epic"}, scope)
    ids = {i["id"] for i in result["items"]}
    assert "private-sub2" not in ids and "private-sub3" not in ids


@pytest.mark.control(reason="unscoped epic rollups still count the private subtasks")
def test_epics_unscoped_unchanged(w):
    result = _call(w, "sw_epic_detail", {"epic_id": "sw-epic", "kb_name": READABLE}, None)
    assert result["total"] == 2
    milestones = _call(w, "sw_milestones", {"kb_name": READABLE}, None)["milestones"]
    assert _by_id(milestones, "sw-milestone")["total_items"] == 1


# -- software-kb: gates on transition and review ------------------------------


@pytest.fixture
def enforced_blocker_gates(monkeypatch):
    """Both gates enforce "no unresolved blockers", so the refusal carries it.

    The default board only warns, and a warned transition goes on to write
    through the operator's config, which a test world is not in.
    """
    import pyrite_software_kb.board as board

    gate = {
        "name": "blockers",
        "policy": "enforce",
        "criteria": [{"text": "No unresolved blockers", "checker": "no_open_blockers"}],
    }
    config = {"lanes": [], "wip_policy": "warn", "gates": {"in_progress": gate, "done": gate}}
    monkeypatch.setattr(board, "load_board_config", lambda _path: config)


def _set_status(w, item_id, status):
    w.db._raw_conn.execute(
        "UPDATE entry SET status = ? WHERE id = ? AND kb_name = ?", (status, item_id, READABLE)
    )
    w.db._raw_conn.commit()


def test_transition_gate_treats_private_blocker_as_unresolved(w, scope, enforced_blocker_gates):
    result = _call(
        w,
        "sw_transition",
        {"item_id": "sw-item2", "kb_name": READABLE, "to_status": "in_progress"},
        scope,
        writable={READABLE},
    )
    assert result.get("error") == "Gate check failed", result
    assert "private-blocker-done" in _gate_blocker_message(result)["message"]


def test_review_gate_treats_private_blocker_as_unresolved(w, scope, enforced_blocker_gates):
    _set_status(w, "sw-item2", "review")
    try:
        result = _call(
            w,
            "sw_review",
            {"item_id": "sw-item2", "kb_name": READABLE, "outcome": "approved", "reviewer": "r"},
            scope,
            writable={READABLE},
        )
    finally:
        _set_status(w, "sw-item2", "accepted")
    assert result.get("error") == "DoD gate check failed", result
    assert "private-blocker-done" in _gate_blocker_message(result)["message"]


@pytest.mark.control(reason="unscoped transition passes the gate: the private blocker is done")
def test_transition_gate_unscoped_unchanged(w, enforced_blocker_gates):
    try:
        result = _call(
            w,
            "sw_transition",
            {"item_id": "sw-item2", "kb_name": READABLE, "to_status": "in_progress"},
            None,
        )
    finally:
        # Past the gate the transition may write; later tests expect "accepted".
        _set_status(w, "sw-item2", "accepted")
    assert result.get("error") != "Gate check failed", result
