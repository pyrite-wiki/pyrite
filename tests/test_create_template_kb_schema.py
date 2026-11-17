"""Regression tests for create --template using KB schema (#1598).

Verifies that:
- `pyrite create -k <kb> -t <type> --template` uses the KB's declared schema fields and defaults.
- For a software KB, `backlog_item` template includes kind, status: proposed, priority: medium, effort, assignee.
- Extra fields declared in kb.yaml on core types appear in the template.
- Undeclared types in a KB that declares schema are refused with the same error as a real create.
- Without `-k`, `create -t <type> --template` keeps today's core types skeleton.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from pyrite.cli.init_command import BUILTIN_TEMPLATES
from pyrite.config import KBConfig
from pyrite.utils.frontmatter import split_frontmatter


def _parse_template_frontmatter(template_str: str) -> dict:
    split = split_frontmatter(template_str)
    return yaml.safe_load(split.text)


def test_create_template_with_software_kb_matches_create_fields(cli_world, tmp_path: Path):
    """In a software KB, backlog_item template reflects fields and defaults written by create."""
    kb_path = tmp_path / "proj"
    kb_path.mkdir(parents=True, exist_ok=True)
    (kb_path / "kb.yaml").write_text(yaml.dump(BUILTIN_TEMPLATES["software"]))

    world = cli_world(kbs=[KBConfig(name="proj", path=kb_path, kb_type="generic")])

    res = world.cli("create", "-k", "proj", "-t", "backlog_item", "--template", "--format", "json")
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["outcome"] == "done"
    assert data["entry_type"] == "backlog_item"

    fm = _parse_template_frontmatter(data["template"])
    assert fm["type"] == "backlog_item"
    assert fm["id"] == ""
    assert fm["title"] == "Your Title Here"
    assert fm["status"] == "proposed"
    assert fm["priority"] == "medium"
    assert "kind" in fm
    assert "effort" in fm
    assert "assignee" in fm


def test_create_template_includes_fields_added_in_kb_yaml(cli_world, tmp_path: Path):
    """A field added to a core type in kb.yaml appears in that KB's note template."""
    kb_path = tmp_path / "custom-kb"
    kb_path.mkdir(parents=True, exist_ok=True)
    kb_yaml_content = {
        "name": "custom-kb",
        "types": {
            "note": {
                "fields": {
                    "project": {"type": "text", "default": "alpha"},
                    "reviewed": {"type": "checkbox", "default": True},
                }
            }
        },
    }
    (kb_path / "kb.yaml").write_text(yaml.dump(kb_yaml_content))

    world = cli_world(kbs=[KBConfig(name="custom-kb", path=kb_path, kb_type="generic")])

    res = world.cli("create", "-k", "custom-kb", "-t", "note", "--template", "--format", "json")
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)

    fm = _parse_template_frontmatter(data["template"])
    assert fm["type"] == "note"
    assert fm["project"] == "alpha"
    assert fm["reviewed"] is True


def test_create_template_refuses_undeclared_type(cli_world, tmp_path: Path):
    """create --template refuses undeclared types with the same error code as real create."""
    kb_path = tmp_path / "proj"
    kb_path.mkdir(parents=True, exist_ok=True)
    (kb_path / "kb.yaml").write_text(yaml.dump(BUILTIN_TEMPLATES["software"]))

    world = cli_world(kbs=[KBConfig(name="proj", path=kb_path, kb_type="generic")])

    # Template invocation for undeclared type
    tpl_res = world.cli("create", "-k", "proj", "-t", "nosuch", "--template", "--format", "json")
    assert tpl_res.exit_code == 1, tpl_res.output
    tpl_data = json.loads(tpl_res.output)
    assert tpl_data["error_code"] == "UNDECLARED_TYPE"
    assert "type 'nosuch' is not declared in KB 'proj'" in tpl_data["error"]

    # Real create invocation for undeclared type
    real_res = world.cli("create", "-k", "proj", "-t", "nosuch", "--title", "x", "--format", "json")
    assert real_res.exit_code == 1, real_res.output
    real_data = json.loads(real_res.output)
    assert real_data["error_code"] == tpl_data["error_code"]
    assert "type 'nosuch' is not declared in KB 'proj'" in real_data["error"]


def test_create_template_without_kb_keeps_core_type_skeleton(cli_world):
    """Without -k, create --template prints core types skeleton with type fields."""
    world = cli_world()
    res = world.cli("create", "-t", "person", "--template", "--format", "json")
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)

    fm = _parse_template_frontmatter(data["template"])
    assert fm["type"] == "person"
    assert "role" in fm
    assert "affiliations" in fm
