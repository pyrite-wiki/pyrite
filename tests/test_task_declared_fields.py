"""Tests for TaskEntry declared fields reading and preservation (#1711).

TaskEntry declares date, start_date, end_date and assigned_at via Temporal
and Assignable protocols. When written in a task file, they must read back at
the field's own name on every reader (CLI pyrite get, MCP kb_get, REST), the
index column must hold them, and a no-op load and save must be byte-identical.
"""

import json
from pathlib import Path

from tests._world import build_world

from pyrite.config import KBConfig
from pyrite.models.task import TaskEntry
from pyrite.storage.repository import KBRepository, new_entry_text


def test_task_declared_fields_from_frontmatter():
    """TaskEntry.from_frontmatter reads date, start_date, end_date, assigned_at."""
    meta = {
        "id": "task-with-dates",
        "title": "Task with Dates",
        "type": "task",
        "status": "open",
        "date": "2026-11-18",
        "start_date": "2026-11-19",
        "end_date": "2026-11-20",
        "due_date": "2026-11-25",
        "assignee": "alice",
        "assigned_at": "2026-11-18T10:00:00Z",
    }
    task = TaskEntry.from_frontmatter(meta, "Some instructions.")
    assert task.date == "2026-11-18"
    assert task.start_date == "2026-11-19"
    assert task.end_date == "2026-11-20"
    assert task.due_date == "2026-11-25"
    assert task.assignee == "alice"
    assert task.assigned_at == "2026-11-18T10:00:00Z"
    # Declared fields must not be dumped into metadata
    assert "date" not in task.metadata
    assert "start_date" not in task.metadata
    assert "end_date" not in task.metadata
    assert "assigned_at" not in task.metadata

    fm = task.to_frontmatter()
    assert fm.get("date") == "2026-11-18"
    assert fm.get("start_date") == "2026-11-19"
    assert fm.get("end_date") == "2026-11-20"
    assert fm.get("due_date") == "2026-11-25"
    assert fm.get("assignee") == "alice"
    assert fm.get("assigned_at") == "2026-11-18T10:00:00Z"


def test_task_declared_fields_absent_clean_frontmatter():
    """Absent declared fields are not emitted in to_frontmatter."""
    task = TaskEntry.from_frontmatter({"id": "task-plain", "title": "Plain Task"}, "Body")
    fm = task.to_frontmatter()
    assert "date" not in fm
    assert "start_date" not in fm
    assert "end_date" not in fm
    assert "assigned_at" not in fm


def test_task_fields_read_across_cli_mcp_and_index(cli_world):
    """Writing date, start_date, end_date, assigned_at in a task file reads back on CLI, MCP, and index."""
    world = cli_world()
    task_text = (
        "---\n"
        "id: task-temporal-test\n"
        "title: Temporal Task\n"
        "type: task\n"
        "status: open\n"
        "date: '2026-11-18'\n"
        "start_date: '2026-11-19'\n"
        "end_date: '2026-11-20'\n"
        "due_date: '2026-11-25'\n"
        "assignee: user:alice\n"
        "assigned_at: '2026-11-18T10:00:00Z'\n"
        "---\n\n"
        "Task instructions body.\n"
    )
    world.add_entry("test-kb", task_text, filename="tasks/task-temporal-test.md")

    # 1. DB index row verification
    row = world.db.get_entry("task-temporal-test", "test-kb")
    assert row is not None
    assert row.get("date") == "2026-11-18"
    assert row.get("start_date") == "2026-11-19"
    assert row.get("end_date") == "2026-11-20"
    assert row.get("due_date") == "2026-11-25"
    assert row.get("assignee") == "user:alice"
    assert row.get("assigned_at") == "2026-11-18T10:00:00Z"

    # 2. CLI pyrite get
    cli_res = world.cli("get", "task-temporal-test", "-k", "test-kb", "--format", "json")
    assert cli_res.exit_code == 0, cli_res.output
    cli_data = json.loads(cli_res.stdout)
    assert cli_data.get("date") == "2026-11-18"
    assert cli_data.get("start_date") == "2026-11-19"
    assert cli_data.get("end_date") == "2026-11-20"
    assert cli_data.get("due_date") == "2026-11-25"
    assert cli_data.get("assignee") == "user:alice"
    assert cli_data.get("assigned_at") == "2026-11-18T10:00:00Z"
    # Ensure they are not in metadata
    meta = cli_data.get("metadata", {})
    assert "date" not in meta
    assert "start_date" not in meta
    assert "end_date" not in meta
    assert "assigned_at" not in meta

    # 3. MCP kb_get
    mcp_res = world.mcp()._dispatch_tool(
        "kb_get", {"kb_name": "test-kb", "entry_id": "task-temporal-test"}
    )
    assert "error" not in mcp_res, mcp_res
    entry = mcp_res.get("entry", mcp_res)
    assert entry.get("date") == "2026-11-18"
    assert entry.get("start_date") == "2026-11-19"
    assert entry.get("end_date") == "2026-11-20"
    assert entry.get("due_date") == "2026-11-25"
    assert entry.get("assignee") == "user:alice"
    assert entry.get("assigned_at") == "2026-11-18T10:00:00Z"


