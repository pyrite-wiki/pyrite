"""`kb_timeline` speaks the caller's KBs and the caller's statuses (#62).

Reproduced from the hallway session that opened the issue: a staging KB
(``status: unprocessed`` proposals, not canon) is indistinguishable from
canon in ``kb_timeline`` -- the tool had *no* ``kb_names`` parameter at all
and no status filter, so staging rows dominated a cold-start timeline and
three near-duplicate renderings of one story beat canon to the page.

The groom's two themes, on one two-KB world called ``canon`` and
``staging``:

- **Theme A (``kb_names``):** the plumbing already runs service -> SQL for
  every other cross-KB tool, and ``kb_names`` is already in
  ``KB_ARGUMENT_NAMES``, so the dispatcher refuses a KB the caller cannot
  read with ``KB_NOT_FOUND`` before the handler runs (P-R4, #952) -- like
  every other tool, so a private KB's existence is not revealed.
- **Theme B (``status`` / ``exclude_status``):** filters run through the
  effective status (``pyrite/storage/effective_status.py``) in SQL before
  ``LIMIT``, matching ``find_by_assignee`` (``queries.py``) and the search
  finders (``sqlite_backend``, ``postgres_backend``). A task whose children
  are all resolved counts as ``done`` though its file says ``open``; the
  acceptance's staging rows hold ``status: unprocessed``, the issue's own
  staging keyword.

The invariants under test:

- ``kb_names`` narrows the query; absent means every KB the caller can read;
- ``kb_names: []`` is a validation error, not "all";
- a KB the caller cannot read is refused ``KB_NOT_FOUND`` by the dispatcher;
- ``exclude_status`` drops only that status in SQL, so the page fills with
  rows from the other KBs instead of handing back a short page;
- ``status="done"`` matches a task done by its children with no rewrite;
- ``status`` and ``exclude_status`` naming the same value is a validation
  error;
- an entry with no status is kept by ``exclude_status`` and dropped by
  ``status``.

The rows go in through the storage layer (`upsert_entry`) rather than
marker files: the regime under test is the SQL filter, not the file loader,
and a filing task's ``date`` is unconstrained by the loader's enum checks.
"""

from __future__ import annotations

from pathlib import Path

import asyncio
import json

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams

from pyrite.config import KBConfig
from pyrite.server.mcp_server import PyriteMCPServer
from tests._world import build_world

CANON = "canon"
STAGING = "staging"

BASE_ARGS = {"date_from": "2026-01-01", "date_to": "2026-12-31", "min_importance": 1}


def _row(
    kb: str,
    entry_id: str,
    date: str | None,
    *,
    status: str | None = None,
    parent: str | None = None,
    entry_type: str = "event",
) -> dict:
    """One timeline row. Staging rows sort first (January < February); the
    statusable, dated rows are exactly what the acceptance asks for."""
    out: dict = {
        "id": entry_id,
        "kb_name": kb,
        "entry_type": entry_type,
        "title": f"{kb} work {entry_id}",
        "body": f"Work item {entry_id} in {kb}.",
        "summary": "",
        "tags": [],
        "sources": [],
        "links": [],
        "metadata": ({"parent": parent} if parent else {}),
        "importance": 5,
    }
    if date:
        out["date"] = date
    if status is not None:
        out["status"] = status
    return out


def _rows() -> list[dict]:
    staging = [
        _row(STAGING, f"s{i:02d}", f"2026-01-{i:02d}", status="unprocessed") for i in range(1, 11)
    ]
    canon = [_row(CANON, f"c{i:02d}", f"2026-02-{i:02d}") for i in range(1, 11)]
    return [
        *staging,
        *canon,
        # The effective-done task tree: p's file says `open`, both children
        # are resolved. The children carry no date (they are not timeline
        # rows), so the only dated task is `p` itself; they are tasks, or
        # the effective-status derivation would have no children to read.
        _row(CANON, "p", "2026-02-15", status="open", entry_type="task"),
        _row(CANON, "p-a", None, status="done", parent="p", entry_type="task"),
        _row(CANON, "p-b", None, status="cancelled", parent="p", entry_type="task"),
    ]


