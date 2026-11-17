"""Tests for initial state enforcement on create (#1684).

A new entry of a type with a state machine starts in that machine's initial
state (e.g. "open" for task). Creating in any other status is refused across
create surfaces (CLI create, body-file frontmatter, MCP kb_create). Ingest
(add, import, sync) is exempt and records the file's status as given.
"""

import json
from pathlib import Path
import pytest
from pyrite.schema import KBSchema, TypeSchema
from pyrite.utils.yaml import load_yaml


TASK_REFUSAL = (
    "a new task is always created open; use `task claim` or `task update --status` to change it."
)


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), text
    return load_yaml(text.split("---", 2)[1])


def test_cli_create_task_status_in_progress_is_refused(cli_world):
    world = cli_world()
    res = world.cli(
        "create",
        "-k",
        "test-kb",
        "-t",
        "task",
        "--title",
        "Task in progress probe",
        "-f",
        "status=in_progress",
        "--format",
        "json",
    )
    assert res.exit_code != 0, res.output
    body = json.loads(res.output)
    assert body.get("error_code") == "VALIDATION_FAILED"
    assert TASK_REFUSAL in body.get("error", "")
    assert any(
        err.get("field") == "status" and err.get("rule") == "managed"
        for err in body.get("errors", [])
    )


def test_cli_create_task_status_open_is_accepted(cli_world):
    world = cli_world()
    res = world.cli(
        "create",
        "-k",
        "test-kb",
        "-t",
        "task",
        "--title",
        "Task open probe",
        "-f",
        "status=open",
        "--format",
        "json",
    )
    assert res.exit_code == 0, res.output
    body = json.loads(res.output)
    entry_id = body["entry_id"]
    path = next(world.kb_path("test-kb").rglob(f"{entry_id}.md"))
    fm = _frontmatter(path)
    assert fm["status"] == "open"


def test_body_file_frontmatter_status_in_progress_is_refused(cli_world):
    world = cli_world()
    fm_file = world.root / "task_in_progress.md"
    fm_file.write_text(
        "---\nstatus: done\npriority: 8\n---\nTicket body\n",
        encoding="utf-8",
    )
    res = world.cli(
        "create",
        "-k",
        "test-kb",
        "-t",
        "task",
        "--title",
        "Body file probe",
        "--body-file",
        str(fm_file),
        "--format",
        "json",
    )
    assert res.exit_code != 0, res.output
    body = json.loads(res.output)
    assert body.get("error_code") == "VALIDATION_FAILED"
    assert TASK_REFUSAL in body.get("error", "")


def test_body_file_frontmatter_status_open_is_accepted(cli_world):
    world = cli_world()
    fm_file = world.root / "task_open.md"
    fm_file.write_text(
        "---\nstatus: open\npriority: 8\n---\nTicket body\n",
        encoding="utf-8",
    )
    res = world.cli(
        "create",
        "-k",
        "test-kb",
        "-t",
        "task",
        "--title",
        "Body file open probe",
        "--body-file",
        str(fm_file),
        "--format",
        "json",
    )
    assert res.exit_code == 0, res.output
    body = json.loads(res.output)
    entry_id = body["entry_id"]
    path = next(world.kb_path("test-kb").rglob(f"{entry_id}.md"))
    fm = _frontmatter(path)
    assert fm["status"] == "open"


def test_mcp_kb_create_task_status_in_progress_is_refused(cli_world):
    world = cli_world()
    server = world.mcp()
    try:
        res = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-kb",
                "entry_type": "task",
                "title": "MCP task probe",
                "status": "in_progress",
            },
        )
        assert res.get("isError") is True or not res.get("created")
        assert res.get("error_code") == "VALIDATION_FAILED"
        assert TASK_REFUSAL in res.get("error", "")
    finally:
        server.close()


