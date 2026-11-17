"""Regression tests for CLI sort options validation (#1387).

Verifies that:
- `pyrite timeline --sort` accepts only `asc`, `desc` (case-insensitive) and refuses anything else.
- `pyrite list-entries --sort-order` accepts only `asc`, `desc` and refuses anything else.
- `pyrite list-entries --sort-by` accepts only `title`, `updated_at`, `created_at`, `entry_type` and refuses anything else.
- Refusals exit 2 with error_code USAGE_ERROR and name the option and allowed values.
- Valid values sort correctly as expected on entries.
"""

from __future__ import annotations

import json

import pytest


def _entry(entry_id: str, title: str, entry_type: str, created: str, updated: str) -> str:
    return (
        "---\n"
        f"id: {entry_id}\n"
        f"type: {entry_type}\n"
        f"title: {title}\n"
        f"created_at: {created}\n"
        f"updated_at: {updated}\n"
        "---\n\n"
        f"Body of {title}\n"
    )


@pytest.fixture
def two_entries_world(cli_world):
    world = cli_world()
    world.add_entry(
        "test-kb",
        _entry("entry-a", "Alpha", "note", "2026-10-01T00:00:00Z", "2026-10-10T00:00:00Z"),
    )
    world.add_entry(
        "test-kb",
        _entry("entry-b", "Beta", "concept", "2026-10-02T00:00:00Z", "2026-10-05T00:00:00Z"),
    )
    world.index()
    return world


def _parse_last_json(output: str) -> dict:
    """Parse the JSON envelope from the last line of CLI output."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return json.loads(lines[-1])


@pytest.mark.cli
def test_timeline_sort_refuses_invalid_value(two_entries_world):
    """`timeline --sort sideways` exits 2 with USAGE_ERROR naming --sort and allowed values."""
    res = two_entries_world.cli(
        "timeline", "-k", "test-kb", "--sort", "sideways", "--format", "json"
    )
    assert res.exit_code == 2, res.output
    data = _parse_last_json(res.output)
    assert data["error_code"] == "USAGE_ERROR"
    assert "--sort" in data["error"]
    assert "choose one of: asc, desc" in data["error"]


@pytest.mark.cli
def test_timeline_sort_refuses_empty_value(two_entries_world):
    """`timeline --sort ""` exits 2 with USAGE_ERROR."""
    res = two_entries_world.cli("timeline", "-k", "test-kb", "--sort", "", "--format", "json")
    assert res.exit_code == 2, res.output
    data = _parse_last_json(res.output)
    assert data["error_code"] == "USAGE_ERROR"
    assert "choose one of: asc, desc" in data["error"]


@pytest.mark.cli
def test_list_entries_sort_order_refuses_invalid_value(two_entries_world):
    """`list-entries --sort-order sideways` exits 2 with USAGE_ERROR."""
    res = two_entries_world.cli(
        "list-entries", "-k", "test-kb", "--sort-order", "sideways", "--format", "json"
    )
    assert res.exit_code == 2, res.output
    data = _parse_last_json(res.output)
    assert data["error_code"] == "USAGE_ERROR"
    assert "--sort-order" in data["error"]
    assert "choose one of: asc, desc" in data["error"]


@pytest.mark.cli
def test_list_entries_sort_by_refuses_invalid_value(two_entries_world):
    """`list-entries --sort-by bogus` exits 2 with USAGE_ERROR."""
    res = two_entries_world.cli(
        "list-entries", "-k", "test-kb", "--sort-by", "bogus", "--format", "json"
    )
    assert res.exit_code == 2, res.output
    data = _parse_last_json(res.output)
    assert data["error_code"] == "USAGE_ERROR"
    assert "--sort-by" in data["error"]
    assert "choose one of: title, updated_at, created_at, entry_type" in data["error"]


@pytest.mark.cli
def test_sort_order_case_insensitive_and_valid_values(two_entries_world):
    """Valid sort_order and sort_by values work and order entries as expected."""
    # Test sort_order=asc
    res_asc = two_entries_world.cli(
        "list-entries",
        "-k",
        "test-kb",
        "--sort-by",
        "title",
        "--sort-order",
        "asc",
        "--format",
        "json",
    )
    assert res_asc.exit_code == 0, res_asc.output
    data_asc = json.loads(res_asc.output)
    assert [e["id"] for e in data_asc["entries"]] == ["entry-a", "entry-b"]

    # Test sort_order=desc
    res_desc = two_entries_world.cli(
        "list-entries",
        "-k",
        "test-kb",
        "--sort-by",
        "title",
        "--sort-order",
        "desc",
        "--format",
        "json",
    )
    assert res_desc.exit_code == 0, res_desc.output
    data_desc = json.loads(res_desc.output)
    assert [e["id"] for e in data_desc["entries"]] == ["entry-b", "entry-a"]

    # Test uppercase ASC
    res_upper = two_entries_world.cli(
        "list-entries",
        "-k",
        "test-kb",
        "--sort-by",
        "title",
        "--sort-order",
        "ASC",
        "--format",
        "json",
    )
    assert res_upper.exit_code == 0, res_upper.output

    # Test timeline sort=asc and sort=desc
    t_asc = two_entries_world.cli("timeline", "-k", "test-kb", "--sort", "asc", "--format", "json")
    assert t_asc.exit_code == 0, t_asc.output
    t_desc = two_entries_world.cli(
        "timeline", "-k", "test-kb", "--sort", "desc", "--format", "json"
    )
    assert t_desc.exit_code == 0, t_desc.output
