"""Tests verifying that fields declared text/string keep their typed values on every surface (#1672).

A value sent to a field declared text (or string) is stored exactly as typed, on
every surface: CLI -f, MCP kb_create/kb_update, and REST POST/PATCH. Numeric-looking
values like 1.10, 02134, 1e5, true, and 2026-11-17 remain strings and are not parsed
into floats, ints, or booleans. Declared number fields continue to store numeric values
per dev's one number rule (read_declared_number).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pyrite.config import KBConfig
from tests._world import build_world

KB_NAME = "test-kb"

KB_YAML = """\
name: test-kb
types:
  note:
    fields:
      version:
        type: text
      count:
        type: number
      urgent:
        type: checkbox
"""

TEXT_CASES = [
    "1.10",
    "02134",
    "1e5",
    "true",
    "2026-11-17",
]


@pytest.fixture
def world(tmp_path: Path):
    kb_path = tmp_path / KB_NAME
    kb_path.mkdir(parents=True, exist_ok=True)
    (kb_path / "kb.yaml").write_text(KB_YAML, encoding="utf-8")
    w = build_world(
        tmp_path / "world",
        kbs=[KBConfig(name=KB_NAME, path=kb_path)],
        rest=True,
    )
    try:
        yield w
    finally:
        w.close()


def _get_entry_field(world: Any, entry_id: str, field_name: str) -> Any:
    world.index(KB_NAME)
    res = world.cli("get", entry_id, "-k", KB_NAME, "--format", "json")
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    if field_name in data and data[field_name] is not None:
        return data[field_name]
    return data.get("metadata", {}).get(field_name)


@pytest.mark.parametrize("surface", ["cli", "mcp", "rest"])
@pytest.mark.parametrize(("i", "val"), list(enumerate(TEXT_CASES)))
def test_declared_text_field_create_preserves_value(world, surface, i, val):
    entry_id = f"c-{surface}-{i}"

    if surface == "cli":
        res = world.cli(
            "create",
            "-k",
            KB_NAME,
            "-t",
            "note",
            "--title",
            entry_id,
            "-f",
            f"version={val}",
            "-f",
            "count=7",
            "--format",
            "json",
        )
        assert res.exit_code == 0, res.output
        created_id = json.loads(res.output)["entry_id"]
    elif surface == "mcp":
        mcp = world.mcp("admin")
        res = mcp._dispatch_tool(
            "kb_create",
            {
                "kb_name": KB_NAME,
                "entry_type": "note",
                "title": entry_id,
                "version": val,
                "count": 7,
            },
        )
        assert "error" not in res, res
        created_id = res["entry_id"]
    else:
        resp = world.client.post(
            "/api/entries",
            json={
                "kb": KB_NAME,
                "entry_type": "note",
                "title": entry_id,
                "metadata": {
                    "version": val,
                    "count": 7,
                },
            },
        )
        assert resp.status_code == 200, resp.text
        created_id = resp.json()["id"]

    stored_val = _get_entry_field(world, created_id, "version")
    assert stored_val == val
    assert isinstance(stored_val, str)

    count_val = _get_entry_field(world, created_id, "count")
    assert count_val == 7
    assert type(count_val) is int


@pytest.mark.parametrize("surface", ["cli", "mcp", "rest"])
@pytest.mark.parametrize(("i", "val"), list(enumerate(TEXT_CASES)))
def test_declared_text_field_update_preserves_value(world, surface, i, val):
    entry_id = f"u-{surface}-{i}"
    # Seed an entry
    mcp = world.mcp("admin")
    seed_res = mcp._dispatch_tool(
        "kb_create",
        {
            "kb_name": KB_NAME,
            "entry_type": "note",
            "title": entry_id,
            "version": "initial",
            "count": 1,
        },
    )
    assert "error" not in seed_res, seed_res
    created_id = seed_res["entry_id"]

    if surface == "cli":
        res = world.cli(
            "update",
            created_id,
            "-k",
            KB_NAME,
            "-f",
            f"version={val}",
            "--format",
            "json",
        )
        assert res.exit_code == 0, res.output
    elif surface == "mcp":
        res = mcp._dispatch_tool(
            "kb_update",
            {
                "kb_name": KB_NAME,
                "entry_id": created_id,
                "version": val,
            },
        )
        assert "error" not in res, res
    else:
        resp = world.client.patch(
            f"/api/entries/{created_id}",
            json={
                "kb": KB_NAME,
                "field": "version",
                "value": val,
            },
        )
        assert resp.status_code == 200, resp.text

    stored_val = _get_entry_field(world, created_id, "version")
    assert stored_val == val
    assert isinstance(stored_val, str)


def test_cli_declared_number_conforms_to_number_rule(world):
    """Number parsing conforms to dev's one number rule (#1628):
    ASCII whole numbers and canonical decimals become int/float;
    007, 1e3, 7.50 remain text.
    """
    cases = [
        ("42", 42, int),
        ("-5", -5, int),
        ("3.14", 3.14, float),
        ("007", "007", str),
        ("1e3", "1e3", str),
        ("7.50", "7.50", str),
        ("-0", "-0", str),
    ]
    for idx, (raw_str, expected_val, expected_type) in enumerate(cases):
        entry_id = f"num-{idx}"
        res = world.cli(
            "create",
            "-k",
            KB_NAME,
            "-t",
            "note",
            "--title",
            entry_id,
            "-f",
            f"count={raw_str}",
            "--format",
            "json",
        )
        assert res.exit_code == 0, res.output
        created_id = json.loads(res.output)["entry_id"]
        got = _get_entry_field(world, created_id, "count")
        assert got == expected_val, f"count={raw_str} expected {expected_val}, got {got}"
        assert type(got) is expected_type, (
            f"count={raw_str} expected {expected_type}, got {type(got)}"
        )


def test_cli_declared_checkbox_rule(world):
    """Checkbox parsing only accepts true/false, 1/0; yes/no remain text."""
    cases = [
        ("true", True, bool),
        ("false", False, bool),
        ("1", True, bool),
        ("0", False, bool),
        ("yes", "yes", str),
        ("no", "no", str),
    ]
    for idx, (raw_str, expected_val, expected_type) in enumerate(cases):
        entry_id = f"chk-{idx}"
        res = world.cli(
            "create",
            "-k",
            KB_NAME,
            "-t",
            "note",
            "--title",
            entry_id,
            "-f",
            f"urgent={raw_str}",
            "--format",
            "json",
        )
        assert res.exit_code == 0, res.output
        created_id = json.loads(res.output)["entry_id"]
        got = _get_entry_field(world, created_id, "urgent")
        assert got == expected_val, f"urgent={raw_str} expected {expected_val}, got {got}"
        assert type(got) is expected_type, (
            f"urgent={raw_str} expected {expected_type}, got {type(got)}"
        )
