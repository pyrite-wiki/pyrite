"""`kb_recent` / `pyrite recent` page the since-filtered set, not the raw list (#1859).

Both surfaces fetched the newest `limit` rows first and only then kept the
ones updated at or after `since`. With more than `limit` entries changed since
then, the rest were silently dropped and the answer carried no `has_more`, so
a caller paging by offset could never reach them. The filter now applies
before the limit, in one service method (`KBService.recent`), and both
surfaces answer with the `kb_list_entries` envelope: `has_more` is computed
over the filtered total, and `offset` walks the next page.

Shapes, from the issue: no `since`; `since` older than every entry; `since`
between entries; `since` in the future (empty, `has_more: false`); equal
`updated_at` at a page edge (ties ordered by id, so a page boundary never
repeats or skips one); `entry_type` set; several KBs with no `kb_name`
(readable set only); and the same on `pyrite recent --since ... --limit N
--format json` with `--offset`.
"""

import json

import pytest

from pyrite.config import KBConfig

#: Five instants, one per entry, oldest first. ``SINCE`` falls between the
#: second and third, so exactly the three newest entries match it.
T_OLD = "2026-10-20T00:00:00Z"
T_A = "2026-10-21T00:00:00Z"
T_B = "2026-10-22T00:00:00Z"
T_C = "2026-10-23T00:00:00Z"
T_D = "2026-10-24T00:00:00Z"

SINCE = "2026-10-22"  # midnight UTC: matches b-note, c-note, d-note

ALL_NOTES = ["old-note", "a-note", "b-note", "c-note", "d-note"]
#: Entries at or after SINCE, newest first (the order `updated_at desc` gives).
SINCE_MATCHES_DESC = ["d-note", "c-note", "b-note"]


def _note(entry_id: str, updated_at: str, *, title: str | None = None, entry_type: str = "note"):
    """One entry file whose ``updated_at`` is the instant under test."""
    return (
        "---\n"
        f"id: {entry_id}\n"
        f"type: {entry_type}\n"
        f"title: {title or entry_id}\n"
        "created_at: 2026-10-19T00:00:00Z\n"
        f"updated_at: {updated_at}\n"
        "---\n\n"
        f"Body of {entry_id}\n"
    )


@pytest.fixture
def world(cli_world):
    """One KB, five notes with known, distinct ``updated_at`` instants."""
    w = cli_world()
    for entry_id, stamp in zip(ALL_NOTES, [T_OLD, T_A, T_B, T_C, T_D], strict=True):
        w.add_entry("test-kb", _note(entry_id, stamp))
    return w


@pytest.fixture
def tie_world(cli_world):
    """One KB, three notes sharing one ``updated_at`` (a page-edge tie)."""
    w = cli_world()
    for entry_id in ("tie-a", "tie-b", "tie-c"):
        w.add_entry("test-kb", _note(entry_id, T_D))
    return w


def _mcp(world, args, *, tier="read", readable_kbs=None):
    server = world.mcp(tier=tier)
    return server._dispatch_tool("kb_recent", args, readable_kbs=readable_kbs)


def _mcp_ids(result) -> list[str]:
    assert "entries" in result, result
    return [entry["id"] for entry in result["entries"]]


def _cli(world, *args):
    result = world.cli("recent", *args, "--format", "json")
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _cli_ids(payload) -> list[str]:
    return [entry["id"] for entry in payload["entries"]]


# -- MCP: the envelope ------------------------------------------------------


def test_mcp_since_with_limit_below_match_count_reports_has_more(world):
    """The issue's repro: 3 changed since `since`, `limit: 1` — the answer
    holds one of them and says more exist instead of dropping the other two."""
    result = _mcp(world, {"kb_name": "test-kb", "since": SINCE, "limit": 1})

    assert _mcp_ids(result) == ["d-note"], result
    assert result["count"] == 1, result
    assert result["total"] == 3, result
    assert result["limit"] == 1, result
    assert result["offset"] == 0, result
    assert result["has_more"] is True, result


def test_mcp_offset_pages_reach_each_match_exactly_once(world):
    """Paging with `offset` over a since-filtered set: every entry updated at
    or after `since` appears exactly once across pages, newest first."""
    seen: list[str] = []
    has_more_pages: list[bool] = []
    for offset in range(4):
        result = _mcp(world, {"kb_name": "test-kb", "since": SINCE, "limit": 1, "offset": offset})
        assert result["offset"] == offset, result
        assert result["total"] == 3, result
        seen.extend(_mcp_ids(result))
        has_more_pages.append(result["has_more"])

    assert seen == SINCE_MATCHES_DESC, seen
    assert len(set(seen)) == len(seen) == 3, "a page boundary repeated or skipped an entry"
    assert has_more_pages == [True, True, False, False], has_more_pages


