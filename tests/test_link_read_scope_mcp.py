"""MCP reads learn nothing about a KB the caller cannot read (P-R4, P-R5).

Private #39 (link data on MCP), #40 (`kb_get` without a KB), and the same
class on the other two MCP content chokepoints: the `pyrite://entries/{id}`
resource and the `summarize_entry` / `find_connections` prompts, which embed
whole entries, links included. Surface: the real chokepoints
(`_dispatch_tool`, `_read_resource`, `_get_prompt`) with the readable set
`mcp_routes` resolves per connection.

The scoped caller is `local_user`; `None` (an operator key, a global admin,
local stdio) is unscoped, and each `control` test pins that it is unchanged.
Plugin tools have their own module per extension.
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
    READ_ONLY,
    READABLE,
    READABLE_ENTRY,
    READABLE_POINTER,
    SHADOWED,
    seed_links,
)


TWIN_TASK = "twin-task"


def _seed_task_twins(world):
    """A task id in PRIVATE and again in READ_ONLY (after it in config order),
    with a readable child, so a lookup without a KB must skip the private
    twin -- the #40 flaw on the task-graph tools."""
    from pyrite.services.kb_service import KBService

    KBService(world.config, world.db).create_entry(
        PRIVATE, TWIN_TASK, "Private twin task", "task", "private task body"
    )
    for task_id, extra in ((TWIN_TASK, ""), ("twin-child", f"parent: {TWIN_TASK}\n")):
        (world.tmpdir / READ_ONLY / f"{task_id}.md").write_text(
            f"---\nid: {task_id}\ntype: task\ntitle: Readable {task_id}\n"
            f"status: open\n{extra}---\n\nreadable task body\n"
        )
    world.index_worker.submit_sync(READ_ONLY)
    world.index_worker.wait_for_idle(timeout=10)


@pytest.fixture(scope="module")
def w(tmp_path_factory):
    world = build_world(tmp_path_factory, label="link-scope-mcp")
    seed_links(world)
    _seed_task_twins(world)
    try:
        yield world
    finally:
        world.close()


@pytest.fixture(scope="module")
def scope(w):
    readable = set(w.principals["local_user"].readable_kbs)
    assert READ_ONLY in readable and PRIVATE not in readable
    return readable


def _call(w, tool, args, readable):
    return w.dispatch_tool(tool, args, client_kind="local", readable_kbs=readable)


def _row(rows, target_id):
    [row] = [r for r in rows if r["id"] == target_id]
    return json.dumps({k: v for k, v in row.items() if k != "id"}, sort_keys=True)


# -- kb_get / kb_read_body without a KB (#40) --------------------------------


@pytest.mark.parametrize("tool", ["kb_get", "kb_read_body"])
def test_lookup_without_kb_is_not_shadowed_by_a_private_twin(w, scope, tool):
    result = _call(w, tool, {"entry_id": SHADOWED}, scope)
    assert "error" not in result, result
    text = json.dumps(result)
    assert "readable twin body" in text and "private twin body" not in text


@pytest.mark.control(
    reason="held before the fix by the handler turning a private hit into a miss; "
    "the scoped lookup that replaces that check must keep it"
)
@pytest.mark.parametrize("tool", ["kb_get", "kb_read_body"])
def test_lookup_without_kb_private_answers_like_missing(w, scope, tool):
    private = _call(w, tool, {"entry_id": PRIVATE_SPY}, scope)
    missing = _call(w, tool, {"entry_id": "definitely-not-there"}, scope)
    assert json.dumps(private).replace(PRIVATE_SPY, "X") == json.dumps(missing).replace(
        "definitely-not-there", "X"
    )


@pytest.mark.control(reason="unscoped lookup keeps config order: the private twin answers")
def test_lookup_without_kb_unscoped_unchanged(w):
    result = _call(w, "kb_get", {"entry_id": SHADOWED}, None)
    assert result["entry"]["kb_name"] == PRIVATE


# -- link data (#39) ---------------------------------------------------------


def test_kb_get_links_are_scoped(w, scope):
    note = _call(w, "kb_get", {"entry_id": READABLE_ENTRY, "kb_name": READABLE}, scope)
    assert PRIVATE_SPY not in json.dumps(note)
    pointer = _call(w, "kb_get", {"entry_id": READABLE_POINTER, "kb_name": READABLE}, scope)
    rows = pointer["entry"]["outlinks"]
    assert _row(rows, PRIVATE_ENTRY) == _row(rows, MISSING_TARGET)


def test_kb_backlinks_drop_private_sources(w, scope):
    result = _call(w, "kb_backlinks", {"entry_id": READABLE_ENTRY, "kb_name": READABLE}, scope)
    assert PRIVATE_SPY not in json.dumps(result)
    assert result["backlink_count"] == 0