def test_mcp_kb_create_task_status_open_is_accepted(cli_world):
    world = cli_world()
    server = world.mcp()
    try:
        res = server._dispatch_tool(
            "kb_create",
            {
                "kb_name": "test-kb",
                "entry_type": "task",
                "title": "MCP task open probe",
                "status": "open",
            },
        )
        assert res.get("created") is True
        entry_id = res["entry_id"]
        path = next(world.kb_path("test-kb").rglob(f"{entry_id}.md"))
        fm = _frontmatter(path)
        assert fm["status"] == "open"
    finally:
        server.close()


def test_add_of_file_with_in_progress_status_is_accepted(cli_world):
    world = cli_world()
    src = world.root / "existing_task.md"
    src.write_text(
        "---\ntype: task\ntitle: Existing Task\nstatus: in_progress\npriority: 7\n---\nExisting content\n",
        encoding="utf-8",
    )
    res = world.cli("add", str(src), "-k", "test-kb", "--format", "json")
    assert res.exit_code == 0, res.output
    body = json.loads(res.output)
    entry_id = body.get("entry_id")
    path = next(world.kb_path("test-kb").rglob(f"{entry_id}.md"))
    fm = _frontmatter(path)
    assert fm["status"] == "in_progress"


def test_import_of_entries_with_in_progress_status_is_accepted(cli_world):
    world = cli_world()
    export = world.root / "tasks_export.json"
    export.write_text(
        json.dumps(
            [
                {
                    "title": "Imported Task",
                    "entry_type": "task",
                    "status": "in_progress",
                    "priority": 7,
                    "body": "Imported task body",
                }
            ]
        ),
        encoding="utf-8",
    )
    res = world.cli("import", str(export), "-k", "test-kb")
    assert res.exit_code == 0, res.output
    path = next(world.kb_path("test-kb").rglob("imported-task.md"))
    fm = _frontmatter(path)
    assert fm["status"] == "in_progress"


def test_type_without_workflow_accepts_any_status(cli_world):
    world = cli_world()
    res = world.cli(
        "create",
        "-k",
        "test-kb",
        "-t",
        "note",
        "--title",
        "Note status probe",
        "-f",
        "status=in_progress",
        "--format",
        "json",
    )
    assert res.exit_code == 0, res.output
    body = json.loads(res.output)
    entry_id = body["entry_id"]
    path = next(world.kb_path("test-kb").rglob(f"{entry_id}.md"))
    fm = _frontmatter(path)
    status_val = fm.get("status") or (
        fm.get("metadata", {}).get("status") if isinstance(fm.get("metadata"), dict) else None
    )
    assert status_val == "in_progress"


def test_plugin_type_with_custom_state_machine_refuses_non_initial(cli_world):
    world = cli_world()
    kb_path = world.kb_path("test-kb")
    (kb_path / "kb.yaml").write_text(
        "name: test-kb\n"
        "kb_type: generic\n"
        "types:\n"
        "  ticket:\n"
        "    state_machine:\n"
        "      states: [proposed, active, done]\n"
        "      initial: proposed\n"
        "      field: status\n",
        encoding="utf-8",
    )
    world.config.get_kb("test-kb").invalidate_schema_cache()

    # Create with non-initial status -> refused
    res_refused = world.cli(
        "create",
        "-k",
        "test-kb",
        "-t",
        "ticket",
        "--title",
        "Ticket active probe",
        "-f",
        "status=active",
        "--allow-undeclared",
        "--format",
        "json",
    )
    assert res_refused.exit_code != 0, res_refused.output
    body = json.loads(res_refused.output)
    assert body.get("error_code") == "VALIDATION_FAILED"
    assert "a new ticket is always created proposed" in body.get("error", "")

    # Create with initial status -> accepted
    res_accepted = world.cli(
        "create",
        "-k",
        "test-kb",
        "-t",
        "ticket",
        "--title",
        "Ticket proposed probe",
        "-f",
        "status=proposed",
        "--allow-undeclared",
        "--format",
        "json",
    )
    assert res_accepted.exit_code == 0, res_accepted.output
