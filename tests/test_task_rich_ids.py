"""Tests for task list and task get (rich) showing full task and parent IDs (#1340).

Regression coverage for:
- Task ID and Parent ID in `task list --format rich` are not sliced to 12 chars.
- Child task IDs in `task get --format rich` are not sliced to 12 chars.
- Long IDs render fully on wide terminals and fold rather than being truncated on narrow terminals.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig


@pytest.fixture
def long_id_task_world(cli_world, tmp_path: Path):
    """Temp task KB with parent and child tasks having IDs longer than 12 characters."""
    kb_path = tmp_path / "tasks-kb"
    (kb_path / "tasks").mkdir(parents=True)

    world = cli_world(
        kbs=[KBConfig(name="test-tasks", path=kb_path, kb_type="task", description="Test task KB")]
    )

    # Create parent task with id > 12 chars: parent-epic-for-login
    parent_res = world.cli(
        "task", "create", "Parent epic for login", "-k", "test-tasks", "--format", "json"
    )
    parent_id = json.loads(parent_res.output)["entry_id"]

    # Create child task with id > 12 chars: write-the-login-form
    child_res = world.cli(
        "task",
        "create",
        "Write the login form",
        "-k",
        "test-tasks",
        "--parent",
        parent_id,
        "--format",
        "json",
    )
    child_id = json.loads(child_res.output)["entry_id"]

    # Create task with exactly 12 chars: exactly-twlv
    exact_res = world.cli("task", "create", "Exactly twlv", "-k", "test-tasks", "--format", "json")
    exact_id = json.loads(exact_res.output)["entry_id"]
    assert len(exact_id) == 12

    return {
        "world": world,
        "parent_id": parent_id,
        "child_id": child_id,
        "exact_id": exact_id,
    }


@pytest.mark.cli
def test_task_list_rich_displays_full_ids_and_parent_id(long_id_task_world):
    """`task list --format rich` displays the full task ID and parent ID without 12-char cut."""
    world = long_id_task_world["world"]
    parent_id = long_id_task_world["parent_id"]
    child_id = long_id_task_world["child_id"]
    exact_id = long_id_task_world["exact_id"]

    with world.pointed():
        res = CliRunner().invoke(
            app,
            ["task", "list", "-k", "test-tasks", "--format", "rich"],
            env={"COLUMNS": "200"},
        )
    assert res.exit_code == 0, res.output

    # The full IDs must be present in the rich table output
    assert parent_id in res.output
    assert child_id in res.output
    assert exact_id in res.output


@pytest.mark.cli
def test_task_get_rich_displays_full_child_ids(long_id_task_world):
    """`task get <parent> --format rich` displays child tasks by their full IDs."""
    world = long_id_task_world["world"]
    parent_id = long_id_task_world["parent_id"]
    child_id = long_id_task_world["child_id"]

    with world.pointed():
        res = CliRunner().invoke(
            app,
            ["task", "get", parent_id, "-k", "test-tasks", "--format", "rich"],
            env={"COLUMNS": "200"},
        )
    assert res.exit_code == 0, res.output

    # The full child ID must appear in the children section
    assert child_id in res.output


@pytest.mark.cli
def test_task_list_rich_narrow_terminal_folds_without_crash(long_id_task_world):
    """In a narrow terminal (COLUMNS=60), task list folds IDs across lines without truncating."""
    world = long_id_task_world["world"]
    with world.pointed():
        res = CliRunner().invoke(
            app,
            ["task", "list", "-k", "test-tasks", "--format", "rich"],
            env={"COLUMNS": "60"},
        )
    assert res.exit_code == 0, res.output
    # Long IDs fold across lines rather than being cut or replaced with ellipsis
    assert "..." not in res.output
    # Reassemble columns from table rows to verify full IDs are preserved across folded cells
    lines = res.output.splitlines()
    id_col = "".join(line.split("│")[1].strip() for line in lines if len(line.split("│")) >= 3)
    parent_col = "".join(line.split("│")[6].strip() for line in lines if len(line.split("│")) >= 7)
    assert "write-the-login-form" in id_col
    assert "parent-epic-for-login" in parent_col