@pytest.mark.control(reason="unscoped caller keeps private backlinks and titles")
def test_link_data_unscoped_unchanged(w):
    result = _call(w, "kb_backlinks", {"entry_id": READABLE_ENTRY, "kb_name": READABLE}, None)
    assert PRIVATE_SPY in {b["id"] for b in result["backlinks"]}
    pointer = _call(w, "kb_get", {"entry_id": READABLE_POINTER, "kb_name": READABLE}, None)
    assert "Private note" in json.dumps(pointer)


# -- resource and prompts ----------------------------------------------------


def test_entry_resource_is_scoped(w, scope):
    shadow = w.mcp_server._read_resource(f"pyrite://entries/{SHADOWED}", readable_kbs=scope)
    assert "readable twin body" in json.dumps(shadow)
    note = w.mcp_server._read_resource(f"pyrite://entries/{READABLE_ENTRY}", readable_kbs=scope)
    assert "error" not in note and PRIVATE_SPY not in json.dumps(note)


def test_summarize_prompt_embeds_no_private_links(w, scope):
    result = w.mcp_server._get_prompt(
        "summarize_entry", {"entry_id": READABLE_ENTRY, "kb_name": READABLE}, readable_kbs=scope
    )
    text = json.dumps(result)
    assert READABLE_ENTRY in text and PRIVATE_SPY not in text
    shadow = w.mcp_server._get_prompt("summarize_entry", {"entry_id": SHADOWED}, readable_kbs=scope)
    assert "readable twin body" in json.dumps(shadow)


def test_find_connections_prompt_embeds_no_private_links(w, scope):
    result = w.mcp_server._get_prompt(
        "find_connections",
        {"entry_a": READABLE_ENTRY, "entry_b": SHADOWED},
        readable_kbs=scope,
    )
    text = json.dumps(result)
    assert PRIVATE_SPY_TITLE not in text and PRIVATE_SPY not in text
    assert "readable twin body" in text


# -- QA validation (#58 on MCP) ----------------------------------------------


def test_qa_validate_entry_is_not_an_existence_oracle(w, scope):
    result = _call(w, "kb_qa_validate", {"entry_id": READABLE_POINTER, "kb_name": READABLE}, scope)
    text = json.dumps(result)
    assert (MISSING_TARGET in text) == (PRIVATE_ENTRY in text)


def test_qa_validate_kb_scopes_link_checks(w, scope):
    result = _call(w, "kb_qa_validate", {"kb_name": READABLE, "severity": "info"}, scope)
    text = json.dumps(result)
    assert PRIVATE_ENTRY in text and MISSING_TARGET in text
    assert any(
        i["rule"] == "orphan_entry" and i["entry_id"] == READABLE_ENTRY for i in result["issues"]
    )


def test_qa_validate_without_kb_scopes_link_checks(w, scope):
    result = _call(w, "kb_qa_validate", {"severity": "info", "limit": 500}, scope)
    text = json.dumps(result)
    assert PRIVATE_ENTRY in text and MISSING_TARGET in text
    assert any(
        i["rule"] == "orphan_entry" and i["entry_id"] == READABLE_ENTRY for i in result["issues"]
    )


@pytest.mark.control(reason="unscoped QA: the private target exists, so it is not broken")
def test_qa_validate_unscoped_unchanged(w):
    result = _call(w, "kb_qa_validate", {"entry_id": READABLE_POINTER, "kb_name": READABLE}, None)
    assert PRIVATE_ENTRY not in json.dumps(result)


# -- QA issues returned from write tools -------------------------------------


def _write(w, tool, args, readable):
    writable = None if readable is None else {READABLE}
    return w.dispatch_tool(
        tool, args, client_kind="local", readable_kbs=readable, writable_kbs=writable
    )


def _broken_link_messages(issues):
    return [i["message"] for i in issues if i["rule"] == "broken_link"]


def _alike(messages):
    [private] = [m for m in messages if PRIVATE_ENTRY in m]
    [missing] = [m for m in messages if MISSING_TARGET in m]
    return private.replace(PRIVATE_ENTRY, "X") == missing.replace(MISSING_TARGET, "X")


def test_qa_assess_reports_private_and_missing_targets_alike(w, scope):
    result = _write(w, "kb_qa_assess", {"entry_id": READABLE_POINTER, "kb_name": READABLE}, scope)
    assert _alike(_broken_link_messages(result["issues"])), result


