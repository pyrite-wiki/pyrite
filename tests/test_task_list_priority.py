"""Priority filtering for the task service and CLI (#309)."""

import json

import pytest
from typer.testing import CliRunner

from tests.cli_help import plain

from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.task_service import TaskService
from pyrite.storage.database import PyriteDB

runner = CliRunner()


@pytest.fixture
def task_list_env(tmp_path, monkeypatch):
    tasks_path = tmp_path / "tasks-kb"
    (tasks_path / "tasks").mkdir(parents=True)
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="test-tasks", path=tasks_path, kb_type="task")],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        db.register_kb(name="test-tasks", kb_type="task", path=str(tasks_path))
        svc = TaskService(config, db)
        svc.create_task(kb_name="test-tasks", title="Seven open", priority=7)
        claimed = svc.create_task(kb_name="test-tasks", title="Seven claimed", priority=7)
        svc.claim_task(claimed["entry_id"], "test-tasks", "agent:test")
        svc.create_task(kb_name="test-tasks", title="Five open", priority=5)
        monkeypatch.setattr("pyrite.cli.context.load_config", lambda: config)
        yield svc
    finally:
        db.close()


@pytest.mark.parametrize(
    ("priority", "status", "expected"),
    [
        (7, None, {"Seven open", "Seven claimed"}),
        (7, "open", {"Seven open"}),
        (None, None, {"Seven open", "Seven claimed", "Five open"}),
        (0, None, set()),
    ],
)
def test_service_priority_filter(task_list_env, priority, status, expected):
    tasks = task_list_env.list_tasks(kb_name="test-tasks", priority=priority, status=status)
    assert {task["title"] for task in tasks} == expected


@pytest.mark.parametrize(
    ("kb_names", "expected"),
    [({"test-tasks"}, {"Seven open", "Seven claimed"}), (set(), set())],
)
def test_service_priority_filter_respects_kb_scope(task_list_env, tmp_path, kb_names, expected):
    other_path = tmp_path / "other-kb"
    (other_path / "tasks").mkdir(parents=True)
    task_list_env.config.add_kb(KBConfig(name="other-tasks", path=other_path, kb_type="task"))
    task_list_env.db.register_kb(name="other-tasks", kb_type="task", path=str(other_path))
    task_list_env.create_task(kb_name="other-tasks", title="Other seven", priority=7)

    tasks = task_list_env.list_tasks(priority=7, kb_names=kb_names)
    assert {task["title"] for task in tasks} == expected


@pytest.mark.cli
@pytest.mark.parametrize("fmt", ["json", "rich"])
@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (["--priority", "7"], {"Seven open", "Seven claimed"}),
        (["--priority", "7", "--status", "open"], {"Seven open"}),
        ([], {"Seven open", "Seven claimed", "Five open"}),
        (["--priority", "0"], set()),
    ],
)
def test_cli_priority_filter(task_list_env, fmt, filters, expected):
    result = runner.invoke(app, ["task", "list", "-k", "test-tasks", "-f", fmt, *filters])
    assert result.exit_code == 0, result.output
    if fmt == "json":
        data = json.loads(result.stdout)
        assert data["count"] == len(expected)
        assert {task["title"] for task in data["tasks"]} == expected
    else:
        for title in ("Seven open", "Seven claimed", "Five open"):
            assert (title in result.stdout) == (title in expected)


@pytest.mark.cli
def test_task_list_help_includes_priority():
    result = runner.invoke(app, ["task", "list", "--help"])
    assert result.exit_code == 0, result.output
    assert "--priority" in plain(result.stdout)
