"""#1397: `search --limit` refuses 0 and negative values as a usage error.

`--limit -1` was silently accepted: on the index path SQLite reads
`LIMIT -1` as "no limit", and the service only surfaced a messy
`ValueError` ("limit must be a positive integer") with exit 1; on the
`--files` path it returned every match with exit 0. A limit is a
maximum count, so 0 and below are meaningless, and both paths must
refuse them the same way, before any search runs.
"""

import json

import pytest


@pytest.fixture
def search_world(cli_world):
    """A world with one KB holding one entry matching "hello"."""
    world = cli_world()
    world.add_entry(
        "test-kb",
        "---\nid: hello\ntype: note\ntitle: Hello World\n---\n\nhello world\n",
    )
    return world


@pytest.mark.cli
class TestSearchLimitBelowOne:
    @pytest.mark.parametrize("bad_limit", ["0", "-1"])
    def test_index_path_refuses_as_usage_error(self, search_world, bad_limit):
        result = search_world.cli(
            "search", "hello", "-k", "test-kb", "--limit", bad_limit, "--format", "json"
        )
        assert result.exit_code == 2, result.output
        assert "Invalid value for '--limit'" in result.output

    @pytest.mark.parametrize("bad_limit", ["0", "-1"])
    def test_files_path_refuses_as_usage_error(self, search_world, bad_limit):
        result = search_world.cli(
            "search",
            "hello",
            "-k",
            "test-kb",
            "--limit",
            bad_limit,
            "--files",
            "--format",
            "json",
        )
        assert result.exit_code == 2, result.output
        assert "Invalid value for '--limit'" in result.output

    def test_limit_one_returns_exactly_one_result(self, search_world):
        result = search_world.cli(
            "search", "hello", "-k", "test-kb", "--limit", "1", "--format", "json"
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["count"] == 1

    def test_limit_one_files_path_runs(self, search_world):
        result = search_world.cli(
            "search", "hello", "-k", "test-kb", "--limit", "1", "--files", "--format", "json"
        )
        assert result.exit_code == 0, result.output
