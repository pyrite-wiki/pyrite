"""A KB's declared task state machine applies to every update (#1709).

Maintainer decision, 2026-11-18: `types.task.state_machine` in kb.yaml (strict
with its own table, or relaxed with `enforce_transitions: false` and
`require_reason_on_transition`) governs EVERY update of a task in that KB, on
every surface, exactly as the core `TASK_WORKFLOW` governs where a KB declares
nothing. Before the fix `KBService._hook_ctx` never handed the `before_save`
hook the KB's schema, so the declared machine was read by
`migrate-relaxed-mode` and applied to no write.

Each cell drives a real KB from `tests/_world.py` through a real surface and
judges by the status left IN THE FILE. The expected values are written by hand
from the tables below, never taken from `TASK_WORKFLOW` or the validator.

Core table (for reading the cells): open -> claimed | cancelled;
claimed -> in_progress | cancelled; in_progress -> blocked | review | done |
failed | cancelled | open (reason); ... `open -> done` and `open -> review`
are NOT core moves; `open -> claimed` IS.

Strict KB declares: states [open, review, done]; open -> review; review -> done.
  - `open -> claimed`: core allows, declared forbids  -> refused.
  - `open -> review`:  core forbids, declared allows  -> accepted.
Relaxed KB declares: enforce_transitions false, reason required,
  states [open, claimed, in_progress, done].
  - `open -> done` with a reason: core forbids, relaxed allows -> accepted.
  - `open -> done` without a reason                            -> refused.
  - `open -> review` (not in `states`) with a reason           -> refused.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from pyrite.config import KBConfig, KBType
from tests._world import build_world

KB = "work"

STRICT_YAML = """\
name: work
kb_type: generic
types:
  task:
    state_machine:
      initial: open
      field: status
      states: [open, review, done]
      transitions:
        - {from: open, to: review, requires: write}
        - {from: review, to: done, requires: write}
"""

RELAXED_YAML = """\
name: work
kb_type: generic
types:
  task:
    state_machine:
      enforce_transitions: false
      require_reason_on_transition: true
      states: [open, claimed, in_progress, done]
