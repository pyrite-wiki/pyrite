"""Regression tests for research-queue init template and help parity (#1663).

Acceptance criteria:
- `pyrite init --help` names every template `pyrite init -t` accepts, including `task`;
  a test fails if they differ in either direction.
- `pyrite init -t research-queue -p q --name q`, then `pyrite task create "Q" -k q --field target_kb=x`,
  then `task claim`, `task update --status in_progress` and `--status done` all succeed,
  and `pyrite index health` has no rows for `q`.
- `pyrite orient -k q` shows the ticket fields with their descriptions.
- The `task` type's definition still lives only in `TASK_KB_PRESET`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.cli.init_command import available_templates
from pyrite.models.task import TASK_KB_PRESET
from pyrite.utils.yaml import load_yaml_file

runner = CliRunner()


@pytest.mark.cli
def test_init_help_lists_every_accepted_template_bidirectionally():
    """`pyrite init --help` lists every template accepted by `init_kb()`, and the reverse."""
    res = runner.invoke(app, ["init", "--help"])
    assert res.exit_code == 0, res.output

    accepted = set(available_templates())
    assert "task" in accepted
    assert "research-queue" in accepted

    # Every accepted template must appear in the help output
    for name in accepted:
        assert name in res.output, f"Accepted template {name!r} missing from `pyrite init --help`"

    # Extract the template list from the help text between "Template:" and "[required]"
    match = re.search(r"Template:(.*?)\[required\]", res.output, re.DOTALL)
    assert match is not None, f"Could not find 'Template:' list in help output:\n{res.output}"
    raw_templates = match.group(1).replace("│", "").replace("\n", " ")
    help_templates = {t.strip() for t in raw_templates.split(",") if t.strip()}

    assert help_templates == accepted, (
        f"Mismatch between init help templates and available_templates():\n"
        f"In help only: {help_templates - accepted}\n"
        f"In available only: {accepted - help_templates}"
    )


@pytest.mark.cli
def test_research_queue_init_lifecycle_and_health(cli_world, tmp_path: Path):
    """pyrite init -t research-queue creates a functional tracker KB (#1663).

    Init -> task create with --field target_kb=x -> claim -> in_progress -> done -> index health.
    """
    world = cli_world()
    q_path = tmp_path / "research-tracker"

    # 1. Initialize research-queue KB
    init_res = world.cli(
        "init", "-t", "research-queue", "-p", str(q_path), "-n", "tracker", "--no-examples"
    )
    assert init_res.exit_code == 0, init_res.output

    kb_yaml_path = q_path / "kb.yaml"
    assert kb_yaml_path.is_file()
    kb_data = load_yaml_file(kb_yaml_path)
    assert kb_data.get("name") == "tracker"
    assert kb_data.get("policies", {}).get("enforce_workflow") is True
    assert "task" in kb_data.get("types", {})
    task_schema = kb_data["types"]["task"]
    assert "target_kb" in task_schema["optional"]
    assert "question" in task_schema["optional"]
    assert "sources_wanted" in task_schema["optional"]
    assert "target_kb" in task_schema["fields"]

    # 2. Task create with declared research ticket fields
    create_res = world.cli(
        "task",
        "create",
        "Investigate Graph Databases",
        "-k",
        "tracker",
        "--field",
        "target_kb=wiki",
        "--field",
        "question=What are the best graph databases?",
        "--format",
        "json",
    )
    assert create_res.exit_code == 0, create_res.output
    create_data = json.loads(create_res.output)
    assert create_data.get("created") is True
    task_id = create_data["entry_id"]

    # Verify frontmatter on disk contains declared ticket fields
    entry_path = q_path / "tasks" / f"{task_id}.md"
    assert entry_path.is_file()
    entry_text = entry_path.read_text(encoding="utf-8")
    assert "target_kb: wiki" in entry_text
    assert "What are the best graph databases?" in entry_text

    # 3. Task claim
    claim_res = world.cli(
        "task", "claim", task_id, "-k", "tracker", "--assignee", "agent:researcher"
    )
    assert claim_res.exit_code == 0, claim_res.output

    # 4. Task update: in_progress
    up1_res = world.cli("task", "update", task_id, "-k", "tracker", "--status", "in_progress")
    assert up1_res.exit_code == 0, up1_res.output

    # 5. Task update: done
    up2_res = world.cli("task", "update", task_id, "-k", "tracker", "--status", "done")
    assert up2_res.exit_code == 0, up2_res.output

    # 6. Index health: clean with 0 errors/warnings
    health_res = world.cli("index", "health", "-k", "tracker", "--format", "json")
    assert health_res.exit_code == 0, health_res.output
    health_data = json.loads(health_res.output)
    assert not health_data.get("undeclared_types", [])
    assert not health_data.get("malformed_entries", [])


@pytest.mark.cli
def test_research_queue_orient_shows_ticket_fields_with_descriptions(cli_world, tmp_path: Path):
    """pyrite orient -k tracker shows ticket fields with their descriptions (#1663)."""
    world = cli_world()
    q_path = tmp_path / "research-queue-orient"

    init_res = world.cli(
        "init", "-t", "research-queue", "-p", str(q_path), "-n", "queue-kb", "--no-examples"
    )
    assert init_res.exit_code == 0, init_res.output

    orient_res = world.cli("orient", "-k", "queue-kb", "--format", "json")
    assert orient_res.exit_code == 0, orient_res.output
    orient_data = json.loads(orient_res.output)

    schema = orient_data.get("schema", {})
    types = schema.get("types", {})
    assert "task" in types
    task_info = types["task"]

    field_descs = task_info.get("field_descriptions", {})
    assert "target_kb" in field_descs
    assert "question" in field_descs
    assert "sources_wanted" in field_descs
    assert "Knowledge base" in field_descs["target_kb"]
    assert "research question" in field_descs["question"]

    assert "ai_instructions" in task_info
    assert "autonomously" in task_info["ai_instructions"]
    assert "guidelines" in task_info
    assert "lineage" in task_info["guidelines"]


def test_task_type_definition_lives_only_in_task_kb_preset():
    """TASK_KB_PRESET is untouched: research-queue deepcopies and extends it."""
    base_task = TASK_KB_PRESET["types"]["task"]
    assert "target_kb" not in base_task.get("optional", [])
    assert "question" not in base_task.get("optional", [])
    assert "sources_wanted" not in base_task.get("optional", [])
    assert "fields" not in base_task


@pytest.mark.cli
def test_init_unknown_template_lists_available_templates(cli_world, tmp_path: Path):
    """Passing an unknown template lists all available templates (including task & research-queue)."""
    world = cli_world()
    bad_path = tmp_path / "bad-kb"
    res = world.cli("init", "-t", "nonexistent-template", "-p", str(bad_path))
    assert res.exit_code != 0
    assert "Unknown template 'nonexistent-template'" in res.output
    assert "task" in res.output
    assert "research-queue" in res.output