def test_mcp_limit_at_or_above_match_count_has_no_more(world):
    """`limit` at least the number changed: `has_more: false`, same entries as
    before the fix (boundary: exactly `limit`)."""
    exact = _mcp(world, {"kb_name": "test-kb", "since": SINCE, "limit": 3})
    assert _mcp_ids(exact) == SINCE_MATCHES_DESC, exact
    assert exact["total"] == 3 and exact["has_more"] is False, exact

    roomy = _mcp(world, {"kb_name": "test-kb", "since": SINCE, "limit": 200})
    assert _mcp_ids(roomy) == SINCE_MATCHES_DESC, roomy
    assert roomy["has_more"] is False and roomy["total"] == 3, roomy


def test_mcp_no_since_keeps_entries_and_gains_the_envelope(world):
    """No `since`: the entries are what the raw newest-`limit` list always
    gave; the answer just also carries `has_more`/`offset`/`total` now."""
    result = _mcp(world, {"kb_name": "test-kb", "limit": 2})

    assert _mcp_ids(result) == ["d-note", "c-note"], result
    assert result["count"] == 2, result
    assert result["total"] == 5, result
    assert result["has_more"] is True, result
    assert result["offset"] == 0, result


def test_mcp_since_older_than_every_entry_pages_everything(world):
    result = _mcp(world, {"kb_name": "test-kb", "since": "2020-01-01", "limit": 2})
    assert result["total"] == 5, result
    assert result["has_more"] is True, result
    assert _mcp_ids(result) == ["d-note", "c-note"], result


def test_mcp_since_between_entries_counts_only_the_newer_ones(world):
    """`since` between two entries: only entries at or after it count, and the
    boundary is inclusive on the instant, not on the string."""
    result = _mcp(world, {"kb_name": "test-kb", "since": "2026-10-22T12:00:00Z", "limit": 10})
    assert _mcp_ids(result) == ["d-note", "c-note"], result
    assert result["total"] == 2 and result["has_more"] is False, result


def test_mcp_since_in_the_future_is_empty_with_has_more_false(world):
    result = _mcp(world, {"kb_name": "test-kb", "since": "2030-01-01", "limit": 5})
    assert result["entries"] == [], result
    assert result["count"] == 0 and result["total"] == 0, result
    assert result["has_more"] is False, result


def test_mcp_entry_type_filter_applies_before_the_limit(world):
    world.add_entry("test-kb", _note("late-task", "2026-10-25T00:00:00Z", entry_type="task"))

    notes = _mcp(
        world, {"kb_name": "test-kb", "since": "2020-01-01", "entry_type": "note", "limit": 2}
    )
    assert notes["total"] == 5, notes
    assert notes["has_more"] is True, notes
    assert all(e["entry_type"] == "note" for e in notes["entries"]), notes

    tasks = _mcp(world, {"kb_name": "test-kb", "since": SINCE, "entry_type": "task", "limit": 10})
    assert _mcp_ids(tasks) == ["late-task"], tasks
    assert tasks["total"] == 1 and tasks["has_more"] is False, tasks


def test_mcp_ties_at_a_page_edge_never_repeat_or_skip(tie_world):
    """Equal `updated_at` at a page boundary: ties are ordered by id, so
    paging with `limit: 1` returns each tied entry exactly once."""
    seen: list[str] = []
    has_more_pages: list[bool] = []
    for offset in range(4):
        result = _mcp(
            tie_world, {"kb_name": "test-kb", "since": "2020-01-01", "limit": 1, "offset": offset}
        )
        assert result["total"] == 3, result
        seen.extend(_mcp_ids(result))
        has_more_pages.append(result["has_more"])

    assert seen == ["tie-a", "tie-b", "tie-c"], seen
    assert has_more_pages == [True, True, False, False], has_more_pages


