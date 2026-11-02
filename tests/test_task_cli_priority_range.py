"""Task writers reject priorities outside their documented range (#1394)."""

import json

import pytest

from pyrite.config import KBConfig
from pyrite.utils.yaml import load_yaml


@pytest.fixture
def task_world(cli_world, tmp_path):
    return cli_world(kbs=[KBConfig(name="tasks", path=tmp_path / "tasks", kb_type="task")])


def _create_task(world, *options):
    result = world.cli("task", "create", "Priority task", "-k", "tasks", *options)
    assert result.exit_code == 0, result.output
    entry_id = json.loads(result.stdout)["entry_id"]
    return world.kb_path("tasks") / "tasks" / f"{entry_id}.md", entry_id


def _frontmatter(path):
    return load_yaml(path.read_text(encoding="utf-8").split("---", 2)[1])


@pytest.mark.cli
@pytest.mark.parametrize("priority", [0, 11, -1, 100])
def test_create_rejects_priority_without_writing(task_world, priority):
    result = task_world.cli(
        "task", "create", "Invalid priority", "-k", "tasks", "--priority", str(priority)
    )

    assert result.exit_code == 2, result.output
    error = json.loads(result.stdout)
    assert error["error_code"] == "USAGE_ERROR"
    assert "--priority" in error["error"]
    assert not list(task_world.kb_path("tasks").rglob("*.md"))


@pytest.mark.cli
@pytest.mark.parametrize("priority", [0, 11, -1, 100])
def test_update_rejects_priority_without_changing_file(task_world, priority):
    path, entry_id = _create_task(task_world)
    before = path.read_bytes()
    result = task_world.cli(
        "task",
        "update",
        entry_id,
        "-k",
        "tasks",
        "--priority",
        str(priority),
        "--assignee",
        "agent:updated",
    )

    assert result.exit_code == 2, result.output
    error = json.loads(result.stdout)
    assert error["error_code"] == "USAGE_ERROR"
    assert "--priority" in error["error"]
    assert path.read_bytes() == before


@pytest.mark.cli
@pytest.mark.control(reason="creating a task at either valid boundary already works")
@pytest.mark.parametrize("priority", [1, 10])
def test_create_accepts_priority_boundaries(task_world, priority):
    path, _ = _create_task(task_world, "--priority", str(priority))
    assert _frontmatter(path)["priority"] == priority


@pytest.mark.cli
@pytest.mark.control(reason="updating a task to either valid boundary already works")
@pytest.mark.parametrize("priority", [1, 10])
def test_update_accepts_priority_boundaries(task_world, priority):
    path, entry_id = _create_task(task_world)
    result = task_world.cli("task", "update", entry_id, "-k", "tasks", "--priority", str(priority))
    assert result.exit_code == 0, result.output
    assert _frontmatter(path)["priority"] == priority


@pytest.mark.cli
@pytest.mark.control(reason="omitting priority on create still selects the existing default")
def test_create_keeps_default_priority(task_world):
    path, _ = _create_task(task_world)
    assert _frontmatter(path)["priority"] == 5


@pytest.mark.cli
@pytest.mark.control(reason="omitting priority on update must leave the existing priority alone")
def test_update_without_priority_preserves_it(task_world):
    path, entry_id = _create_task(task_world, "--priority", "8")
    result = task_world.cli(
        "task", "update", entry_id, "-k", "tasks", "--assignee", "agent:updated"
    )
    assert result.exit_code == 0, result.output
    frontmatter = _frontmatter(path)
    assert frontmatter["priority"] == 8
    assert frontmatter["assignee"] == "agent:updated"