def test_task_fields_read_across_rest(tmp_path: Path):
    """Reading task with date fields through REST GET /api/entries/{id}."""
    kb_path = tmp_path / "rest-kb"
    world = build_world(
        tmp_path / "w",
        kbs=[KBConfig(name="test-kb", path=kb_path, kb_type="task")],
        rest=True,
    )
    try:
        task_text = (
            "---\n"
            "id: task-rest-test\n"
            "title: REST Task\n"
            "type: task\n"
            "status: open\n"
            "date: '2026-11-18'\n"
            "start_date: '2026-11-19'\n"
            "end_date: '2026-11-20'\n"
            "due_date: '2026-11-25'\n"
            "assignee: user:alice\n"
            "assigned_at: '2026-11-18T10:00:00Z'\n"
            "---\n\n"
            "REST task instructions.\n"
        )
        world.add_entry("test-kb", task_text, filename="tasks/task-rest-test.md")

        rest_res = world.client.get("/api/entries/task-rest-test", params={"kb": "test-kb"})
        assert rest_res.status_code == 200, rest_res.text
        rest_data = rest_res.json()
        assert rest_data.get("date") == "2026-11-18"
        assert rest_data.get("start_date") == "2026-11-19"
        assert rest_data.get("end_date") == "2026-11-20"
        assert rest_data.get("due_date") == "2026-11-25"
        assert rest_data.get("assignee") == "user:alice"
        assert rest_data.get("assigned_at") == "2026-11-18T10:00:00Z"
    finally:
        world.close()


def test_task_load_save_byte_identical(cli_world):
    """A no-op load and save of a task with declared fields is byte-identical."""
    world = cli_world()
    task_text = (
        "---\n"
        "id: task-roundtrip\n"
        "title: Roundtrip Task\n"
        "type: task\n"
        "status: open\n"
        "assignee: user:alice\n"
        'assigned_at: "2026-11-18T10:00:00Z"\n'
        "priority: 5\n"
        'date: "2026-11-18"\n'
        'start_date: "2026-11-19"\n'
        'end_date: "2026-11-20"\n'
        'due_date: "2026-11-25"\n'
        "---\n\n"
        "Body content here.\n"
    )
    path = world.add_entry("test-kb", task_text, filename="tasks/task-roundtrip.md")
    before_bytes = path.read_bytes()

    repo = KBRepository(world.config.get_kb("test-kb"))
    entry = repo.load("task-roundtrip")
    assert entry is not None
    assert entry.date == "2026-11-18"
    assert entry.start_date == "2026-11-19"
    assert entry.end_date == "2026-11-20"
    assert entry.assigned_at == "2026-11-18T10:00:00Z"

    # Save loaded entry back to disk
    path.unlink()
    repo.save(entry, exclusive=False)
    after_bytes = path.read_bytes()

    assert before_bytes == after_bytes
