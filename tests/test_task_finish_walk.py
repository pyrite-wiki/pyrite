"""`task finish --walk`: walk a held task to `done` in one write (#1685).

The property: `task finish --walk` passes the task through every edge of the
declared path to `done`, each edge judged by `_task_validate_transition()`
(through `validate_status_change()`) and recorded as one `status_change_log`
item; no state is skipped and the transition table does not change. Without
`--walk` only a direct edge is taken, and the refusal names the path `--walk`
would take. CLI and MCP return one shape from one service function.

The walk cases are parametrised from the task workflow's own declared states
(`TASK_WORKFLOW["states"]`), and `EXPECTED_WALK` must name every one of them:
a status added to the table fails `test_every_declared_status_has_a_walk_case`
until it is given a hand-written expected walk here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from pyrite.config import KBConfig
from pyrite.models.task import TASK_WORKFLOW
from tests._world import build_world

KB = "work"
#: #1709: the before_save hook is never given the KB's schema, so a task
#: `state_machine` a KB declares is ignored by every update; start and finish
#: are judged as `task update` is (the core table) until that is decided.
_HOOK_IGNORES_KB_SCHEMA = (
    "#1709: the update hook is never given kb_schema; a KB's task state_machine is ignored"
)
WHO = "agent:w"
REFUSED = None

#: Each declared status -> the states `finish --walk` passes through to `done`
#: (from first), or REFUSED. Written by hand from the transition table:
#: open (nothing claimed it), done (already there), failed (its only way on is
#: back through open), cancelled (terminal) are refused.
EXPECTED_WALK: dict[str, list[str] | None] = {
    "open": REFUSED,
    "claimed": ["claimed", "in_progress", "done"],
    "in_progress": ["in_progress", "done"],
    "blocked": ["blocked", "in_progress", "done"],
    "review": ["review", "done"],
    "done": REFUSED,
    "failed": REFUSED,
    "cancelled": REFUSED,
}


def _task(entry_id: str, extra: str = "", body: str = "Work.\n") -> str:
    return f"---\ntype: task\nid: {entry_id}\ntitle: Task {entry_id}\n{extra}---\n\n{body}"


@pytest.fixture
def world(tmp_path):
    w = build_world(tmp_path, kbs=[KBConfig(name=KB, path=tmp_path / "kb", kb_type="generic")])
    with w.pointed():
        yield w
    w.close()


def _front(path: Path) -> dict:
    return yaml.safe_load(path.read_text().split("---", 2)[1])


def _edges(front: dict) -> list[tuple[str, str]]:
    return [(i["from"], i["to"]) for i in front.get("status_change_log") or []]


def _pairs(states: list[str]) -> list[tuple[str, str]]:
    return list(zip(states, states[1:], strict=False))


def _finish(world, entry_id: str, *extra: str):
    result = world.cli("task", "finish", entry_id, "-k", KB, *extra, "--format", "json")
    return result.exit_code, json.loads(result.stdout)


@pytest.mark.control(reason="pins the case table to the declared states; holds without the feature")
def test_every_declared_status_has_a_walk_case():
    assert set(EXPECTED_WALK) == set(TASK_WORKFLOW["states"])


@pytest.mark.parametrize("start", TASK_WORKFLOW["states"])
def test_finish_walk_from_each_declared_status(world, start):
    path = world.add_entry(KB, _task("t", f"status: {start}\nassignee: {WHO}\n"))
    before = path.read_bytes()

    code, data = _finish(world, "t", "--walk", "--reason", "shipped", "--by", WHO)

    expected = EXPECTED_WALK[start]
    if expected is REFUSED:
        assert code == 1, data
        assert data["outcome"] == "error"
        assert path.read_bytes() == before
        return
    assert code == 0, data
    assert data["finished"] is True
    assert data["status"] == "done"
    assert data["path"] == expected
    front = _front(path)
    assert front["status"] == "done"
    assert front["status_reason"] == "shipped"
    assert front["assignee"] == WHO
    assert _edges(front) == _pairs(expected)
    for item in front["status_change_log"]:
        assert item["by"] == WHO  # the holder, who finished it
        assert item["comment"] == "shipped"
        assert "override" not in item


def test_finish_keeps_the_earlier_log_items_and_appends_one_per_edge(world):
    path = world.add_entry(
        KB,
        _task(
            "t",
            f"status: claimed\nassignee: {WHO}\nstatus_change_log:\n"
            "- {date: '2026-01-01T00:00:00Z', from: open, to: claimed, by: x, comment: c}\n",
        ),
    )
    code, data = _finish(world, "t", "--walk", "--reason", "r", "--by", WHO)
    assert code == 0, data
    assert _edges(_front(path)) == [
        ("open", "claimed"),
        ("claimed", "in_progress"),
        ("in_progress", "done"),
    ]


def test_finish_without_walk_takes_a_direct_edge(world):
    path = world.add_entry(KB, _task("t", f"status: in_progress\nassignee: {WHO}\n"))
    code, data = _finish(world, "t", "--by", WHO)
    assert code == 0, data
    assert data["path"] == ["in_progress", "done"]
    front = _front(path)
    assert front["status"] == "done"
    assert _edges(front) == [("in_progress", "done")]
    assert "status_reason" not in front  # not asked for, not written


def test_finish_without_walk_from_claimed_is_refused_and_names_the_path(world):
    path = world.add_entry(KB, _task("t", f"status: claimed\nassignee: {WHO}\n"))
    before = path.read_bytes()

    code, data = _finish(world, "t", "--reason", "r", "--by", WHO)

    assert code == 1, data
    assert "claimed → in_progress → done" in data["error"]
    assert "--walk" in data["error"]
    assert path.read_bytes() == before


# -- finish requires the caller to hold the task; operators have an override ----


def test_the_holder_finishes(world):
    path = world.add_entry(KB, _task("t", "status: review\nassignee: agent:x\n"))
    code, data = _finish(world, "t", "--by", "agent:x")
    assert code == 0, data
    assert (data["by"], data["override"]) == ("agent:x", False)
    assert [(i["by"], i.get("override")) for i in _front(path)["status_change_log"]] == [
        ("agent:x", None)
    ]


@pytest.mark.parametrize(
    "extra",
    [("--by", "agent:b"), (), ("--by", "")],
    ids=["another-agent", "no-caller", "empty-caller"],
)
def test_a_caller_who_does_not_hold_the_task_is_refused_and_nothing_is_written(world, extra):
    path = world.add_entry(KB, _task("t", "status: claimed\nassignee: agent:a\n"))
    before = path.read_bytes()

    code, data = _finish(world, "t", "--walk", *extra)

    assert code == 1, data
    assert data["error_code"] == "FORBIDDEN", data
    assert "finish requires the caller to hold the task" in data["error"]
    assert path.read_bytes() == before


def test_the_operator_finishes_a_task_it_does_not_hold_only_by_the_override(world):
    """The CLI is the operator: with --operator-override it finishes agent:a's
    task, and each log item records the operator and the override."""
    path = world.add_entry(KB, _task("t", "status: claimed\nassignee: agent:a\n"))

    code, data = _finish(world, "t", "--walk", "--operator-override")

    assert code == 0, data
    assert (data["by"], data["override"]) == ("operator", True)
    front = _front(path)
    assert front["status"] == "done"
    assert front["assignee"] == "agent:a"
    assert [(i["by"], i["override"]) for i in front["status_change_log"]] == [
        ("operator", True),
        ("operator", True),
    ]


# -- workflows: core (above), a KB's strict state machine, a relaxed one ---------


def _schema(world, state_machine: str) -> None:
    (world.kb_path(KB) / "kb.yaml").write_text(
        "name: work\nkb_type: generic\ntypes:\n  task:\n    state_machine:\n" + state_machine
    )


@pytest.mark.xfail(strict=True, reason=_HOOK_IGNORES_KB_SCHEMA)
def test_finish_walk_follows_a_kb_strict_state_machine(world):
    _schema(
        world,
        "      states: [open, claimed, in_progress, testing, done]\n"
        "      transitions:\n"
        "        - {from: open, to: claimed, requires: write}\n"
        "        - {from: claimed, to: in_progress, requires: write}\n"
        "        - {from: in_progress, to: testing, requires: write}\n"
        "        - {from: testing, to: done, requires: write}\n",
    )
    path = world.add_entry(KB, _task("t", f"status: in_progress\nassignee: {WHO}\n"))

    code, data = _finish(world, "t", "--walk", "--by", WHO)

    assert code == 0, data
    assert data["path"] == ["in_progress", "testing", "done"]
    assert _edges(_front(path)) == [("in_progress", "testing"), ("testing", "done")]


_RELAXED = (
    "      enforce_transitions: false\n"
    "      require_reason_on_transition: true\n"
    "      states: [open, claimed, in_progress, done]\n"
)


@pytest.mark.xfail(strict=True, reason=_HOOK_IGNORES_KB_SCHEMA)
def test_finish_walk_under_a_relaxed_workflow_takes_one_edge_with_its_reason(world):
    _schema(world, _RELAXED)
    path = world.add_entry(KB, _task("t", f"status: claimed\nassignee: {WHO}\n"))

    code, data = _finish(world, "t", "--walk", "--reason", "done early", "--by", WHO)

    assert code == 0, data
    front = _front(path)
    assert front["status"] == "done"
    assert front["status_reason"] == "done early"
    assert _edges(front) == [("claimed", "done")]


@pytest.mark.xfail(strict=True, reason=_HOOK_IGNORES_KB_SCHEMA)
def test_finish_under_a_relaxed_workflow_without_a_reason_is_refused(world):
    _schema(world, _RELAXED)
    path = world.add_entry(KB, _task("t", f"status: claimed\nassignee: {WHO}\n"))
    before = path.read_bytes()

    code, data = _finish(world, "t", "--walk", "--by", WHO)

    assert code == 1, data
    assert "reason" in data["error"]
    assert path.read_bytes() == before


# -- one write: a walk computed from a stale read is never applied ---------------


def test_a_walk_whose_start_is_not_the_files_status_is_refused(world):
    """The path is handed to the one validator, which checks it starts at the
    status the file holds when the write reads it (ADR-0042 decision 10)."""
    from pyrite.exceptions import ValidationError
    from pyrite.services.kb_service import KBService
    from pyrite.storage.file_operations import Set

    path = world.add_entry(KB, _task("t", f"status: blocked\nassignee: {WHO}\n"))
    before = path.read_bytes()
    svc = KBService(world.config, world.db)

    with pytest.raises(ValidationError, match="blocked"):
        svc.apply_operations("t", KB, [Set("status", "done")], status_path=("in_progress", "done"))
    assert path.read_bytes() == before


def test_a_path_that_skips_a_state_is_refused_edge_by_edge(world):
    """The hook judges every edge of the path it is handed, not only its two
    ends: claimed -> done is no declared edge, so a path of just those two is
    refused though it starts at the file's status and ends at the one set."""
    from pyrite.exceptions import ValidationError
    from pyrite.services.kb_service import KBService
    from pyrite.storage.file_operations import Set

    path = world.add_entry(KB, _task("t", f"status: claimed\nassignee: {WHO}\n"))
    before = path.read_bytes()
    svc = KBService(world.config, world.db)

    with pytest.raises(ValidationError, match="'claimed' to 'done'"):
        svc.apply_operations("t", KB, [Set("status", "done")], status_path=("claimed", "done"))
    assert path.read_bytes() == before