"""

NO_MACHINE_YAML = "name: work\nkb_type: generic\n"

TASK_FILE = "---\nid: x\ntitle: X\ntype: task\nstatus: open\n---\n\nWork.\n"

SURFACES = ["cli_task_update", "cli_update", "mcp_kb_update", "mcp_task_update", "rest_patch"]
#: The surfaces that can carry a status and a reason in one write.
REASON_SURFACES = ["cli_task_update", "cli_update", "mcp_kb_update", "rest_put"]


@pytest.fixture
def world_for(tmp_path):
    worlds = []

    def make(kb_yaml: str):
        kb_path = tmp_path / "kb"
        kb_path.mkdir(exist_ok=True)
        (kb_path / "kb.yaml").write_text(kb_yaml, encoding="utf-8")
        world = build_world(
            tmp_path / "world",
            kbs=[KBConfig(name=KB, path=kb_path, kb_type=KBType.GENERIC)],
        )
        world.add_entry(KB, TASK_FILE, filename="tasks/x.md")
        worlds.append(world)
        return world

    yield make
    for world in worlds:
        world.close()


def _file_status(world) -> str:
    text = (world.kb_path(KB) / "tasks" / "x.md").read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---", 2)[1])["status"]


def _update(world, surface: str, status: str, reason: str | None = None) -> None:
    """One update of task `x` through `surface`; never raises on a refusal."""
    if surface == "cli_task_update":
        args = ["task", "update", "x", "-k", KB, "--status", status, "--format", "json"]
        if reason is not None:
            args += ["--reason", reason]
        world.cli(*args)
    elif surface == "cli_update":
        args = ["update", "x", "-k", KB, "-f", f"status={status}", "--format", "json"]
        if reason is not None:
            args += ["-f", f"status_reason={reason}"]
        world.cli(*args)
    elif surface == "mcp_kb_update":
        fields = {"entry_id": "x", "kb_name": KB, "status": status}
        if reason is not None:
            fields["status_reason"] = reason
        world.mcp(tier="write")._dispatch_tool("kb_update", fields)
    elif surface == "mcp_task_update":
        assert reason is None, "task_update has no reason argument"
        world.mcp(tier="write")._dispatch_tool(
            "task_update", {"task_id": "x", "kb_name": KB, "status": status}
        )
    elif surface == "rest_patch":
        assert reason is None, "PATCH sets one field"
        world.client.patch("/api/entries/x", json={"kb": KB, "field": "status", "value": status})
    elif surface == "rest_put":
        meta = {"status": status}
        if reason is not None:
            meta["status_reason"] = reason
        world.client.put("/api/entries/x", json={"kb": KB, "metadata": meta})
    else:  # pragma: no cover
        raise AssertionError(surface)


# -- strict declared table ---------------------------------------------------


@pytest.mark.parametrize("surface", SURFACES)
def test_strict_declared_table_refuses_a_move_only_the_core_table_allows(world_for, surface):
    world = world_for(STRICT_YAML)
    _update(world, surface, "claimed")  # core: open -> claimed is fine
    assert _file_status(world) == "open"


@pytest.mark.parametrize("surface", SURFACES)
def test_strict_declared_table_accepts_a_move_only_the_declared_table_allows(world_for, surface):
    world = world_for(STRICT_YAML)
    _update(world, surface, "review")  # core: open -> review is refused
    assert _file_status(world) == "review"


# -- relaxed declared machine ------------------------------------------------


@pytest.mark.parametrize("surface", REASON_SURFACES)
def test_relaxed_accepts_an_off_table_move_with_a_reason(world_for, surface):
    world = world_for(RELAXED_YAML)
    _update(world, surface, "done", reason="relaxed says ok")
    assert _file_status(world) == "done"


@pytest.mark.control(
    reason="the core table refuses this move too, so it passes before the fix; after it, it guards the relaxed rules against over-loosening"
)
@pytest.mark.parametrize("surface", SURFACES)
def test_relaxed_refuses_an_off_table_move_without_a_reason(world_for, surface):
    world = world_for(RELAXED_YAML)
    _update(world, surface, "done")
    assert _file_status(world) == "open"


@pytest.mark.control(
    reason="the core table refuses this move too, so it passes before the fix; after it, it guards the relaxed rules against over-loosening"
)
@pytest.mark.parametrize("surface", REASON_SURFACES)
def test_relaxed_keeps_state_membership(world_for, surface):
    world = world_for(RELAXED_YAML)
    _update(world, surface, "review", reason="review is not a declared state")
    assert _file_status(world) == "open"


# -- no declared machine: the core table, unchanged (control) ----------------


@pytest.mark.control(
    reason="no machine declared: the core TASK_WORKFLOW applied before the fix too"
)
@pytest.mark.parametrize("surface", SURFACES)
def test_without_a_declared_machine_the_core_table_refuses_open_to_done(world_for, surface):
    world = world_for(NO_MACHINE_YAML)
    _update(world, surface, "done")
    assert _file_status(world) == "open"


@pytest.mark.control(
    reason="no machine declared: the core TASK_WORKFLOW applied before the fix too"
)
@pytest.mark.parametrize("surface", SURFACES)
def test_without_a_declared_machine_the_core_table_allows_open_to_claimed(world_for, surface):
    world = world_for(NO_MACHINE_YAML)
    _update(world, surface, "claimed")
    assert _file_status(world) == "claimed"


# -- the other routes that move a status -------------------------------------
#
# `claim` and `reset` write through `KBService.update_entry`, so they meet the
# declared machine too; `task update --comment` writes through
# `apply_operations`, which runs the same hook.


def _index_status(world) -> str:
    return world.db.get_entry("x", KB).get("status")


def _set_file_status(world, status: str) -> None:
    path = world.kb_path(KB) / "tasks" / "x.md"
    path.write_text(path.read_text().replace("status: open", f"status: {status}"))
    world.index(KB)


@pytest.mark.parametrize("route", ["cli", "mcp", "rest"])
def test_claim_is_refused_where_the_declared_table_has_no_open_to_claimed(world_for, route):
    world = world_for(STRICT_YAML)  # declares open -> review only
    if route == "cli":
        world.cli("task", "claim", "x", "-k", KB, "-a", "bob", "--format", "json")
    elif route == "mcp":
        world.mcp(tier="write")._dispatch_tool(
            "task_claim", {"task_id": "x", "kb_name": KB, "assignee": "bob"}
        )
    else:
        world.client.post("/api/tasks/x/claim", params={"kb": KB}, json={"assignee": "bob"})
    # The claim moves the index row first and rolls it back when the file
    # write is refused: neither the file nor the index may say `claimed`.
    assert _file_status(world) == "open"
    assert _index_status(world) == "open"


def test_reset_is_refused_where_the_declared_table_has_no_in_progress_to_open(world_for):
    world = world_for(STRICT_YAML)
    _set_file_status(world, "in_progress")
    world.cli("task", "reset", "x", "-k", KB, "--format", "json")
    assert _file_status(world) == "in_progress"


@pytest.mark.control(
    reason="reset supplies its own reason, which relaxed mode needs; core allowed it too"
)
def test_reset_is_accepted_by_a_relaxed_machine_because_it_carries_a_reason(world_for):
    world = world_for(RELAXED_YAML)
    _set_file_status(world, "in_progress")
    world.cli("task", "reset", "x", "-k", KB, "--format", "json")
    assert _file_status(world) == "open"


def test_the_audit_comment_route_applies_the_declared_table(world_for):
    world = world_for(STRICT_YAML)
    args = ["task", "update", "x", "-k", KB, "--comment", "why", "--format", "json"]
    world.cli(*args, "-s", "claimed")
    assert _file_status(world) == "open"  # declared table: no open -> claimed
    world.cli(*args, "-s", "review")
    assert _file_status(world) == "review"  # declared table: open -> review


# -- scope and consequences, pinned as built (maintainer questions Q1, Q2) ----
#
# These cells record what the code does TODAY so a later decision shows up as
# a test change, not a silent one. They are not the maintainer's answers.

EVENT_YAML = """\
name: work
kb_type: generic
types:
  task: {}
  event:
    state_machine:
      states: [confirmed, disputed, proven]
      transitions:
        - {from: confirmed, to: disputed, requires: write}
