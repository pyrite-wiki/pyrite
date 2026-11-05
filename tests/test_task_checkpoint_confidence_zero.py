"""CLI and MCP coverage for `task checkpoint --confidence 0` (#1406).

A checkpoint with confidence 0 must record 0 (in `agent_context` and the
body line), and a checkpoint with no confidence must record nothing and
report no confidence value.
"""

import json

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.storage.repository import KBRepository
from tests._world import mcp_server as _make_mcp_server
from tests.test_task_cli_get import task_cli_env  # noqa: F401  (fixture)

runner = CliRunner()


@pytest.mark.cli
def test_cli_checkpoint_confidence_zero_overwrites_previous(task_cli_env):  # noqa: F811
    task_id = task_cli_env["task_id"]
    args = ["task", "checkpoint", task_id, "-k", "test-tasks", "--format", "json"]

    first = runner.invoke(app, [*args, "-m", "First pass", "--confidence", "0.8"])
    assert first.exit_code == 0, first.output
    assert json.loads(first.stdout)["confidence"] == 0.8

    second = runner.invoke(app, [*args, "-m", "Found a blocker", "--confidence", "0"])
    assert second.exit_code == 0, second.output
    assert json.loads(second.stdout)["confidence"] == 0

    repo = KBRepository(task_cli_env["config"].get_kb("test-tasks"))
    entry = repo.load(task_id)
    assert entry.agent_context["confidence"] == 0
    assert "**Confidence**: 0%" in entry.body


@pytest.mark.cli
def test_cli_checkpoint_without_confidence_reports_none(task_cli_env):  # noqa: F811
    task_id = task_cli_env["task_id"]
    args = ["task", "checkpoint", task_id, "-k", "test-tasks", "--format", "json"]

    first = runner.invoke(app, [*args, "-m", "First pass", "--confidence", "0.8"])
    assert first.exit_code == 0, first.output

    second = runner.invoke(app, [*args, "-m", "Still going"])
    assert second.exit_code == 0, second.output
    assert json.loads(second.stdout)["confidence"] is None

    repo = KBRepository(task_cli_env["config"].get_kb("test-tasks"))
    entry = repo.load(task_id)
    assert entry.agent_context["confidence"] == 0.8
    assert entry.body.count("**Confidence**") == 1


@pytest.fixture
def mcp_task_server():
    with _make_mcp_server(
        [{"name": "test-tasks", "kb_type": "task", "subdirs": ["tasks"]}], tier="admin"
    ) as env:
        yield env


def test_mcp_checkpoint_confidence_zero_and_omitted(mcp_task_server):
    server = mcp_task_server["server"]
    created = server._dispatch_tool(
        "task_create",
        {"kb_name": "test-tasks", "title": "MCP checkpoint task"},
        client_id="stdio",
        client_kind="local",
    )
    assert "error" not in created, created
    task_id = created["entry_id"]

    first = server._dispatch_tool(
        "task_checkpoint",
        {
            "task_id": task_id,
            "kb_name": "test-tasks",
            "message": "First pass",
            "confidence": 0.8,
        },
        client_id="stdio",
        client_kind="local",
    )
    assert first.get("confidence") == 0.8, first

    second = server._dispatch_tool(
        "task_checkpoint",
        {
            "task_id": task_id,
            "kb_name": "test-tasks",
            "message": "Found a blocker",
            "confidence": 0,
        },
        client_id="stdio",
        client_kind="local",
    )
    assert second.get("confidence") == 0, second

    repo = KBRepository(mcp_task_server["test-tasks"])
    entry = repo.load(task_id)
    assert entry.agent_context["confidence"] == 0
    assert "**Confidence**: 0%" in entry.body

    third = server._dispatch_tool(
        "task_checkpoint",
        {"task_id": task_id, "kb_name": "test-tasks", "message": "Still going"},
        client_id="stdio",
        client_kind="local",
    )
    assert third.get("confidence") is None, third
    assert repo.load(task_id).agent_context["confidence"] == 0