# -- one shape on CLI and MCP ----------------------------------------------------


def test_cli_and_mcp_finish_return_one_shape(world):
    world.add_entry(KB, _task("c", f"status: claimed\nassignee: {WHO}\n"))
    world.add_entry(KB, _task("m", f"status: claimed\nassignee: {WHO}\n"))

    _code, cli = _finish(world, "c", "--walk", "--reason", "r", "--by", WHO)
    mcp = world.mcp("write")._dispatch_tool(
        "task_finish", {"task_id": "m", "kb_name": KB, "walk": True, "reason": "r", "by": WHO}
    )

    cli.pop("outcome")
    assert cli.pop("task_id") == "c" and mcp.pop("task_id") == "m"
    assert cli == mcp


def test_mcp_finish_refusal_names_the_path(world):
    world.add_entry(KB, _task("m", f"status: claimed\nassignee: {WHO}\n"))
    result = world.mcp("write")._dispatch_tool(
        "task_finish", {"task_id": "m", "kb_name": KB, "by": WHO}
    )
    assert result["error_code"] == "VALIDATION_FAILED", result
    assert "claimed → in_progress → done" in result["error"]


# -- the holder rule over MCP (no signed-in user; tests/test_mcp_tools_record_the_caller.py
# has the signed-in cells) -----------------------------------------------------