"""

EVENT_RELAXED_YAML = """\
name: work
kb_type: generic
types:
  task: {}
  event:
    state_machine:
      enforce_transitions: false
      require_reason_on_transition: true
      states: [confirmed, disputed, proven]
"""

EVENT_FILE = (
    "---\nid: e1\ntitle: E\ntype: event\ndate: '2026-01-01'\nstatus: confirmed\n---\n\nHappened.\n"
)


def _event_status(world) -> str:
    text = (world.kb_path(KB) / "events" / "e1.md").read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---", 2)[1])["status"]


def _move_event(world, surface: str, status: str, reason: str | None = None) -> None:
    if surface == "cli_update":
        args = ["update", "e1", "-k", KB, "-f", f"status={status}", "--format", "json"]
        if reason:
            args += ["-f", f"status_reason={reason}"]
        world.cli(*args)
    else:
        fields = {"entry_id": "e1", "kb_name": KB, "status": status}
        if reason:
            fields["status_reason"] = reason
        world.mcp(tier="write")._dispatch_tool("kb_update", fields)


@pytest.mark.parametrize("surface", ["cli_update", "mcp_kb_update"])
def test_q2_a_non_task_type_with_a_declared_machine_is_judged_by_it(world_for, surface):
    """Q2 (open): the hook applies to ANY type whose kb.yaml declares a
    `state_machine` and whose entry class carries a `status` (`event` does; a
    generic custom type does not, so its old status is missing and the hook
    fails open). It is not limited to `task` (`is_task_or_opted_in` in
    `_task_validate_transition`); the #1709 decision says "task". Before the
    fix the declared machine was never read, so an event moved freely. Pinned
    as built; narrowing to `task` would change this cell, not the others."""
    world = world_for(EVENT_YAML)
    world.add_entry(KB, EVENT_FILE, filename="events/e1.md")
    _move_event(world, surface, "proven")  # declared table: confirmed -> proven is absent
    assert _event_status(world) == "confirmed"
    _move_event(world, surface, "disputed")  # declared table: confirmed -> disputed
    assert _event_status(world) == "disputed"


def test_q2_a_reason_never_reaches_a_non_task_type_whose_class_has_no_status_reason(world_for):
    """Q2 (open), a consequence: the hook reads `status_reason` from the entry
    CLASS. `event` has no such attribute, so a relaxed reasons-required machine
    declared on it refuses every move, a reason given or not. Pinned as built."""
    world = world_for(EVENT_RELAXED_YAML)
    world.add_entry(KB, EVENT_FILE, filename="events/e1.md")
    _move_event(world, "cli_update", "proven", reason="because")
    assert _event_status(world) == "confirmed"


@pytest.mark.parametrize("route", ["cli", "mcp", "rest"])
def test_q1_claim_in_a_relaxed_reasons_required_kb_is_refused(world_for, route):
    """Q1 (open): `claim` passes no `status_reason`, so a relaxed KB with
    `require_reason_on_transition: true` refuses `open -> claimed`. Pinned as
    built; the options are accept and document, give claim a reason, or exempt
    `open -> claimed`. The claim's index change is rolled back."""
    world = world_for(RELAXED_YAML)
    if route == "cli":
        world.cli("task", "claim", "x", "-k", KB, "-a", "bob", "--format", "json")
    elif route == "mcp":
        world.mcp(tier="write")._dispatch_tool(
            "task_claim", {"task_id": "x", "kb_name": KB, "assignee": "bob"}
        )
    else:
        world.client.post("/api/tasks/x/claim", params={"kb": KB}, json={"assignee": "bob"})
    assert _file_status(world) == "open"
    assert _index_status(world) == "open"


# -- a kb.yaml that does not load: bulk create answers as it did before -------


def test_a_bulk_create_on_a_kb_yaml_that_does_not_load_fails_per_item(world_for):
    """`bulk_create_entries` used to build its hook context without the schema,
    so an unloadable kb.yaml was refused per item inside the batch. Carrying
    the schema must not turn that into a refusal of the whole call."""
    world = world_for(NO_MACHINE_YAML)
    (world.kb_path(KB) / "kb.yaml").write_bytes(b"name: work\ntypes: {note: [unclosed\n")
    answer = world.mcp(tier="write")._dispatch_tool(
        "kb_bulk_create",
        {"kb_name": KB, "entries": [{"entry_type": "note", "title": "A"}]},
    )
    assert "error_code" not in answer, answer
    assert (answer["total"], answer["created"], answer["failed"]) == (1, 0, 1)
    assert answer["results"][0]["error_code"] == "KB_CONFIG_INVALID"
