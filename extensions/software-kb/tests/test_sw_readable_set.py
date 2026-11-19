"""The `sw_*` read surfaces return only rows from KBs the caller may read (#223).

Each of these tools declares `kb_name` optionally, so a call that omits it is
refused for a scoped caller by the MCP chokepoint rather than served the index.
They pass the caller's readable set into the query through `kb_scope_clause`,
the rule the other extensions use, so the four branches are the same
everywhere: a named KB binds, a set narrows, an empty set matches nothing, and
`None` stays unscoped.

These drive a real `PyriteDB` rather than a stub, because the narrowing is the
storage query's job (`kb_name` and the clause compose) and a stub would only
test the stub.
"""

import json
from pathlib import Path

import pytest
from pyrite_software_kb.plugin import SoftwareKBPlugin

from pyrite.config import KBConfig
from pyrite.storage.database import PyriteDB
from tests._world import build_world

PUBLIC, PRIVATE = "public-kb", "private-kb"

# Every handler that was in OPTIONAL_KB_TOOLS for this extension, with the entry
# type it reads. `_mcp_create_adr` is absent on purpose: it writes an ADR, so a
# behavioural test would create one; its read (finding the next ADR number) is
# narrowed by the same helper as `_mcp_adrs`, which is covered here.
SW_SURFACES = [
    ("_mcp_adrs", "adr"),
    ("_mcp_component", "component"),
    ("_mcp_standards", "standard"),
    ("_mcp_validations", "programmatic_validation"),
    ("_mcp_conventions", "development_convention"),
    ("_mcp_backlog", "backlog_item"),
    ("_mcp_epics", "backlog_item"),
    ("_mcp_milestones", "milestone"),
    ("_mcp_review_queue", "backlog_item"),
    ("_mcp_board", "backlog_item"),
    ("_mcp_pull_next", "backlog_item"),
]

# Surfaces that echo every matching row, used to show the filter is what
# removed the private row rather than the handler returning nothing for an
# unrelated reason.
NON_VACUITY_SURFACES = [
    ("_mcp_adrs", "adr"),
    ("_mcp_component", "component"),
    ("_mcp_standards", "standard"),
    ("_mcp_backlog", "backlog_item"),
    ("_mcp_milestones", "milestone"),
]


def _make_db(tmp_path, entry_type):
    db = PyriteDB(Path(tmp_path) / "index.db")
    for kb in (PUBLIC, PRIVATE):
        db._raw_conn.execute(
            "INSERT OR IGNORE INTO kb (name, path, kb_type) VALUES (?, ?, ?)",
            (kb, str(tmp_path), "software"),
        )
        db._raw_conn.execute(
            "INSERT OR IGNORE INTO entry (id, kb_name, entry_type, title, body, metadata,"
            "importance, status, priority, created_at, updated_at) VALUES (?, ?, ?, ?, '', ?,"
            " 5, 'accepted', 'medium', '2026-01-01T00:00:00', '2026-01-02T00:00:00')",
            (
                f"{kb}-{entry_type}",
                kb,
                entry_type,
                f"{kb} {entry_type}",
                json.dumps(
                    {
                        "adr_number": 1,
                        "kind": "epic",
                        "category": "coding",
                        "review_status": "under_review",
                    }
                ),
            ),
        )
    db._raw_conn.commit()
    return db


def _plugin_for(db):
    plugin = SoftwareKBPlugin()
    plugin._get_db = lambda: (db, False)
    return plugin


def _call(tmp_path, handler, entry_type, **kwargs):
    db = _make_db(tmp_path, entry_type)
    try:
        return json.dumps(getattr(_plugin_for(db), handler)({}, **kwargs))
    finally:
        db.close()


@pytest.mark.parametrize("handler,entry_type", NON_VACUITY_SURFACES)
class TestThePrivateRowIsReachableWhenUnscoped:
    def test_unscoped_call_does_reach_the_private_kb(self, tmp_path, handler, entry_type):
        out = _call(tmp_path, handler, entry_type)
        assert f"{PUBLIC}-{entry_type}" in out
        assert f"{PRIVATE}-{entry_type}" in out

    def test_the_readable_set_is_what_removes_it(self, tmp_path, handler, entry_type):
        out = _call(tmp_path, handler, entry_type, readable_kbs={PUBLIC})
        assert f"{PUBLIC}-{entry_type}" in out
        assert f"{PRIVATE}-{entry_type}" not in out


