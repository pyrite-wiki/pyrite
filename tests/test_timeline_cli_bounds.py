"""`pyrite timeline` refuses out-of-range --limit and --min-importance.

The timeline --limit had no lower bound, so --limit -1 was accepted and
SQLite read it as "no limit", returning every event. --min-importance
promised 1-10 in its help but enforced nothing. Both now refuse bad values
as usage errors (exit 2), matching the REST timeline, which enforces
limit ge=1 le=500 and min_importance ge=1 le=10.
"""

import json

import pytest
from click import unstyle

from pyrite.config import KBConfig, KBType
from pyrite.models import EventEntry
from pyrite.storage.repository import KBRepository


@pytest.fixture
def timeline_world(cli_world, tmp_path):
    """A world with an events KB holding two events (importance 5 and 8)."""
    world = cli_world(
        kbs=[KBConfig(name="test-events", path=tmp_path / "events", kb_type=KBType.EVENTS)]
    )
    repo = KBRepository(world.config.get_kb("test-events"))
    for i, importance in enumerate((5, 8)):
        event = EventEntry.create(
            date=f"2025-01-{10 + i:02d}",
            title=f"Test Event {i}",
            body=f"Body for event {i}.",
            importance=importance,
        )
        repo.save(event)
    world.index("test-events")
    return world


def _usage_error(result, option):
    """Assert a Typer usage error: exit 2, JSON document, USAGE_ERROR."""
    output = unstyle(result.output)
    assert result.exit_code == 2, result.output
    assert "Traceback" not in output
    document = json.loads(next(line for line in output.splitlines() if line.startswith("{")))
    assert document["error_code"] == "USAGE_ERROR"
    assert f"Invalid value for '{option}'" in document["error"]


@pytest.mark.cli
@pytest.mark.parametrize("bad_limit", ["-1", "0", "501"])
def test_timeline_limit_out_of_range_is_usage_error(timeline_world, bad_limit):
    result = timeline_world.cli("timeline", "--limit", bad_limit, "--format", "json")
    _usage_error(result, "--limit")


@pytest.mark.cli
@pytest.mark.parametrize("bad_importance", ["0", "11"])
def test_timeline_min_importance_out_of_range_is_usage_error(timeline_world, bad_importance):
    result = timeline_world.cli("timeline", "--min-importance", bad_importance, "--format", "json")
    _usage_error(result, "--min-importance")


@pytest.mark.cli
def test_timeline_limit_one_returns_one_event(timeline_world):
    result = timeline_world.cli("timeline", "--limit", "1", "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["count"] == 1


@pytest.mark.cli
def test_timeline_limit_at_cap_still_works(timeline_world):
    result = timeline_world.cli("timeline", "--limit", "500", "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["count"] == 2


@pytest.mark.cli
@pytest.mark.parametrize("good_importance", ["1", "10"])
def test_timeline_min_importance_in_range_works(timeline_world, good_importance):
    result = timeline_world.cli("timeline", "--min-importance", good_importance, "--format", "json")
    assert result.exit_code == 0, result.output