@pytest.fixture
def timeline_world(tmp_path: Path):
    w = build_world(
        tmp_path / "w",
        kbs=[
            KBConfig(name=CANON, path=tmp_path / "w" / CANON, kb_type="generic"),
            KBConfig(name=STAGING, path=tmp_path / "w" / STAGING, kb_type="generic"),
        ],
    )
    for row in _rows():
        w.db.backend.upsert_entry(row)
    yield w
    w.close()


@pytest.fixture
def server(timeline_world):
    s = PyriteMCPServer(config=timeline_world.config, tier="read")
    yield s
    s.close()


def _dispatch(server, args, *, readable_kbs=None):
    return server._dispatch_tool("kb_timeline", {**BASE_ARGS, **args}, readable_kbs=readable_kbs)


def _timeline(server, **extra):
    out = _dispatch(server, extra)
    assert "error_code" not in out, out
    return out


def test_kb_names_narrows_the_query(server):
    """Absent = every KB the caller can read; given, only those KBs.
    Unfiltered, staging fills the page -- the hallway bug reproduced."""
    default = _timeline(server, limit=10)
    assert default["count"] == 10  # staging sorts first: the hallway bug
    assert {e["kb_name"] for e in default["events"]} == {STAGING}

    named = _timeline(server, limit=20, kb_names=[CANON])
    assert named["count"] == 11  # ten canon rows + the task
    assert {e["kb_name"] for e in named["events"]} == {CANON}


def test_an_empty_kb_names_is_a_validation_error(server):
    """``kb_names: []`` names no KB; omitting it is how you say "all".
    The SQL clause's ``1 = 0`` is an internal rule for scopes, not a shape a
    caller may spell."""
    out = _dispatch(server, {"kb_names": []})
    assert out["error_code"] == "VALIDATION_FAILED", out


@pytest.mark.parametrize("names", [CANON, {"name": CANON}, [7], [""], ["   "], None, [CANON, 7]])
def test_kb_names_requires_a_list_of_nonblank_strings(server, names):
    out = _dispatch(server, {"kb_names": names})
    assert out["error_code"] == "VALIDATION_FAILED", out


def test_a_kb_the_caller_cannot_read_is_refused_before_the_handler(server):
    """The dispatcher refuses a named unreadable KB like an unknown one,
    so a private KB's existence is not revealed (P-R4, #952)."""
    out = _dispatch(server, {"kb_names": [STAGING]}, readable_kbs={CANON})
    assert out["error_code"] == "KB_NOT_FOUND", out


def test_an_unknown_kb_is_refused_kb_not_found(server):
    out = _dispatch(server, {"kb_names": ["no-such-kb"]})
    assert out["error_code"] == "KB_NOT_FOUND", out


def test_exclude_status_beats_limit(server):
    """``exclude_status`` runs in SQL before ``LIMIT``: staging rows (which
    fill the whole page unfiltered) are dropped and canon fills the page."""
    out = _timeline(server, limit=10, exclude_status=["unprocessed"])
    assert out["count"] == 10
    assert {e["kb_name"] for e in out["events"]} == {CANON}


def test_status_done_counts_a_task_done_by_its_children(server):
    """`p`'s file says ``open``; its children are all resolved, so it is
    effectively ``done`` -- the effective status, not the column."""
    out = _timeline(server, status="done")
    assert {e["id"] for e in out["events"]} == {"p"}


def test_status_and_exclude_status_naming_one_value_are_refused(server):
    out = _dispatch(server, {"status": "open", "exclude_status": ["open"]})
    assert out["error_code"] == "VALIDATION_FAILED", out


def test_no_status_is_kept_by_exclude_status_and_dropped_by_status(server):
    """The canon rows carry no status: ``exclude_status`` keeps them (an
    unset status is not `unprocessed`), ``status`` drops them (an unset
    status is not `done` either) -- and `p` reads as `done`, not `open`."""
    kept = _timeline(server, exclude_status=["unprocessed"])
    assert {e["id"] for e in kept["events"]} >= {"c01"}

    dropped = _timeline(server, status="done")
    assert {e["id"] for e in dropped["events"]} == {"p"}


