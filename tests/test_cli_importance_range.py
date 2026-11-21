"""#1393: create and update refuse an --importance outside 1-10.

The help text says "Importance (1-10)", but both commands accepted any
integer: 999, 0, and -5 were all written to the entry without complaint.
"""

import json

import pytest
from typer.testing import CliRunner

from pyrite.cli import app

runner = CliRunner()


def _md_files(world):
    return list(world.kb_path("test-kb").rglob("*.md"))


def _create(world, importance):
    return world.cli(
        "create",
        "-k",
        "test-kb",
        "-t",
        "note",
        "--title",
        "Test",
        "-b",
        "x",
        "--importance",
        str(importance),
        "--format",
        "json",
    )


@pytest.mark.cli
class TestCreateImportanceRange:
    @pytest.mark.parametrize("bad", [999, 0, -5])
    def test_create_refuses_out_of_range_importance(self, cli_world, bad):
        world = cli_world()
        result = _create(world, bad)
        assert result.exit_code == 2, result.output
        assert "Invalid value for '--importance'" in result.output
        assert _md_files(world) == []

    @pytest.mark.parametrize("good", [1, 10])
    def test_create_accepts_the_bounds(self, cli_world, good):
        world = cli_world()
        result = _create(world, good)
        assert result.exit_code == 0, result.output
        assert len(_md_files(world)) == 1


@pytest.mark.cli
class TestUpdateImportanceRange:
    def _made_entry(self, world):
        made = _create(world, 5)
        assert made.exit_code == 0, made.output
        return json.loads(made.output)["entry_id"]

    @pytest.mark.parametrize("bad", [11, 0, -1])
    def test_update_refuses_out_of_range_importance(self, cli_world, bad):
        world = cli_world()
        entry_id = self._made_entry(world)
        path = next(world.kb_path("test-kb").rglob("*.md"))
        before = path.read_text(encoding="utf-8")

        result = world.cli("update", entry_id, "-k", "test-kb", "--importance", str(bad))
        assert result.exit_code == 2, result.output
        assert "Invalid value for '--importance'" in result.output
        assert path.read_text(encoding="utf-8") == before

    @pytest.mark.parametrize("good", [1, 10])
    def test_update_accepts_the_bounds(self, cli_world, good):
        world = cli_world()
        entry_id = self._made_entry(world)
        result = world.cli("update", entry_id, "-k", "test-kb", "--importance", str(good))
        assert result.exit_code == 0, result.output
