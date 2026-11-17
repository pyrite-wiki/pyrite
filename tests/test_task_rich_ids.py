"""Tests for task list and task get (rich) showing full task and parent IDs (#1340).

Regression coverage for:
- Task ID and Parent ID in `task list --format rich` are not sliced to 12 chars.
- Child task IDs in `task get --format rich` are not sliced to 12 chars.
- Long IDs render fully on wide terminals and fold rather than being truncated on narrow terminals.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.task_service import TaskService
from pyrite.storage.database import PyriteDB

runner = CliRunner()


@pytest.fixture
def long_id_task_cli_env():
    """Temp task KB with parent and child tasks having IDs longer than 12 characters."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tasks_path = tmpdir / "tasks-kb"
        (tasks_path / "tasks").mkdir(parents=True)

        kb_config = KBConfig(
            name="test-tasks", path=tasks_path, kb_type="task", description="Test task KB"
        )
        config = PyriteConfig(
            knowledge_bases=[kb_config],
            settings=Settings(index_path=tmpdir / "index.db"),
        )
        db = PyriteDB(config.settings.index_path)
        db.register_kb(
            name="test-tasks", kb_type="task", path=str(tasks_path), description="Test task KB"
        )
        svc = TaskService(config, db)

        # Create parent task with id > 12 chars: parent-epic-for-login
        parent = svc.create_task(
            kb_name="test-tasks",
            title="Parent epic for login",
        )
        # Create child task with id > 12 chars: write-the-login-form
        child = svc.create_task(
            kb_name="test-tasks",
            title="Write the login form",
            parent=parent["entry_id"],
        )
        # Create task with exactly 12 chars: exactly twel
        exact = svc.create_task(
            kb_name="test-tasks",
            title="Exactly twel",
        )
        db.close()

        with patch("pyrite.cli.context.load_config", return_value=config):
            yield {
                "config": config,
                "parent_id": parent["entry_id"],
                "child_id": child["entry_id"],
                "exact_id": exact["entry_id"],
            }


@pytest.mark.cli
def test_task_list_rich_displays_full_ids_and_parent_id(long_id_task_cli_env):
    """`task list --format rich` displays the full task ID and parent ID without 12-char cut."""
    parent_id = long_id_task_cli_env["parent_id"]
    child_id = long_id_task_cli_env["child_id"]
    exact_id = long_id_task_cli_env["exact_id"]

    res = runner.invoke(
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
def test_task_get_rich_displays_full_child_ids(long_id_task_cli_env):
    """`task get <parent> --format rich` displays child tasks by their full IDs."""
    parent_id = long_id_task_cli_env["parent_id"]
    child_id = long_id_task_cli_env["child_id"]

    res = runner.invoke(
        app,
        ["task", "get", parent_id, "-k", "test-tasks", "--format", "rich"],
        env={"COLUMNS": "200"},
    )
    assert res.exit_code == 0, res.output

    # The full child ID must appear in the children section
    assert child_id in res.output


@pytest.mark.cli
def test_task_list_rich_narrow_terminal_folds_without_crash(long_id_task_cli_env):
    """In a narrow terminal (COLUMNS=60), task list folds IDs across lines without truncating."""
    res = runner.invoke(
        app,
        ["task", "list", "-k", "test-tasks", "--format", "rich"],
        env={"COLUMNS": "60"},
    )
    assert res.exit_code == 0, res.output
    # Long IDs fold across lines rather than being cut or replaced with ellipsis
    assert "..." not in res.output
    assert "write-th" in res.output and "form" in res.output
    assert "parent-" in res.output and "login" in res.output
