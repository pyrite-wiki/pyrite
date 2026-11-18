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