def _mcp_finish(world, args: dict, principal=None) -> dict:
    return world.mcp("write")._dispatch_tool(
        "task_finish", {"task_id": "t", "kb_name": KB, "walk": True, **args}, principal=principal
    )


def test_mcp_the_holder_finishes(world):
    path = world.add_entry(KB, _task("t", "status: claimed\nassignee: agent:a\n"))
    result = _mcp_finish(world, {"by": "agent:a"})
    assert result.get("finished") is True, result
    assert [i["by"] for i in _front(path)["status_change_log"]] == ["agent:a", "agent:a"]


@pytest.mark.parametrize(
    "args",
    [{"by": "agent:b"}, {}, {"operator_override": True}],
    ids=["another-agent", "no-caller", "override-without-the-operator"],
)
def test_mcp_a_caller_who_does_not_hold_the_task_is_refused(world, args):
    """No principal is nobody, never the operator: the override is refused too."""
    path = world.add_entry(KB, _task("t", "status: claimed\nassignee: agent:a\n"))
    before = path.read_bytes()
    result = _mcp_finish(world, args)
    assert result.get("error_code") == "FORBIDDEN", result
    assert path.read_bytes() == before


def test_mcp_the_operator_finishes_by_the_override(world):
    """The local process (stdio) is the operator: with the override it
    finishes a task it does not hold, recorded as the operator."""
    from pyrite.services.access_policy import Principal

    path = world.add_entry(KB, _task("t", "status: claimed\nassignee: agent:a\n"))
    result = _mcp_finish(world, {"operator_override": True}, principal=Principal.local())
    assert result.get("finished") is True, result
    assert (result["by"], result["override"]) == ("operator", True)
    assert [(i["by"], i["override"]) for i in _front(path)["status_change_log"]] == [
        ("operator", True),
        ("operator", True),
    ]
