"""Regression tests for create --template using KB schema (#1598).

Verifies that:
- `pyrite create -k <kb> -t <type> --template` matches the frontmatter written by a real create.
- Tested across multiple (KB, type) pairs including plugin type mappings (e.g. event -> investigation_event).
- In an enforced KB, undeclared types are refused with the same error envelope and exit code
  as a real create, both with and without `--allow-undeclared`.
- In an unenforced KB, `--allow-undeclared` allows generating the template.
- Unknown KB names are refused with KB_NOT_FOUND.
- Extra fields declared in kb.yaml on core types appear in the template.
- Without `-k`, `create -t <type> --template` keeps today's core types skeleton.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from pyrite.cli.init_command import BUILTIN_TEMPLATES
from pyrite.config import KBConfig
from pyrite.utils.frontmatter import split_frontmatter


def _parse_template_frontmatter(template_str: str) -> dict:
    split = split_frontmatter(template_str)
    return yaml.safe_load(split.text)


@pytest.mark.parametrize(
    ("kb_type", "kb_template", "entry_type", "create_args"),
    [
        ("generic", "software", "backlog_item", []),
        ("generic", "software", "adr", []),
        ("journalism-investigation", None, "event", ["--date", "2024-01-01"]),
    ],
)
def test_create_template_matches_real_create_frontmatter(
    cli_world,
    tmp_path: Path,
    kb_type: str,
    kb_template: str | None,
    entry_type: str,
    create_args: list[str],
):
    """Template keys and defaults equal what a real create writes to disk (#1598)."""
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir(parents=True, exist_ok=True)
    if kb_template:
        (kb_path / "kb.yaml").write_text(yaml.dump(BUILTIN_TEMPLATES[kb_template]))
    else:
        (kb_path / "kb.yaml").write_text("name: test-kb\n")

    world = cli_world(kbs=[KBConfig(name="test-kb", path=kb_path, kb_type=kb_type)])

    # Real create
    real_res = world.cli(
        "create",
        "-k",
        "test-kb",
        "-t",
        entry_type,
        "--title",
        "Sample Entry",
        *create_args,
        "--format",
        "json",
    )
    assert real_res.exit_code == 0, real_res.output
    real_data = json.loads(real_res.output)
    assert real_data["outcome"] == "done"

    # Template create
    tpl_res = world.cli(
        "create",
        "-k",
        "test-kb",
        "-t",
        entry_type,
        "--template",
        "--format",
        "json",
    )
    assert tpl_res.exit_code == 0, tpl_res.output
    tpl_data = json.loads(tpl_res.output)
    assert tpl_data["outcome"] == "done"

    # Stored file on disk
    entry_files = [p for p in kb_path.glob("**/*.md") if p.name != "kb.yaml"]
    assert len(entry_files) == 1
    real_fm = yaml.safe_load(split_frontmatter(entry_files[0].read_text(encoding="utf-8")).text)
    tpl_fm = _parse_template_frontmatter(tpl_data["template"])

    # Types must match (including mapped types, e.g. investigation_event)
    assert tpl_fm["type"] == real_fm["type"]
    assert tpl_data["entry_type"] == real_data["entry_type"]

    # All schema fields and defaults must match (ignoring id, title, and create-specific tags/args)
    ignore_keys = {"id", "title", "tags", "date"}
    for key, val in tpl_fm.items():
        if key not in ignore_keys:
            assert key in real_fm, f"Template key '{key}' missing from real create file"
            assert val == real_fm[key], (
                f"Default value mismatch for key '{key}': {val!r} != {real_fm[key]!r}"
            )

    for key, val in real_fm.items():
        if key not in ignore_keys:
            assert key in tpl_fm, f"Real create file key '{key}' missing from template"
            assert val == tpl_fm[key], f"Value mismatch for key '{key}': {val!r} != {tpl_fm[key]!r}"


def test_create_template_undeclared_type_refusal_matches_real_create(cli_world, tmp_path: Path):
    """In an enforced KB, template and real create refuse undeclared types identically."""
    kb_path = tmp_path / "proj"
    kb_path.mkdir(parents=True, exist_ok=True)
    (kb_path / "kb.yaml").write_text(yaml.dump(BUILTIN_TEMPLATES["software"]))

    world = cli_world(kbs=[KBConfig(name="proj", path=kb_path, kb_type="generic")])

    # 1. Without --allow-undeclared: both refuse with UNDECLARED_TYPE
    tpl_res = world.cli("create", "-k", "proj", "-t", "nosuch", "--template", "--format", "json")
    real_res = world.cli("create", "-k", "proj", "-t", "nosuch", "--title", "x", "--format", "json")
    assert tpl_res.exit_code == real_res.exit_code == 1
    tpl_data = json.loads(tpl_res.output)
    real_data = json.loads(real_res.output)
    assert tpl_data["error_code"] == real_data["error_code"] == "UNDECLARED_TYPE"
    assert tpl_data["error"] == real_data["error"]

    # 2. With --allow-undeclared: in enforced KB, both refuse with SCHEMA_VIOLATION (rule known_type)
    tpl_res2 = world.cli(
        "create",
        "-k",
        "proj",
        "-t",
        "nosuch",
        "--template",
        "--allow-undeclared",
        "--format",
        "json",
    )
    real_res2 = world.cli(
        "create",
        "-k",
        "proj",
        "-t",
        "nosuch",
        "--title",
        "x",
        "--allow-undeclared",
        "--format",
        "json",
    )
    assert tpl_res2.exit_code == real_res2.exit_code == 1
    tpl_data2 = json.loads(tpl_res2.output)
    real_data2 = json.loads(real_res2.output)
    assert tpl_data2["error_code"] == real_data2["error_code"] == "SCHEMA_VIOLATION"
    assert tpl_data2["errors"] == real_data2["errors"]
    assert tpl_data2["error"] == real_data2["error"]


def test_create_template_undeclared_type_allowed_in_unenforced_kb(cli_world, tmp_path: Path):
    """In an unenforced KB, --allow-undeclared allows template and real create."""
    kb_path = tmp_path / "generic-kb"
    kb_path.mkdir(parents=True, exist_ok=True)

    world = cli_world(kbs=[KBConfig(name="generic-kb", path=kb_path, kb_type="generic")])

    tpl_res = world.cli(
        "create",
        "-k",
        "generic-kb",
        "-t",
        "custom",
        "--template",
        "--allow-undeclared",
        "--format",
        "json",
    )
    assert tpl_res.exit_code == 0, tpl_res.output
    tpl_data = json.loads(tpl_res.output)
    assert tpl_data["outcome"] == "done"
    assert tpl_data["entry_type"] == "custom"

    fm = _parse_template_frontmatter(tpl_data["template"])
    assert fm["type"] == "custom"


def test_create_template_unknown_kb_refused(cli_world):
    """create -k nosuch --template refuses with KB_NOT_FOUND."""
    world = cli_world()
    res = world.cli("create", "-k", "nosuch-kb", "-t", "note", "--template", "--format", "json")
    assert res.exit_code == 1, res.output
    data = json.loads(res.output)
    assert data["error_code"] == "KB_NOT_FOUND"
    assert "KB 'nosuch-kb' not found" in data["error"]


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