def test_offset_paging_stays_stable_under_the_filters(server):
    """The 09-18 groom's paging regime: `kb_names` + the exclusion, paged
    by `offset`/`limit`, walk canon's rows once each."""
    seen: list[str] = []
    for offset in range(0, 15, 5):
        out = _timeline(
            server, kb_names=[CANON], exclude_status=["unprocessed"], limit=5, offset=offset
        )
        seen.extend(e["id"] for e in out["events"])
        if not out["events"]:
            break
    assert len(seen) == 11
    assert len(seen) == len(set(seen))  # no row twice


@pytest.mark.parametrize("exclude", ["unprocessed", 7, {}, ["done", 7], None])
def test_exclude_status_requires_a_list_of_strings(server, exclude):
    out = _dispatch(server, {"exclude_status": exclude})
    assert out["error_code"] == "VALIDATION_FAILED", out


def test_an_empty_exclude_status_is_a_validation_error(server):
    out = _dispatch(server, {"exclude_status": []})
    assert out["error_code"] == "VALIDATION_FAILED", out


@pytest.mark.control(reason="control: the dispatcher already refuses mixed readable/private KBs")
def test_mixed_readable_and_unreadable_kbs_are_refused(server):
    out = _dispatch(server, {"kb_names": [CANON, STAGING]}, readable_kbs={CANON})
    assert out["error_code"] == "KB_NOT_FOUND", out


def _sdk_call(server, arguments, *, readable_kbs=None):
    sdk = server.build_sdk_server(readable_kbs=readable_kbs)
    request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name="kb_timeline", arguments={**BASE_ARGS, **arguments}),
    )
    return asyncio.run(sdk.request_handlers[CallToolRequest](request)).root


@pytest.mark.control(
    reason="control: the SDK already validates the published array-of-strings schema"
)
@pytest.mark.parametrize("exclude", ["unprocessed", [7]])
def test_sdk_refuses_exclude_status_outside_the_published_shape(server, exclude):
    result = _sdk_call(server, {"exclude_status": exclude})
    assert result.isError
    assert "Input validation error" in result.content[0].text


@pytest.mark.control(
    reason="control: a valid array already filters actual timeline rows over the SDK"
)
def test_sdk_array_filter_returns_real_matching_rows(server):
    result = _sdk_call(server, {"exclude_status": ["unprocessed"]})
    assert not result.isError
    out = json.loads(result.content[0].text)
    assert out["count"] == 11
    assert {row["kb_name"] for row in out["events"]} == {CANON}


@pytest.mark.parametrize("argument", ["kb_names", "exclude_status"])
def test_sdk_empty_filter_lists_are_refused_consistently(server, argument):
    result = _sdk_call(server, {argument: []})
    out = json.loads(result.content[0].text)
    assert out["error_code"] == "VALIDATION_FAILED", out


@pytest.mark.control(reason="control: an empty scalar status intentionally means no status filter")
def test_empty_scalar_status_preserves_unfiltered_rows(server):
    default = _timeline(server)
    empty = _timeline(server, status="")
    assert default["count"] == empty["count"] == 21
    assert [row["id"] for row in empty["events"]] == [row["id"] for row in default["events"]]


@pytest.mark.control(
    reason="control: the real SDK call handler already limits rows to readable KBs"
)
@pytest.mark.parametrize(("readable", "count"), [({CANON}, 11), (set(), 0)])
def test_scoped_sdk_without_kb_names_reads_only_its_scope(server, readable, count):
    result = _sdk_call(server, {}, readable_kbs=readable)
    assert not result.isError
    out = json.loads(result.content[0].text)
    assert out["count"] == count
    assert {row["kb_name"] for row in out["events"]} <= readable


@pytest.mark.control(reason="control: the scoped SDK already refuses an explicitly unreadable KB")
def test_scoped_sdk_refuses_mixed_readable_and_unreadable_names(server):
    result = _sdk_call(server, {"kb_names": [CANON, STAGING]}, readable_kbs={CANON})
    out = json.loads(result.content[0].text)
    assert out["error_code"] == "KB_NOT_FOUND", out
