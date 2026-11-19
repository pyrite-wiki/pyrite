"""#1006: `search --mode zzz` ends the command in rich format too.

The rich path caught the refusal in its generic "index search failed" handler
and answered with a file search: exit 0 and a result table for a mode the
command had just refused. JSON already exited 1. A refusal is a refusal on
every format.
"""

import json

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")


def test_rich_refuses_an_unknown_mode_and_does_not_run_a_file_search(cli_world):
    world = cli_world()
    made = world.cli("create", "-k", "test-kb", "-t", "note", "--title", "Kestrel")
    assert made.exit_code == 0, made.output

    result = world.cli("search", "kestrel", "-k", "test-kb", "--mode", "zzz", "--format", "rich")

    assert result.exit_code == 1, result.output
    assert "Unknown search mode" in result.output, result.output
    for mode in ("keyword", "semantic", "hybrid"):
        assert mode in result.output, result.output
    assert "Falling back" not in result.output, result.output
    assert "Search Results" not in result.output, result.output


def test_json_refuses_the_same_mode_with_the_same_contract(cli_world):
    """The control: the machine format still exits 1 with its own payload."""
    world = cli_world()
    made = world.cli("create", "-k", "test-kb", "-t", "note", "--title", "Kestrel")
    assert made.exit_code == 0, made.output
    world.index("test-kb")

    result = world.cli("search", "kestrel", "-k", "test-kb", "--mode", "zzz", "--format", "json")

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["error_type"] == "ValidationError", payload
    assert "zzz" in payload["error"], payload