@pytest.mark.parametrize("handler,entry_type", SW_SURFACES)
def test_no_read_surface_serves_an_unreadable_kb(tmp_path, handler, entry_type):
    scoped = _call(tmp_path, handler, entry_type, readable_kbs={PUBLIC})
    assert f"{PRIVATE}-{entry_type}" not in scoped

    empty = _call(tmp_path, handler, entry_type, readable_kbs=set())
    assert f"{PUBLIC}-{entry_type}" not in empty
    assert f"{PRIVATE}-{entry_type}" not in empty


@pytest.mark.parametrize(
    "readable_kbs,expected_counts",
    [
        ({PUBLIC}, {"adr_count": 1, "component_count": 1, "validation_count": 0}),
        ({PUBLIC, PRIVATE}, {"adr_count": 2, "component_count": 2, "validation_count": 0}),
    ],
    ids=["public-only", "both-kbs"],
)
def test_pull_next_context_preview_preserves_readable_link_scope(
    tmp_path, readable_kbs, expected_counts
):
    world = build_world(
        tmp_path,
        kbs=[KBConfig(name=kb, path=tmp_path / kb, kb_type="software") for kb in (PUBLIC, PRIVATE)],
        rest=False,
    )
    try:
        world.add_entry(
            PUBLIC,
            f"""---
id: next-item
title: Next item
type: backlog_item
kind: feature
status: accepted
priority: high
links:
  - target: public-adr
    relation: related_to
  - target: private-adr
    kb: {PRIVATE}
    relation: related_to
---
Implement this item.
""",
        )
        for kb, visibility in ((PUBLIC, "public"), (PRIVATE, "private")):
            world.add_entry(
                kb,
                f"""---
id: {visibility}-adr
title: {visibility} decision
type: adr
adr_number: 1
status: accepted
date: 2026-10-23
---
Decision details.
""",
            )
            world.add_entry(
                kb,
                f"""---
id: {visibility}-component
title: {visibility} component
type: component
kind: module
path: {visibility}/module.py
owner: core
links:
  - target: next-item
    kb: {PUBLIC}
    relation: related_to
---
Component details.
""",
            )

        files_before = {
            path: path.read_bytes()
            for kb in (PUBLIC, PRIVATE)
            for path in world.kb_path(kb).rglob("*.md")
        }
        server = world.mcp("read")
        oriented = server._dispatch_tool(
            "kb_orient", {"kb_name": PUBLIC}, readable_kbs=readable_kbs
        )
        assert "error" not in oriented
        context = server._dispatch_tool(
            "sw_context_for_item",
            {"item_id": "next-item", "kb_name": PUBLIC},
            readable_kbs=readable_kbs,
        )
        pulled = server._dispatch_tool(
            "sw_pull_next", {"kb_name": PUBLIC}, readable_kbs=readable_kbs
        )

        assert context["item"]["id"] == pulled["recommendation"]["id"] == "next-item"
        if PRIVATE in readable_kbs:
            assert {entry["id"] for entry in context["adrs"]} == {"public-adr", "private-adr"}
            assert {entry["id"] for entry in context["components"]} == {
                "public-component",
                "private-component",
            }
            assert context["related"] == []
        else:
            assert [entry["id"] for entry in context["adrs"]] == ["public-adr"]
            assert [entry["id"] for entry in context["components"]] == ["public-component"]
            # The readable source names this target, but its metadata stays hidden.
            assert context["related"] == [
                {
                    "id": "private-adr",
                    "title": None,
                    "entry_type": None,
                    "relation": "related_to",
                    "body_preview": "",
                }
            ]
            # An unreadable source contributes no backlink, even as a placeholder.
            assert "private-component" not in json.dumps(context)
            assert "private decision" not in json.dumps(context)

        assert {
            path: path.read_bytes()
            for kb in (PUBLIC, PRIVATE)
            for path in world.kb_path(kb).rglob("*.md")
        } == files_before
        assert pulled["context_preview"] == expected_counts
        assert pulled["context_preview"] == {
            "adr_count": len(context["adrs"]),
            "component_count": len(context["components"]),
            "validation_count": len(context["validations"]),
        }
    finally:
        world.close()
