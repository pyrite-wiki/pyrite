"""Regression tests for CLI sort options validation (#1387).

Verifies that:
- `pyrite timeline --sort` accepts only `asc`, `desc` (case-insensitive) and refuses anything else.
- `pyrite list-entries --sort-order` accepts only `asc`, `desc` and refuses anything else.
- `pyrite list-entries --sort-by` accepts only `title`, `updated_at`, `created_at`, `entry_type` and refuses anything else.
- Refusals exit 2 with error_code USAGE_ERROR and name the option, invalid value, and allowed values.
- Valid values sort correctly as expected on entries and timeline events.
"""

from __future__ import annotations

import json

import pytest

from pyrite.storage.backends.base_backend import BaseBackend
from pyrite.storage.backends.sqlite_backend import SQLiteBackend


def _entry(
    entry_id: str, title: str, entry_type: str, created: str, updated: str, date: str | None = None
) -> str:
    date_field = f"date: '{date}'\n" if date else ""
    return (
        "---\n"
        f"id: {entry_id}\n"
        f"type: {entry_type}\n"
        f"title: {title}\n"
        f"{date_field}"
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
        _entry(
            "entry-a",
            "Alpha",
            "event",
            "2026-10-01T00:00:00Z",
            "2026-10-10T00:00:00Z",
            date="2026-01-01",
        ),
    )
    world.add_entry(
        "test-kb",
        _entry(
            "entry-b",
            "Beta",
            "concept",
            "2026-10-02T00:00:00Z",
            "2026-10-05T00:00:00Z",
            date="2026-06-01",
        ),
    )
    world.index()
    return world


def _parse_last_json(output: str) -> dict:
    """Parse the JSON envelope from the last line of CLI output."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return json.loads(lines[-1])


def test_sort_by_columns_match_storage_backend():
    """CLI allowed sort columns match BaseBackend and SQLiteBackend _SORT_COLUMNS."""
    expected = {"title", "updated_at", "created_at", "entry_type"}
    assert BaseBackend._SORT_COLUMNS == expected
    assert SQLiteBackend._SORT_COLUMNS == expected


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
    assert "unknown sort order 'sideways'; choose one of: asc, desc" in data["error"]


@pytest.mark.cli
def test_timeline_sort_refuses_empty_value(two_entries_world):
    """`timeline --sort ""` exits 2 with USAGE_ERROR."""
    res = two_entries_world.cli("timeline", "-k", "test-kb", "--sort", "", "--format", "json")
    assert res.exit_code == 2, res.output
    data = _parse_last_json(res.output)
    assert data["error_code"] == "USAGE_ERROR"
    assert "unknown sort order ''; choose one of: asc, desc" in data["error"]


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
    assert "unknown sort order 'sideways'; choose one of: asc, desc" in data["error"]


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
    assert (
        "unknown sort column 'bogus'; choose one of: created_at, entry_type, title, updated_at"
        in data["error"]
    )


@pytest.mark.parametrize(
    ("col", "expected_asc"),
    [
        ("title", ["entry-a", "entry-b"]),
        ("updated_at", ["entry-b", "entry-a"]),
        ("created_at", ["entry-a", "entry-b"]),
        ("entry_type", ["entry-b", "entry-a"]),
    ],
)
@pytest.mark.cli
def test_list_entries_all_sort_by_columns_order_correctly(
    two_entries_world, col: str, expected_asc: list[str]
):
    """Each valid --sort-by column works and orders entries correctly in both asc and desc."""
    # Ascending
    res_asc = two_entries_world.cli(
        "list-entries", "-k", "test-kb", "--sort-by", col, "--sort-order", "asc", "--format", "json"
    )
    assert res_asc.exit_code == 0, res_asc.output
    data_asc = json.loads(res_asc.output)
    assert [e["id"] for e in data_asc["entries"]] == expected_asc

    # Descending
    res_desc = two_entries_world.cli(
        "list-entries",
        "-k",
        "test-kb",
        "--sort-by",
        col,
        "--sort-order",
        "desc",
        "--format",
        "json",
    )
    assert res_desc.exit_code == 0, res_desc.output
    data_desc = json.loads(res_desc.output)
    assert [e["id"] for e in data_desc["entries"]] == list(reversed(expected_asc))


@pytest.mark.cli
def test_sort_order_case_insensitive_and_timeline_ordering(two_entries_world):
    """Sort order accepts uppercase ASC/DESC and orders timeline events correctly."""
    # Uppercase ASC on list-entries
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
    assert [e["id"] for e in json.loads(res_upper.output)["entries"]] == ["entry-a", "entry-b"]

    # Timeline asc order
    t_asc = two_entries_world.cli("timeline", "-k", "test-kb", "--sort", "asc", "--format", "json")
    assert t_asc.exit_code == 0, t_asc.output
    data_t_asc = json.loads(t_asc.output)
    assert [e["id"] for e in data_t_asc["events"]] == ["entry-a", "entry-b"]

    # Timeline desc order
    t_desc = two_entries_world.cli(
        "timeline", "-k", "test-kb", "--sort", "desc", "--format", "json"
    )
    assert t_desc.exit_code == 0, t_desc.output
    data_t_desc = json.loads(t_desc.output)
    assert [e["id"] for e in data_t_desc["events"]] == ["entry-b", "entry-a"]

    # Timeline uppercase DESC
    t_upper = two_entries_world.cli(
        "timeline", "-k", "test-kb", "--sort", "DESC", "--format", "json"
    )
    assert t_upper.exit_code == 0, t_upper.output
    assert [e["id"] for e in json.loads(t_upper.output)["events"]] == ["entry-b", "entry-a"]
