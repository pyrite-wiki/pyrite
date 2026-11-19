"""A date or time parameter is either understood or refused (#1268).

`kb_recent {"since": "yesterday"}` answered `{"entries": [], "count": 0}` -- the
same answer as "nothing has changed since then", which is a confident wrong
answer for the "what changed since I last looked" workflow the tool exists for.
The value was never parsed: entries were kept by comparing strings,
``(entry["updated_at"] or "") >= since``. So a *valid* ISO datetime with a
non-UTC offset was also answered wrongly -- ``2026-10-23T05:00:00-04:00`` is
09:00Z, and entries last updated at 06:06Z came back as "after" it.

Both surfaces that take ``since`` are covered here: MCP ``kb_recent`` and the
CLI ``recent``. The shapes are the issue's table: a bad value is refused
(``VALIDATION_FAILED``, naming the parameter, with the accepted forms and an
example), a good one filters by the instant it names, and an empty value means
"no lower bound" rather than "nothing newer".
"""

import json

import pytest

#: The three instants the world holds, in UTC. ``EARLY`` is the one the offset
#: case must exclude: it is after 00:00Z and before 09:00Z.
OLD = "2026-10-20T00:00:00Z"
EARLY = "2026-10-23T06:06:00Z"
LATE = "2026-10-23T12:00:00Z"

ALL_IDS = {"old-note", "early-note", "late-note"}


def _note(entry_id: str, title: str, updated_at: str) -> str:
    """One entry file whose ``updated_at`` is the instant under test."""
    return (
        "---\n"
        f"id: {entry_id}\n"
        "type: note\n"
        f"title: {title}\n"
        "created_at: 2026-10-20T00:00:00Z\n"
        f"updated_at: {updated_at}\n"
        "---\n\n"
        f"{title}\n"
    )


@pytest.fixture
def updates(cli_world):
    """One KB whose three entries are ordered by a known ``updated_at``."""
    world = cli_world()
    world.add_entry("test-kb", _note("old-note", "old", OLD))
    world.add_entry("test-kb", _note("early-note", "early", EARLY))
    world.add_entry("test-kb", _note("late-note", "late", LATE))
    return world


def _mcp_recent(updates, since, *, tier="read"):
    """`kb_recent` over a real MCP server, with the value callers send."""
    server = updates.mcp(tier=tier)
    return server._dispatch_tool("kb_recent", {"kb_name": "test-kb", "since": since})


def _mcp_ids(updates, since) -> set[str]:
    result = _mcp_recent(updates, since)
    assert "entries" in result, result
    return {entry["id"] for entry in result["entries"]}


def _cli_recent(updates, since):
    return updates.cli("recent", "-k", "test-kb", "--since", since, "--format", "json")


#: Values no parser should accept. ``yesterday`` is the report's own repro.
BAD_VALUES = ["yesterday", "2026-13-45", "last week", "2026-10-22T99:00:00Z", "24h"]


@pytest.mark.parametrize("bad", BAD_VALUES)
def test_mcp_refuses_a_since_it_cannot_parse(updates, bad):
    """The refusal names the parameter, the accepted forms and an example."""
    result = _mcp_recent(updates, bad)

    assert result["error_code"] == "VALIDATION_FAILED", result
    assert "since" in result["error"], result
    suggestion = result.get("suggestion") or ""
    assert "YYYY-MM-DD" in suggestion, result
    assert "2026-10-22" in suggestion, result


@pytest.mark.parametrize(
    ("since", "expected"),
    [
        # A date is midnight UTC.
        ("2026-10-23", {"early-note", "late-note"}),
        # ... the same instant spelled as a datetime.
        ("2026-10-23T00:00:00Z", {"early-note", "late-note"}),
        ("2026-10-22T20:00:00-04:00", {"early-note", "late-note"}),
        # 09:00Z: EARLY (06:06Z) is before it, and says so in UTC, so a string
        # comparison kept it. This is the valid-input half of the bug.
        ("2026-10-23T05:00:00-04:00", {"late-note"}),
        # 07:00Z: LATE (12:00Z) is after it; a string comparison dropped it.
        ("2026-10-23T12:00:00+05:00", {"late-note"}),
        # A datetime with no offset is read as UTC, not as the reader's local time.
        ("2026-10-22T00:00:00", {"early-note", "late-note"}),
        # A space separator is still ISO 8601; 10:00Z is before both later entries.
        ("2026-10-22 10:00", {"early-note", "late-note"}),
        # No lower bound, not "nothing newer".
        ("", ALL_IDS),
    ],
)
def test_mcp_filters_by_the_instant_since_names(updates, since, expected):
    assert _mcp_ids(updates, since) == expected


def test_cli_refuses_a_since_it_cannot_parse(updates):
    result = _cli_recent(updates, "yesterday")

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "VALIDATION_FAILED", payload
    assert "since" in payload["error"], payload
    assert "2026-10-22" in payload["suggestion"], payload


@pytest.mark.parametrize(
    ("since", "expected"),
    [
        ("2026-10-23T00:00:00Z", {"early-note", "late-note"}),
        ("2026-10-23T05:00:00-04:00", {"late-note"}),
        ("2026-10-23T12:00:00+05:00", {"late-note"}),
        ("2026-10-22T00:00:00", {"early-note", "late-note"}),
        ("2026-10-22 10:00", {"early-note", "late-note"}),
    ],
)
def test_cli_filters_by_the_instant_since_names(updates, since, expected):
    result = _cli_recent(updates, since)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert {entry["id"] for entry in payload["entries"]} == expected
