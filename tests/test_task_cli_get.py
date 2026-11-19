"""CLI tests for `pyrite task get` and the deprecated `task status` alias.

Regression coverage for
`bug-task-status-single-item-json-read-intermittently-empty`:

- `task get` is the new single-item lookup name (mirrors `pyrite get`).
- `task status` still works as a deprecated alias for one release, with the
  deprecation notice on stderr so JSON on stdout stays clean & parseable.
- Both read the same fresh path as `task list`, so a claim made just before
  the read is reflected (no read-after-write divergence).
"""

import json
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
def task_cli_env():
    """Temp task KB wired into a patched load_config for CLI invocation."""
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
        created = svc.create_task(kb_name="test-tasks", title="CLI lookup task")
        task_id = created["entry_id"]
        db.close()

        with patch("pyrite.cli.context.load_config", return_value=config):
            yield {"config": config, "task_id": task_id}


@pytest.mark.cli
def test_task_get_json_is_parseable(task_cli_env):
    """`task get -f json` returns clean parseable JSON for the task."""
    result = runner.invoke(
        app, ["task", "get", task_cli_env["task_id"], "-k", "test-tasks", "-f", "json"]
    )
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["id"] == task_cli_env["task_id"]
    assert data["title"] == "CLI lookup task"


@pytest.mark.cli
def test_task_status_alias_warns_on_stderr_but_keeps_stdout_clean(task_cli_env):
    """Deprecated `task status` still works; the deprecation notice goes to
    stderr so stdout stays valid JSON."""
    result = runner.invoke(
        app, ["task", "status", task_cli_env["task_id"], "-k", "test-tasks", "--format", "json"]
    )
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)  # stdout must be pure JSON
    assert data["id"] == task_cli_env["task_id"]
    warning = " ".join(result.stderr.split())
    assert "`task status` is deprecated and will be removed in 0.27" in warning
    assert "use `task get` instead" in warning


@pytest.mark.cli
def test_task_get_reflects_claim_made_before_read(task_cli_env):
    """A claim transition is visible to the very next `task get` — the
    single-item read uses the same fresh path as the list view."""
    config = task_cli_env["config"]
    task_id = task_cli_env["task_id"]

    db = PyriteDB(config.settings.index_path)
    TaskService(config, db).claim_task(task_id, "test-tasks", "agent:cli")
    db.close()

    result = runner.invoke(app, ["task", "get", task_id, "-k", "test-tasks", "-f", "json"])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["status"] == "claimed"
    assert data["assignee"] == "agent:cli"
