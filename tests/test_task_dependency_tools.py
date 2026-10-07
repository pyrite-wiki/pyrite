"""Task dependency tools handle deep graphs and derived completion."""

from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.server.mcp_server import PyriteMCPServer


CHAIN_LENGTH = 1500
TASK_KB = "test-tasks"


def _write_task(tasks_dir: Path, task_id: str, *, status="open", parent=None, dependencies=()):
    lines = ["---", f"id: {task_id}", "type: task", f"title: {task_id}", f"status: {status}"]
    if parent:
        lines.append(f"parent: {parent}")
    if dependencies:
        lines.append("dependencies:")
        lines.extend(f"  - {dependency}" for dependency in dependencies)
    lines.extend(("---", ""))
    (tasks_dir / f"{task_id}.md").write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture(scope="module")
def task_tool_server(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("task-dependency-tools")
    kb_path = tmp_path / TASK_KB
    tasks_dir = kb_path / "tasks"
    tasks_dir.mkdir(parents=True)

    for index in range(CHAIN_LENGTH + 1):
        dependencies = [f"chain-{index + 1:04d}"] if index < CHAIN_LENGTH else []
        _write_task(tasks_dir, f"chain-{index:04d}", dependencies=dependencies)

    _write_task(tasks_dir, "derived-root", dependencies=["derived-parent", "open-parent"])
    _write_task(
        tasks_dir,
        "derived-parent",
        parent=None,
        dependencies=["derived-blocker-1"],
    )
    _write_task(tasks_dir, "derived-child", status="done", parent="derived-parent")
    _write_task(tasks_dir, "derived-blocker-1", dependencies=["derived-blocker-2"])
    _write_task(tasks_dir, "derived-blocker-2", dependencies=["derived-blocker-3"])
    _write_task(tasks_dir, "derived-blocker-3")
    _write_task(tasks_dir, "open-parent", dependencies=["open-leaf"])
    _write_task(tasks_dir, "open-leaf")

    _write_task(tasks_dir, "shared-root", dependencies=["shared-short", "shared-long"])
    _write_task(tasks_dir, "shared-short", dependencies=["shared-middle"])
    _write_task(tasks_dir, "shared-middle", dependencies=["shared-leaf"])
    _write_task(tasks_dir, "shared-leaf")
    _write_task(tasks_dir, "shared-long", dependencies=["shared-bridge"])
    _write_task(tasks_dir, "shared-bridge", dependencies=["shared-middle"])

    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name=TASK_KB, path=kb_path, kb_type="task", description="Task test KB")
        ],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    server = PyriteMCPServer(config, tier="admin")
    server.index_mgr.index_all()
    try:
        yield server
    finally:
        server.close()


def _call_task_tool(server, tool, task_id):
    return server._dispatch_tool(tool, {"task_id": task_id, "kb_name": TASK_KB})


def test_task_blocked_by_handles_a_1500_step_chain_through_mcp(task_tool_server):
    result = _call_task_tool(task_tool_server, "task_blocked_by", "chain-0000")

    assert result.get("count") == CHAIN_LENGTH, result
    assert result["blocked_by"][0]["id"] == "chain-0001"
    assert result["blocked_by"][-1]["id"] == f"chain-{CHAIN_LENGTH:04d}"


def test_task_critical_path_handles_a_1500_step_chain_through_mcp(task_tool_server):
    result = _call_task_tool(task_tool_server, "task_critical_path", "chain-0000")

    assert result.get("chain_length") == CHAIN_LENGTH, result
    assert result["critical_path"][0]["id"] == "chain-0001"
    assert result["critical_path"][-1]["id"] == f"chain-{CHAIN_LENGTH:04d}"


def test_dependency_tools_use_derived_status_for_blockers_and_critical_path(task_tool_server):
    blocked = _call_task_tool(task_tool_server, "task_blocked_by", "derived-root")
    path = _call_task_tool(task_tool_server, "task_critical_path", "derived-root")

    assert [row["id"] for row in blocked["blocked_by"]] == ["open-parent", "open-leaf"]
    assert all(row["derived"]["effective_status"] == "open" for row in blocked["blocked_by"])
    assert path["chain_length"] == 2
    assert [row["id"] for row in path["critical_path"]] == ["open-parent", "open-leaf"]
    assert all(row["derived"]["effective_status"] == "open" for row in path["critical_path"])


def test_critical_path_uses_the_longer_branch_through_a_shared_dependency(task_tool_server):
    result = _call_task_tool(task_tool_server, "task_critical_path", "shared-root")

    assert result["chain_length"] == 4
    assert [row["id"] for row in result["critical_path"]] == [
        "shared-long",
        "shared-bridge",
        "shared-middle",
        "shared-leaf",
    ]
