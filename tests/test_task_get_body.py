"""Regression tests for task get and MCP task_status returning task body (#1681).

Acceptance criteria:
- `task get --format json` and MCP `task_status` both carry `body`, byte-equal
  to the file's body for a short body.
- Over the MCP chunk default, MCP returns the ADR-0034 truncation markers
  (`body_truncated`, `body_length`, `body_offset`, `body_chunk_size`),
  while CLI `task get` returns the full body.
- One builder serves both (CLI and MCP return the exact same keys).
- Regimes: empty body; body over the chunk default; file missing behind an index row.
- Rich output of `task get` prints the body under the fields.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig
from pyrite.services.body_bounds import DEFAULT_BODY_CHUNK


@pytest.fixture
def task_body_world(cli_world, tmp_path: Path):
    """World with a task KB and helper methods for CLI and MCP calls."""
    kb_path = tmp_path / "task-body-kb"
    (kb_path / "tasks").mkdir(parents=True)

    world = cli_world(
        kbs=[KBConfig(name="test-tasks", path=kb_path, kb_type="task", description="Test task KB")]
    )
    return world, kb_path


EXPECTED_KEYS = {
    "agent_context",
    "assignee",
    "body",
    "children",
    "dependencies",
    "derived",
    "due_date",
    "evidence",
    "id",
    "kb_name",
    "parent",
    "priority",
    "status",
    "title",
}


@pytest.mark.cli
def test_task_get_and_mcp_status_share_exact_same_keys(task_body_world):
    """CLI task get and MCP task_status share the exact same dictionary keys (#1681)."""
    world, _ = task_body_world
    res = world.cli(
        "task",
        "create",
        "Key parity task",
        "-k",
        "test-tasks",
        "--body",
        "Task instructions here.",
        "--format",
        "json",
    )
    assert res.exit_code == 0, res.output
    task_id = json.loads(res.output)["entry_id"]

    # 1. CLI task get
    cli_res = world.cli("task", "get", task_id, "-k", "test-tasks", "--format", "json")
    assert cli_res.exit_code == 0, cli_res.output
    cli_data = json.loads(cli_res.output)

    # 2. MCP task_status
    mcp_server = world.mcp()
    mcp_data = mcp_server._task_status({"task_id": task_id, "kb_name": "test-tasks"})

    assert set(cli_data.keys()) - {"outcome"} == EXPECTED_KEYS
    assert cli_data.get("outcome") == "done"
    assert set(mcp_data.keys()) == EXPECTED_KEYS
    assert set(cli_data.keys()) - {"outcome"} == set(mcp_data.keys())


@pytest.mark.parametrize(
    "body_text",
    [
        "Simple body paragraph.",
        "Multi-line\nbody with `backticks` and $symbols and 'quotes'.\n\nSecond paragraph.",
        "Line with trailing whitespace   \nand special chars: <>&\"'#*",
    ],
)
@pytest.mark.cli
def test_task_get_body_byte_equal_to_file(task_body_world, body_text: str):
    """CLI task get and MCP task_status both carry `body`, byte-equal to disk (#1681)."""
    world, _ = task_body_world
    res = world.cli(
        "task",
        "create",
        "Byte equality task",
        "-k",
        "test-tasks",
        "--body",
        body_text,
        "--format",
        "json",
    )
    assert res.exit_code == 0, res.output
    task_id = json.loads(res.output)["entry_id"]

    cli_res = world.cli("task", "get", task_id, "-k", "test-tasks", "--format", "json")
    assert cli_res.exit_code == 0, cli_res.output
    cli_data = json.loads(cli_res.output)
    assert cli_data["body"] == body_text
    assert "body_truncated" not in cli_data

    mcp_server = world.mcp()
    mcp_data = mcp_server._task_status({"task_id": task_id, "kb_name": "test-tasks"})
    assert mcp_data["body"] == body_text
    assert "body_truncated" not in mcp_data


@pytest.mark.cli
def test_task_get_empty_body(task_body_world):
    """An empty body returns empty string and carries no truncation markers (#1681)."""
    world, _ = task_body_world
    res = world.cli("task", "create", "Empty body task", "-k", "test-tasks", "--format", "json")
    assert res.exit_code == 0, res.output
    task_id = json.loads(res.output)["entry_id"]

    cli_res = world.cli("task", "get", task_id, "-k", "test-tasks", "--format", "json")
    assert cli_res.exit_code == 0, cli_res.output
    cli_data = json.loads(cli_res.output)
    assert cli_data["body"] == ""
    assert "body_truncated" not in cli_data

    mcp_server = world.mcp()
    mcp_data = mcp_server._task_status({"task_id": task_id, "kb_name": "test-tasks"})
    assert mcp_data["body"] == ""
    assert "body_truncated" not in mcp_data


@pytest.mark.cli
def test_mcp_task_status_truncates_over_chunk_default_while_cli_returns_full(task_body_world):
    """Over the MCP chunk default, MCP returns truncation markers, CLI returns full (#1681)."""
    world, _ = task_body_world
    long_body = "x" * (DEFAULT_BODY_CHUNK + 500)

    res = world.cli(
        "task",
        "create",
        "Long body task",
        "-k",
        "test-tasks",
        "--body",
        long_body,
        "--format",
        "json",
    )
    assert res.exit_code == 0, res.output
    task_id = json.loads(res.output)["entry_id"]

    # CLI returns full body without truncation markers
    cli_res = world.cli("task", "get", task_id, "-k", "test-tasks", "--format", "json")
    assert cli_res.exit_code == 0, cli_res.output
    cli_data = json.loads(cli_res.output)
    assert cli_data["body"] == long_body
    assert "body_truncated" not in cli_data

    # MCP truncates and attaches ADR-0034 markers
    mcp_server = world.mcp()
    mcp_data = mcp_server._task_status({"task_id": task_id, "kb_name": "test-tasks"})
    assert mcp_data["body_truncated"] is True
    assert mcp_data["body_length"] == len(long_body)
    assert mcp_data["body_offset"] == 0
    assert mcp_data["body_chunk_size"] == DEFAULT_BODY_CHUNK
    assert mcp_data["body"] == long_body[:DEFAULT_BODY_CHUNK]


@pytest.mark.cli
def test_task_get_and_mcp_status_refuse_when_file_missing_behind_index_row(task_body_world):
    """When a task's file is gone behind an index row, both CLI and MCP refuse with NOT_FOUND."""
    world, kb_path = task_body_world
    res = world.cli(
        "task",
        "create",
        "Orphaned index row task",
        "-k",
        "test-tasks",
        "--body",
        "Ephemeral body",
        "--format",
        "json",
    )
    assert res.exit_code == 0, res.output
    task_id = json.loads(res.output)["entry_id"]

    # Delete file from disk behind index row
    task_file = kb_path / "tasks" / f"{task_id}.md"
    assert task_file.is_file()
    task_file.unlink()

    # CLI task get refuses NOT_FOUND
    cli_res = world.cli("task", "get", task_id, "-k", "test-tasks", "--format", "json")
    assert cli_res.exit_code != 0
    cli_err = json.loads(cli_res.output)
    assert cli_err.get("error_code") == "NOT_FOUND"

    # MCP task_status refuses NOT_FOUND
    mcp_server = world.mcp()
    mcp_err = mcp_server._task_status({"task_id": task_id, "kb_name": "test-tasks"})
    assert mcp_err.get("error_code") == "NOT_FOUND"
    assert "not found" in mcp_err.get("error", "").lower()


@pytest.mark.cli
def test_task_get_rich_prints_body_under_fields(task_body_world):
    """Rich output of task get prints the body under the metadata fields (#1681)."""
    world, _ = task_body_world
    agent_instruction = "Crucial autonomous instructions for the agent."
    res = world.cli(
        "task",
        "create",
        "Rich body view task",
        "-k",
        "test-tasks",
        "--body",
        agent_instruction,
        "--format",
        "json",
    )
    assert res.exit_code == 0, res.output
    task_id = json.loads(res.output)["entry_id"]

    with world.pointed():
        rich_res = CliRunner().invoke(
            app,
            ["task", "get", task_id, "-k", "test-tasks", "--format", "rich"],
            env={"COLUMNS": "120"},
        )
    assert rich_res.exit_code == 0, rich_res.output
    assert agent_instruction in rich_res.output
    # Body appears after metadata fields
    status_pos = rich_res.output.find("Status: open")
    body_pos = rich_res.output.find(agent_instruction)
    assert status_pos != -1
    assert body_pos > status_pos
