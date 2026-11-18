"""Collection query limits are usage errors before opening a KB (#1557)."""

import json

import pytest


@pytest.fixture
def query_world(cli_world):
    world = cli_world()
    for number in range(3):
        world.add_entry(
            "test-kb",
            f"---\nid: note-{number}\ntype: note\ntitle: Note {number}\n---\nBody.\n",
        )
    return world


@pytest.mark.cli
@pytest.mark.parametrize("limit", [0, -3, 1001])
@pytest.mark.parametrize("kb", ["test-kb", "missing"])
def test_query_rejects_out_of_range_limit_before_opening_kb(query_world, limit, kb):
    result = query_world.cli("collections", "query", "type:note", "--kb", kb, "--limit", str(limit))
    assert result.exit_code == 2, result.output
    error = json.loads(result.stdout)
    assert error["error_code"] == "USAGE_ERROR"
    assert "--limit" in error["error"]


@pytest.mark.cli
@pytest.mark.control(reason="valid limits keep their existing result counts")
@pytest.mark.parametrize(("limit", "count"), [(1, 1), (1000, 3)])
@pytest.mark.parametrize("option", ["--limit", "-n"])
def test_query_accepts_limit_boundaries(query_world, limit, count, option):
    result = query_world.cli(
        "collections", "query", "type:note", "--kb", "test-kb", option, str(limit)
    )
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.stdout)["entries"]) == count
