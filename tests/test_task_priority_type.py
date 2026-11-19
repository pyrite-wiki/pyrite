"""`priority` is one JSON type, an integer, from every task reader (#1759).

The index column is a string, so a row can hold `"8"`, a word (`medium`,
from a KB schema that retyped the field, #554) or nothing. The property:
one function shapes a task row for all readers (#1503's rule), so the
value every surface reports is the integer `coerce_task_priority` reads,
not whichever caller remembered to coerce. Before this change the
surfaces that happened to coerce agreed, and `TaskService.get_task` --
the single-task reader the MCP task-graph tools resolve through --
returned the raw row: `"8"`, `"medium"`, `None`.

Expected values are written by hand from that property: a created task
with priority 8 reads as the int `8` everywhere; a row holding `"8"`
reads 8, a row holding `medium` reads 5 (the word's value on the 1-10
scale is the default), a row holding nothing reads 5.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pyrite.config import KBConfig
from pyrite.server.api import create_app, get_db
from pyrite.services.task_service import TaskService

KB = "test-tasks"


@pytest.fixture
def task_world(cli_world, tmp_path: Path):
    kb_path = tmp_path / "priority-kb"
    (kb_path / "tasks").mkdir(parents=True)
    return cli_world(
        kbs=[KBConfig(name=KB, path=kb_path, kb_type="task", description="Task priority KB")]
    )


def _create(world, priority: int = 8) -> dict:
    res = world.cli(
        "task",
        "create",
        "Priority task",
        "-k",
        KB,
        "--priority",
        str(priority),
        "--format",
        "json",
    )
    assert res.exit_code == 0, res.output
    return json.loads(res.output)


def _cli_get(world, task_id: str) -> dict:
    res = world.cli("task", "get", task_id, "-k", KB, "--format", "json")
    assert res.exit_code == 0, res.output
    return json.loads(res.output)


def _cli_list_row(world, task_id: str) -> dict:
    res = world.cli("task", "list", "-k", KB, "--format", "json")
    assert res.exit_code == 0, res.output
    return next(t for t in json.loads(res.output)["tasks"] if t["id"] == task_id)


def _rest_client(world) -> TestClient:
    app = create_app(config=world.config)
    app.dependency_overrides[get_db] = lambda: world.db
    return TestClient(app)


def _set_row_priority(world, task_id: str, value) -> None:
    """Write the index row's priority column directly: the row a sync
    taken before the model's reading existed would have left behind."""
    con = sqlite3.connect(world.config.settings.index_path)
    try:
        con.execute("UPDATE entry SET priority = ? WHERE id = ?", (value, task_id))
        con.commit()
    finally:
        con.close()


def _assert_int(value, expected: int) -> None:
    assert isinstance(value, int) and not isinstance(value, bool), repr(value)
    assert value == expected


@pytest.mark.cli
def test_a_created_tasks_priority_is_the_int_8_on_every_surface(task_world):
    world = task_world
    created = _create(world, priority=8)
    task_id = created["entry_id"]

    _assert_int(created["priority"], 8)  # task create output
    _assert_int(_cli_get(world, task_id)["priority"], 8)  # task get (CLI JSON)
    _assert_int(_cli_list_row(world, task_id)["priority"], 8)  # task list

    updated = world.cli("task", "update", task_id, "-k", KB, "--priority", "8", "--format", "json")
    assert updated.exit_code == 0, updated.output
    _assert_int(json.loads(updated.output)["priority"], 8)  # task update output

    mcp = world.mcp()
    _assert_int(
        mcp._task_status({"task_id": task_id, "kb_name": KB})["priority"], 8
    )  # MCP task_status
    _assert_int(
        mcp._kb_get({"entry_id": task_id, "kb_name": KB})["entry"]["priority"], 8
    )  # MCP kb_get

    client = _rest_client(world)
    listing = client.get("/api/tasks", params={"kb": KB})
    assert listing.status_code == 200, listing.text
    row = next(t for t in listing.json()["tasks"] if t["id"] == task_id)
    _assert_int(row["priority"], 8)  # REST task list
    entry = client.get(f"/api/entries/{task_id}", params={"kb": KB})
    assert entry.status_code == 200, entry.text
    _assert_int(entry.json()["priority"], 8)  # REST entry


@pytest.mark.cli
@pytest.mark.parametrize(
    ("row_value", "expected"),
    [("8", 8), ("medium", 5), (None, 5)],
    ids=["a string number", "a word", "no value"],
)
def test_a_row_holding_a_string_a_word_or_nothing_reads_as_an_int(task_world, row_value, expected):
    world = task_world
    task_id = _create(world, priority=8)["entry_id"]
    _set_row_priority(world, task_id, row_value)

    svc = TaskService(world.config, world.db)
    row = svc.get_task(task_id, KB, readable_kbs=None)
    assert row is not None
    _assert_int(row["priority"], expected)  # the single-task reader itself
    _assert_int(svc.get_task_detail(task_id, KB, readable_kbs=None)["priority"], expected)
    _assert_int(_cli_get(world, task_id)["priority"], expected)
    _assert_int(_cli_list_row(world, task_id)["priority"], expected)
    _assert_int(
        world.mcp()._task_status({"task_id": task_id, "kb_name": KB})["priority"],
        expected,
    )
