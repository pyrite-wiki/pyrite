"""A --limit or --offset above SQLite's integer range is refused as a usage error (#1800).

SQLite integer parameters are signed 64-bit integers (-2**63 to 2**63-1).
Values exceeding 2**63-1 must be refused before any query runs, exiting 2 with
error_code USAGE_ERROR and no traceback, across all output formats.
"""

from __future__ import annotations

import json

from click import unstyle
import pytest

from pyrite.cli.context import ROW_COUNT_MAX

OVERSIZED = [
    str(ROW_COUNT_MAX + 1),  # 2**63: 9223372036854775808
    "99999999999999999999",
]

COMMAND_CASES = [
    (["social", "newest"], "--limit"),
    (["social", "top"], "--limit"),
    (["tags"], "--limit"),
    (["list-entries"], "--offset"),
    (["search", "test"], "--limit"),
]


@pytest.fixture
def populated_world(cli_world):
    world = cli_world()
    for i in range(3):
        world.add_entry(
            "test-kb",
            f"---\nid: note-{i}\ntype: note\ntitle: Note {i}\ntags: [tag-{i}]\n---\nBody {i}.\n",
        )
    return world


@pytest.mark.cli
@pytest.mark.parametrize(("cmd_args", "option"), COMMAND_CASES)
@pytest.mark.parametrize("value", OVERSIZED)
def test_oversized_row_count_exits_usage_error_no_traceback(
    populated_world, cmd_args, option, value
):
    args = [*cmd_args, option, value]
    res = populated_world.cli(*args)
    assert res.exit_code == 2, res.output
    plain = unstyle(res.output)
    assert "Traceback" not in plain
    assert "OverflowError" not in plain
    assert "not in the range" in plain
    # If the command emitted a JSON error document, check error_code
    for line in plain.splitlines():
        if line.startswith("{"):
            doc = json.loads(line)
            assert doc["error_code"] == "USAGE_ERROR"
            break


@pytest.mark.cli
@pytest.mark.parametrize(("cmd_args", "option"), COMMAND_CASES)
@pytest.mark.parametrize("value", OVERSIZED)
def test_oversized_row_count_on_empty_kb(cli_world, cmd_args, option, value):
    world = cli_world()
    args = [*cmd_args, option, value]
    res = world.cli(*args)
    assert res.exit_code == 2, res.output
    plain = unstyle(res.output)
    assert "Traceback" not in plain
    assert "OverflowError" not in plain
    assert "not in the range" in plain


@pytest.mark.cli
@pytest.mark.parametrize(
    ("cmd_args", "option"),
    [
        (["tags"], "--limit"),
        (["list-entries"], "--offset"),
        (["search", "test"], "--limit"),
    ],
)
@pytest.mark.parametrize("fmt", ["json", "rich"])
def test_oversized_row_count_formats(populated_world, cmd_args, option, fmt):
    args = [*cmd_args, option, "99999999999999999999", "--format", fmt]
    res = populated_world.cli(*args)
    assert res.exit_code == 2, res.output
    plain = unstyle(res.output)
    assert "Traceback" not in plain
    assert "OverflowError" not in plain
    assert "not in the range" in plain
    if fmt == "json":
        doc = json.loads(next(line for line in plain.splitlines() if line.startswith("{")))
        assert doc["error_code"] == "USAGE_ERROR"
    else:
        assert "USAGE_ERROR" in plain


@pytest.mark.cli
@pytest.mark.control(reason="2**63 - 1 is the SQLite integer max and is accepted by CLI")
@pytest.mark.parametrize(
    ("cmd_args", "option"),
    [
        (["social", "newest"], "--limit"),
        (["social", "top"], "--limit"),
        (["tags"], "--limit"),
        (["list-entries"], "--offset"),
        (["search", "test"], "--limit"),
    ],
)
def test_sqlite_max_integer_is_accepted(populated_world, cmd_args, option):
    args = [*cmd_args, option, str(ROW_COUNT_MAX)]
    res = populated_world.cli(*args)
    assert res.exit_code == 0, res.output
    plain = unstyle(res.output)
    assert "Traceback" not in plain
    assert "OverflowError" not in plain


@pytest.mark.cli
def test_huge_negative_offset_refused_as_usage_error(populated_world):
    res = populated_world.cli("list-entries", "--offset", "-99999999999999999999")
    assert res.exit_code == 2, res.output
    plain = unstyle(res.output)
    assert "Traceback" not in plain
    assert "OverflowError" not in plain
    assert "not in the range" in plain
    doc = json.loads(next(line for line in plain.splitlines() if line.startswith("{")))
    assert doc["error_code"] == "USAGE_ERROR"