def test_mcp_no_kb_name_pages_the_readable_set_only(cli_world, tmp_path):
    """Several KBs, no `kb_name`: pages reach every readable changed entry and
    nothing from a KB outside the readable set — totals included."""
    kbs = [
        KBConfig(name="kb-a", path=tmp_path / "kb-a", kb_type="generic"),
        KBConfig(name="kb-b", path=tmp_path / "kb-b", kb_type="generic"),
    ]
    w = cli_world(kbs=kbs)
    w.add_entry("kb-a", _note("a-old", T_A))
    w.add_entry("kb-a", _note("a-new", T_D))
    w.add_entry("kb-b", _note("b-new", T_C))

    scoped = _mcp(w, {"since": "2020-01-01", "limit": 1}, readable_kbs={"kb-a"})
    assert scoped["total"] == 2, scoped
    assert _mcp_ids(scoped) == ["a-new"], scoped
    assert scoped["has_more"] is True, scoped
    second = _mcp(w, {"since": "2020-01-01", "limit": 1, "offset": 1}, readable_kbs={"kb-a"})
    assert _mcp_ids(second) == ["a-old"], second
    assert second["has_more"] is False, second
    assert all(e["kb_name"] == "kb-a" for e in scoped["entries"] + second["entries"])

    unscoped = _mcp(w, {"since": "2020-01-01", "limit": 10}, tier="admin")
    assert unscoped["total"] == 3, unscoped
    assert _mcp_ids(unscoped) == ["a-new", "b-new", "a-old"], unscoped
    assert unscoped["has_more"] is False, unscoped


# -- CLI: the same contract on `pyrite recent` ------------------------------


def test_cli_since_with_limit_reports_has_more_and_pages(world):
    first = _cli(world, "-k", "test-kb", "--since", SINCE, "--limit", "1")
    assert _cli_ids(first) == ["d-note"], first
    assert first["count"] == 1, first
    assert first["total"] == 3 and first["limit"] == 1 and first["offset"] == 0, first
    assert first["has_more"] is True, first

    seen = _cli_ids(first)
    for offset, expect_more in ((1, True), (2, False), (3, False)):
        page = _cli(
            world, "-k", "test-kb", "--since", SINCE, "--limit", "1", "--offset", str(offset)
        )
        assert page["offset"] == offset and page["total"] == 3, page
        assert page["has_more"] is expect_more, (offset, page)
        seen.extend(_cli_ids(page))

    assert seen == SINCE_MATCHES_DESC, seen
    assert len(set(seen)) == 3, "a page boundary repeated or skipped an entry"


def test_cli_no_since_gains_the_envelope(world):
    payload = _cli(world, "-k", "test-kb", "--limit", "2")
    assert _cli_ids(payload) == ["d-note", "c-note"], payload
    assert payload["total"] == 5 and payload["has_more"] is True, payload
    assert payload["offset"] == 0 and payload["limit"] == 2 and payload["count"] == 2, payload


def test_cli_limit_at_match_count_has_no_more(world):
    payload = _cli(world, "-k", "test-kb", "--since", SINCE, "--limit", "3")
    assert _cli_ids(payload) == SINCE_MATCHES_DESC, payload
    assert payload["has_more"] is False and payload["total"] == 3, payload


def test_cli_since_in_the_future_is_empty_with_has_more_false(world):
    payload = _cli(world, "-k", "test-kb", "--since", "2030-01-01")
    assert payload["entries"] == [] and payload["count"] == 0, payload
    assert payload["total"] == 0 and payload["has_more"] is False, payload


def test_cli_since_between_entries_and_offset_beyond_the_end(world):
    payload = _cli(world, "-k", "test-kb", "--since", "2026-10-22T12:00:00Z", "--limit", "10")
    assert _cli_ids(payload) == ["d-note", "c-note"], payload
    assert payload["total"] == 2 and payload["has_more"] is False, payload

    beyond = _cli(world, "-k", "test-kb", "--since", SINCE, "--limit", "2", "--offset", "3")
    assert beyond["entries"] == [] and beyond["has_more"] is False, beyond
    assert beyond["total"] == 3 and beyond["offset"] == 3, beyond


def test_cli_recent_rejects_a_negative_offset(world):
    result = world.cli("recent", "-k", "test-kb", "--offset", "-1", "--format", "json")
    assert result.exit_code == 2, result.output
    assert "Invalid value for '--offset'" in result.output, result.output


def test_cli_ties_at_a_page_edge_never_repeat_or_skip(tie_world):
    seen: list[str] = []
    for offset in range(4):
        payload = _cli(
            tie_world,
            "-k",
            "test-kb",
            "--since",
            "2020-01-01",
            "--limit",
            "1",
            "--offset",
            str(offset),
        )
        assert payload["total"] == 3, payload
        seen.extend(_cli_ids(payload))
    assert seen == ["tie-a", "tie-b", "tie-c"], seen
