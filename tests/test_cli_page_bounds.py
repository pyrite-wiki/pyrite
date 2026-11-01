"""A page bound is a non-negative integer, refused before any query runs (#1386).

``pyrite list-entries --limit -1`` and ``pyrite recent --limit -1`` accepted a
negative limit with exit 0. SQLite reads ``LIMIT -1`` as "no limit", so the
whole KB came back, and ``list-entries`` reported ``"has_more": true`` although
nothing was left. ``list-entries --offset -5`` was worse: pagination silently
skipped backwards.

The REST route already held the invariant (``limit`` ``ge=1``, ``offset``
``ge=0``, pyrite/server/endpoints/entries.py). The CLI options had no lower
bound. The fix copies the pattern ``orient --offset`` already used: ``min=0``
on the option, so click's range error comes back as ``USAGE_ERROR``, exit 2.
``--limit 0`` keeps today's meaning (no entries, ``has_more`` true); it is a
separate open question for the maintainer, which is why the bound is ``min=0``
and not ``min=1``.
"""

import json

import pytest


def _entry(entry_id: str, title: str) -> str:
    """One note entry file in the KB under test."""
    return (
        "---\n"
        f"id: {entry_id}\n"
        "type: note\n"
        f"title: {title}\n"
        "created_at: 2026-10-20T00:00:00Z\n"
        "updated_at: 2026-10-20T00:00:00Z\n"
        "---\n\n"
        f"{title}\n"
    )


@pytest.fixture
def page_bounds(cli_world):
    """One KB with exactly two entries, so a bound of one paginates."""
    world = cli_world()
    world.add_entry("test-kb", _entry("alpha-note", "Alpha note"))
    world.add_entry("test-kb", _entry("beta-note", "Beta note"))
    return world


@pytest.mark.parametrize(
    ("args", "option"),
    [
        (["--limit", "-1"], "--limit"),
        (["--offset", "-5"], "--offset"),
    ],
)
def test_list_entries_refuses_a_negative_bound(page_bounds, args, option):
    """A negative bound is a usage error naming the option, exit 2."""
    result = page_bounds.cli(
        "list-entries", "-k", "test-kb", *args, "--format", "json"
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "USAGE_ERROR", payload
    assert option in payload["error"], payload


def test_recent_refuses_a_negative_limit(page_bounds):
    """``recent`` has the same lower-bound gap; it gets the same refusal."""
    result = page_bounds.cli("recent", "-k", "test-kb", "--limit", "-1", "--format", "json")

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "USAGE_ERROR", payload
    assert "--limit" in payload["error"], payload


def test_a_valid_bound_still_paginates(page_bounds):
    """The bound does not change valid calls: one entry, has_more false."""
    result = page_bounds.cli(
        "list-entries", "-k", "test-kb", "--limit", "1", "--offset", "1", "--format", "json"
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert len(payload["entries"]) == 1, payload
    assert payload["has_more"] is False, payload