def test_create_with_validate_reports_private_and_missing_targets_alike(w, scope):
    result = _write(
        w,
        "kb_create",
        {
            "kb_name": READABLE,
            "entry_type": "note",
            "title": "Validated pointer",
            "body": f"[[{PRIVATE}:{PRIVATE_ENTRY}]] and [[{PRIVATE}:{MISSING_TARGET}]]",
            "validate": True,
        },
        scope,
    )
    assert _alike(_broken_link_messages(result["qa_issues"])), result


def test_update_with_validate_reports_private_and_missing_targets_alike(w, scope):
    result = _write(
        w,
        "kb_update",
        {
            "entry_id": READABLE_POINTER,
            "kb_name": READABLE,
            "body": f"see [[{PRIVATE}:{PRIVATE_ENTRY}]] and [[{PRIVATE}:{MISSING_TARGET}]]",
            "validate": True,
        },
        scope,
    )
    assert _alike(_broken_link_messages(result["qa_issues"])), result


def test_qa_assess_kb_reports_private_and_missing_targets_alike(w, scope):
    result = _write(w, "kb_qa_assess", {"kb_name": READABLE, "max_age_hours": 0}, scope)
    [pointer] = [r for r in result["results"] if r.get("target_entry") == READABLE_POINTER]
    assert _alike(_broken_link_messages(pointer["issues"])), pointer


@pytest.mark.control(reason="unscoped assessment: the private target exists, so not broken")
def test_qa_assess_unscoped_unchanged(w):
    result = _write(w, "kb_qa_assess", {"entry_id": READABLE_POINTER, "kb_name": READABLE}, None)
    assert not any(PRIVATE_ENTRY in m for m in _broken_link_messages(result["issues"]))


# -- task-graph tools without a KB (#40 on the task resolver) ----------------


@pytest.mark.parametrize(
    "tool",
    ["task_status", "task_subtree", "task_ancestors", "task_blocked_by", "task_critical_path"],
)
def test_task_lookup_without_kb_is_not_shadowed_by_a_private_twin(w, scope, tool):
    result = _call(w, tool, {"task_id": TWIN_TASK}, scope)
    assert "error" not in result, result
    assert "Private twin task" not in json.dumps(result)


def test_task_subtree_resolves_to_the_readable_twin(w, scope):
    result = _call(w, "task_subtree", {"task_id": TWIN_TASK}, scope)
    assert "twin-child" in json.dumps(result)


@pytest.mark.control(reason="unscoped task lookup keeps config order: the private twin answers")
def test_task_lookup_unscoped_unchanged(w):
    result = _call(w, "task_status", {"task_id": TWIN_TASK}, None)
    assert "Private twin task" in json.dumps(result)


# -- kb_qa_status counts (cold read) -----------------------------------------


@pytest.mark.control(
    reason="a consistency check: on the batch base neither tool was scoped, so they "
    "agreed; after round 0 only validate was, and this failed -- it pins that they agree"
)
def test_qa_status_counts_match_scoped_validation(w, scope):
    status = _call(w, "kb_qa_status", {"kb_name": READABLE}, scope)
    validated = _call(
        w, "kb_qa_validate", {"kb_name": READABLE, "severity": "info", "limit": 10000}, scope
    )
    for rule in ("broken_link", "orphan_entry"):
        counted = sum(1 for i in validated["issues"] if i["rule"] == rule)
        assert status["issues_by_rule"].get(rule, 0) == counted, rule


def test_qa_status_scoped_counts_differ_from_unscoped_by_the_private_rows(w, scope):
    scoped = _call(w, "kb_qa_status", {"kb_name": READABLE}, scope)["issues_by_rule"]
    unscoped = _call(w, "kb_qa_status", {"kb_name": READABLE}, None)["issues_by_rule"]
    # Each link to the existing private note reads as broken (the pointer's,
    # and the ones other tests in this module write); the readable note,
    # linked only from the private KB, reads as an orphan.
    assert scoped["broken_link"] >= unscoped["broken_link"] + 1
    assert scoped.get("orphan_entry", 0) >= unscoped.get("orphan_entry", 0) + 1


# -- byte equality: kb_qa_validate, private vs missing target ----------------


def test_qa_validate_private_and_missing_target_issues_are_byte_identical(w, scope):
    result = _call(w, "kb_qa_validate", {"entry_id": READABLE_POINTER, "kb_name": READABLE}, scope)
    broken = [i for i in result["issues"] if i["rule"] == "broken_link"]
    [private] = [i for i in broken if PRIVATE_ENTRY in i["message"]]
    [missing] = [i for i in broken if MISSING_TARGET in i["message"]]
    assert json.dumps(private, sort_keys=True).replace(PRIVATE_ENTRY, "X") == json.dumps(
        missing, sort_keys=True
    ).replace(MISSING_TARGET, "X")
