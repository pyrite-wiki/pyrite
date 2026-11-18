"""A first-class park for tasks: reason, kind and recheck date (#1688).

The property (the groom of #1688, section 3): a task's park state -- one of
``none``, ``parked``, ``due`` or ``person`` -- is a pure function of three
frontmatter fields (``parked_awaiting``, ``park_kind``, ``retest_after``) and
today's date, decided by one predicate, ``parked_state(task, today)``, which
never reads the clock. Every view reads it:

- ``task list --dispatchable`` leaves a ``parked`` or ``person`` task out with
  reason ``parked``; an open ``due`` task is listed with ``recheck: true``;
  every other live task that is ``due`` is listed under ``due_rechecks``, so a
  park whose date has come returns in ANY live status. A ``person`` park never
  returns to agents.
- ``task list --parked/--unparked`` (MCP ``parked``, REST ``?parked=``).

``task park`` / ``task unpark`` (CLI, MCP ``task_park`` / ``task_unpark``)
write the three fields; unpark REMOVES them. Neither changes the status.
Text in the body never parks a task.

Every expected value below is written by hand from that property, never
computed by the code under test. Today is injected by monkeypatching the
service's one clock read (``task_service._today``); the predicate is called
with it directly.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
import yaml

from pyrite.config import KBConfig, KBType
from tests._world import build_world

KB = "park"

BEFORE = dt.date(2026, 12, 9)
ON = dt.date(2026, 12, 10)
AFTER = dt.date(2026, 12, 11)
DATES = {"before": BEFORE, "on": ON, "after": AFTER}


def _task(
    tid: str,
    *,
    extra: str = "",
    status: str = "open",
    priority: int = 5,
    minute: int = 0,
    body: str = "Work.",
) -> str:
    return (
        f"---\nid: {tid}\ntitle: Task {tid}\ntype: task\nstatus: {status}\n"
        f"priority: {priority}\ncreated_at: '2026-01-01T00:{minute:02d}:00'\n{extra}---\n\n{body}\n"
    )


#: The listing fixture: one file per shape the groom names. Open tasks have
#: priority 5 and ascending minutes, so the dispatch order is this order.
FILES = {
    # -- open, waiting on the world, with a date --------------------------
    "ext-date": _task(  # kind absent counts as external; a bare YAML date
        "ext-date", minute=1, extra="parked_awaiting: foia-response\nretest_after: 2026-12-10\n"
    ),
    "ext-explicit": _task(  # explicit kind, a quoted date
        "ext-explicit",
        minute=2,
        extra="parked_awaiting: ruling\npark_kind: external\nretest_after: '2026-12-10'\n",
    ),
    "ext-datetime": _task(  # a datetime: its date counts
        "ext-datetime",
        minute=3,
        extra="parked_awaiting: reply\nretest_after: 2026-12-10T09:00:00\n",
    ),
    "only-date": _task(  # a hand-edited file with only retest_after
        "only-date", minute=4, extra="retest_after: 2026-12-10\n"
    ),
    # -- open, never back by date ------------------------------------------
    "person-date": _task(
        "person-date",
        minute=5,
        extra="parked_awaiting: payment\npark_kind: person\nretest_after: 2026-12-01\n",
    ),
    "unknown-kind": _task(  # an unknown kind is treated as person
        "unknown-kind",
        minute=6,
        extra="parked_awaiting: login\npark_kind: someday\nretest_after: 2026-12-01\n",
    ),
    "no-date": _task("no-date", minute=7, extra="parked_awaiting: outreach\n"),
    "bad-date": _task(  # a non-date retest_after: stays parked
        "bad-date", minute=8, extra="parked_awaiting: decision\nretest_after: next week\n"
    ),
    # -- not parks ------------------------------------------------------------
    "ws-reason": _task("ws-reason", minute=9, extra="parked_awaiting: '   '\n"),
    "body-prose": _task(
        "body-prose",
        minute=10,
        body="parked_awaiting: decision\nretest_after: 2020-01-01\npark_kind: external",
    ),
    "plain": _task("plain", minute=11),
    # -- live but not open, with a date ---------------------------------------
    "ip-due": _task(
        "ip-due",
        status="in_progress",
        priority=7,
        extra="assignee: agent:x\nparked_awaiting: reply\nretest_after: 2026-12-10\n",
    ),
    "claimed-due": _task(
        "claimed-due",
        status="claimed",
        priority=7,
        extra="assignee: agent:y\nparked_awaiting: reply\nretest_after: 2026-12-08\n",
    ),
    "blocked-due": _task(
        "blocked-due",
        status="blocked",
        priority=3,
        extra="assignee: agent:z\nparked_awaiting: ruling\nretest_after: 2026-12-10\n",
    ),
    "open-assigned-due": _task(
        "open-assigned-due",
        minute=12,
        extra="assignee: mark\nparked_awaiting: reply\nretest_after: 2026-12-10\n",
    ),
    "person-ip": _task(
        "person-ip",
        status="in_progress",
        extra="assignee: agent:x\nparked_awaiting: payment\npark_kind: person\n"
        "retest_after: 2026-12-01\n",
    ),
    "done-due": _task(  # resolved: never a recheck
        "done-due", status="done", extra="parked_awaiting: reply\nretest_after: 2026-12-01\n"
    ),
}

#: Literal: each file's park state on each date.
STATE = {
    "before": {
        "ext-date": "parked",
        "ext-explicit": "parked",
        "ext-datetime": "parked",
        "only-date": "parked",
        "person-date": "person",
        "unknown-kind": "person",
        "no-date": "parked",
        "bad-date": "parked",
        "ws-reason": "none",
        "body-prose": "none",
        "plain": "none",
        "ip-due": "parked",
        "claimed-due": "due",
        "blocked-due": "parked",
        "open-assigned-due": "parked",
        "person-ip": "person",
        "done-due": "due",
    },
}
STATE["on"] = {
    **STATE["before"],
    "ext-date": "due",
    "ext-explicit": "due",
    "ext-datetime": "due",
    "only-date": "due",
    "ip-due": "due",
    "blocked-due": "due",
    "open-assigned-due": "due",
}
STATE["after"] = dict(STATE["on"])

#: Literal: the dispatchable ids on each date, in order, and which carry recheck.
DISPATCHABLE = {
    "before": ["ws-reason", "body-prose", "plain"],
    "on": ["ext-date", "ext-explicit", "ext-datetime", "only-date", "ws-reason", "body-prose",
           "plain"],
    "after": ["ext-date", "ext-explicit", "ext-datetime", "only-date", "ws-reason",
              "body-prose", "plain"],
}  # fmt: skip
RECHECK = {
    "before": set(),
    "on": {"ext-date", "ext-explicit", "ext-datetime", "only-date"},
    "after": {"ext-date", "ext-explicit", "ext-datetime", "only-date"},
}
#: Literal: each open task left out, with its reason.
EXCLUDED = {
    "before": {
        "ext-date": "parked",
        "ext-explicit": "parked",
        "ext-datetime": "parked",
        "only-date": "parked",
        "person-date": "parked",
        "unknown-kind": "parked",
        "no-date": "parked",
        "bad-date": "parked",
        "open-assigned-due": "assigned",
    },
    "on": {
        "person-date": "parked",
        "unknown-kind": "parked",
        "no-date": "parked",
        "bad-date": "parked",
        "open-assigned-due": "assigned",
    },
}
EXCLUDED["after"] = dict(EXCLUDED["on"])
#: Literal: due_rechecks in order -- priority highest first, then the date,
#: then id. A person park and a resolved task are never in it.
DUE_RECHECKS = {
    "before": ["claimed-due"],
    "on": ["claimed-due", "ip-due", "open-assigned-due", "blocked-due"],
    "after": ["claimed-due", "ip-due", "open-assigned-due", "blocked-due"],
}

PARKED_IDS = {tid for tid, state in STATE["on"].items() if state != "none"}
UNPARKED_IDS = {"ws-reason", "body-prose", "plain"}


# -- The predicate, pure ----------------------------------------------------------


def _state(today, **fields):
    from pyrite.models.task_queue import parked_state

    return parked_state(fields, today)


@pytest.mark.parametrize(
    ("reason", "parks"),
    [
        (None, False),  # absent
        ("", False),
        ("   ", False),  # whitespace
        ("''", False),  # the literal two-quote text a shell leaves
        (["browser-session"], True),  # a list
        ([], False),
        ("decision", True),
    ],
    ids=["absent", "empty", "whitespace", "quoted-empty", "list", "empty-list", "reason"],
)
def test_reason_forms(reason, parks):
    fields = {} if reason is None else {"parked_awaiting": reason}
    assert _state(ON, **fields) == ("parked" if parks else "none")


@pytest.mark.parametrize(
    ("retest_after", "before", "on", "after"),
    [
        (None, "parked", "parked", "parked"),  # absent: parked until unparked
        (dt.date(2026, 12, 10), "parked", "due", "due"),  # a date
        ("2026-12-10", "parked", "due", "due"),  # a quoted date
        (dt.datetime(2026, 12, 10, 23, 59), "parked", "due", "due"),  # a datetime
        ("2026-12-10T08:00:00Z", "parked", "due", "due"),  # a datetime string
        ("next week", "parked", "parked", "parked"),  # not a date: never due
        ("2026-13-01", "parked", "parked", "parked"),  # not a real date
    ],
    ids=["absent", "date", "quoted", "datetime", "datetime-str", "non-date", "bad-month"],
)
def test_retest_after_shapes_around_today(retest_after, before, on, after):
    fields = {"parked_awaiting": "reply"}
    if retest_after is not None:
        fields["retest_after"] = retest_after
    assert [_state(d, **fields) for d in (BEFORE, ON, AFTER)] == [before, on, after]


@pytest.mark.parametrize(
    ("kind", "on"),
    [(None, "due"), ("external", "due"), ("person", "person"), ("someday", "person"),
     ("", "due"), (" Person ", "person")],
    ids=["absent", "external", "person", "unknown", "empty", "spaced-capital"],
)  # fmt: skip
def test_park_kinds(kind, on):
    fields = {"parked_awaiting": "x", "retest_after": "2026-12-10"}
    if kind is not None:
        fields["park_kind"] = kind
    assert _state(ON, **fields) == on
    # A person park never comes back by date: not before, not long after.
    if on == "person":
        assert _state(dt.date(2099, 1, 1), **fields) == "person"
        assert _state(BEFORE, **fields) == "person"


def test_only_retest_after_is_parked_until_its_date():
    assert _state(BEFORE, retest_after="2026-12-10") == "parked"
    assert _state(ON, retest_after="2026-12-10") == "due"
    # And empty values of all three are no park.
    assert _state(ON, parked_awaiting="", park_kind="", retest_after="") == "none"


def test_the_predicate_never_reads_the_clock(monkeypatch):
    """The same inputs give the same answer whatever the clock says."""
    import pyrite.models.task_queue as tq

    class _Exploding(dt.date):
        @classmethod
        def today(cls):
            raise AssertionError("parked_state read the clock")

    monkeypatch.setattr(tq, "date", _Exploding, raising=False)
    monkeypatch.setattr(tq, "datetime", _Exploding, raising=False)
    assert tq.parked_state({"parked_awaiting": "x", "retest_after": "2026-12-10"}, ON) == "due"


# -- The listing views, on every surface ------------------------------------------


def _build(root: Path, files: dict[str, str]):
    path = root / KB
    path.mkdir()
    (path / "kb.yaml").write_text(f"name: {KB}\nkb_type: generic\n", encoding="utf-8")
    for tid, text in files.items():
        (path / f"{tid}.md").write_text(text, encoding="utf-8")
    return build_world(root, kbs=[KBConfig(name=KB, path=path, kb_type=KBType.GENERIC)])


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    w = _build(tmp_path_factory.mktemp("park"), FILES)
    try:
        yield w
    finally:
        w.close()


@pytest.fixture
def today(monkeypatch):
    """Set the service's one clock read; returns the setter."""
    import pyrite.services.task_service as ts

    def set_today(day: dt.date) -> None:
        monkeypatch.setattr(ts, "_today", lambda: day, raising=False)

    return set_today


def _cli_list(world, *args):
    result = world.cli("task", "list", "-k", KB, *args, "--format", "json")
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _mcp_list(world, **args):
    out = world.mcp(tier="read")._dispatch_tool("task_list", {"kb_name": KB, **args})
    assert "error" not in out, out
    return out


def _rest_list(world, **params):
    response = world.client.get("/api/tasks", params={"kb": KB, **params})
    assert response.status_code == 200, response.text
    return response.json()


DISPATCH = {
    "cli": lambda w: _cli_list(w, "--dispatchable"),
    "mcp": lambda w: _mcp_list(w, dispatchable=True),
    "rest": lambda w: _rest_list(w, dispatchable="true"),
}


@pytest.mark.parametrize("when", list(DATES))
@pytest.mark.parametrize("surface", list(DISPATCH))
def test_dispatchable_around_the_recheck_date(world, today, surface, when):
    today(DATES[when])
    out = DISPATCH[surface](world)
    assert [t["id"] for t in out["tasks"]] == DISPATCHABLE[when]
    assert {t["id"] for t in out["tasks"] if t.get("recheck")} == RECHECK[when]
    assert {e["id"]: e["reason"] for e in out["excluded"]} == EXCLUDED[when]
    assert [d["id"] for d in out["due_rechecks"]] == DUE_RECHECKS[when]


@pytest.mark.parametrize("surface", list(DISPATCH))
def test_a_recheck_row_says_why_and_when(world, today, surface):
    today(ON)
    out = DISPATCH[surface](world)
    row = next(t for t in out["tasks"] if t["id"] == "ext-explicit")
    assert row["recheck"] is True
    assert row["parked_awaiting"] == "ruling"
    assert row["park_kind"] == "external"
    assert row["retest_after"] == "2026-12-10"
    due = next(d for d in out["due_rechecks"] if d["id"] == "ip-due")
    assert due == {
        "id": "ip-due",
        "kb_name": KB,
        "status": "in_progress",
        "priority": 7,
        "assignee": "agent:x",
        "parked_awaiting": "reply",
        "park_kind": "",
        "retest_after": "2026-12-10",
    }


def test_a_person_park_never_returns_to_agents(world, today):
    for day in (BEFORE, ON, AFTER, dt.date(2099, 1, 1)):
        today(day)
        out = _cli_list(world, "--dispatchable")
        listed = {t["id"] for t in out["tasks"]} | {d["id"] for d in out["due_rechecks"]}
        assert not listed & {"person-date", "unknown-kind", "person-ip"}, day


PARKED = {
    "cli": lambda w, on: _cli_list(w, "--parked" if on else "--unparked"),
    "mcp": lambda w, on: _mcp_list(w, parked=on),
    "rest": lambda w, on: _rest_list(w, parked="true" if on else "false"),
}


@pytest.mark.parametrize("surface", list(PARKED))
def test_parked_and_unparked_filters_agree(world, today, surface):
    today(ON)
    parked = PARKED[surface](world, True)
    unparked = PARKED[surface](world, False)
    assert {t["id"] for t in parked["tasks"]} == PARKED_IDS
    assert {t["id"] for t in unparked["tasks"]} == UNPARKED_IDS
    assert {t["id"]: t["park_state"] for t in parked["tasks"]} == {
        tid: STATE["on"][tid] for tid in PARKED_IDS
    }
    assert {t["park_state"] for t in unparked["tasks"]} == {"none"}


def test_parked_filter_with_status_counts_in_progress(world, today):
    today(ON)
    out = _cli_list(world, "--parked", "--status", "in_progress")
    assert {t["id"] for t in out["tasks"]} == {"ip-due", "person-ip"}


def test_the_listing_reads_whitespace_the_same_with_and_without_assignee(tmp_path, today):
    """The main read and the assignee hydration pass agree: whitespace is no
    reason, on both paths."""
    files = {
        "ws": _task("ws", extra="assignee: mark\nparked_awaiting: '   '\n"),
        "real": _task("real", extra="assignee: mark\nparked_awaiting: ' decision '\n"),
    }
    w = _build(tmp_path, files)
    try:
        today(ON)
        plain = {t["id"]: t["parked_awaiting"] for t in _cli_list(w)["tasks"]}
        by_assignee = {t["id"]: t["parked_awaiting"] for t in _cli_list(w, "-a", "mark")["tasks"]}
        assert plain == by_assignee == {"ws": "", "real": "decision"}
    finally:
        w.close()


# -- park / unpark ------------------------------------------------------------------


@pytest.fixture
def wworld(tmp_path):
    w = _build(
        tmp_path,
        {
            "t": _task("t"),
            "ip": _task("ip", status="in_progress", extra="assignee: agent:x\n"),
            "hand": _task("hand", extra="retest_after: 2026-12-10\n"),
            "a-note": "---\nid: a-note\ntitle: A note\ntype: note\n---\n\nText.\n",
        },
    )
    with w.pointed():
        yield w
    w.close()


def _front(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])


def _park_keys(path: Path) -> list[str]:
    front_text = path.read_text(encoding="utf-8").split("---", 2)[1]
    return [
        line
        for line in front_text.splitlines()
        if line.split(":", 1)[0] in ("parked_awaiting", "park_kind", "retest_after")
    ]


def _cli(world, *args):
    result = world.cli("task", *args, "-k", KB, "--format", "json")
    return result.exit_code, json.loads(result.stdout)


@pytest.mark.parametrize("tid", ["t", "ip"])
def test_park_writes_the_three_fields_and_keeps_the_status(wworld, tid):
    path = wworld.kb_path(KB) / f"{tid}.md"
    status = _front(path)["status"]
    code, data = _cli(
        wworld, "park", tid, "--reason", "foia-response", "--kind", "external",
        "--retest-after", "2026-12-10",
    )  # fmt: skip
    assert code == 0, data
    front = _front(path)
    assert front["status"] == status
    assert front["parked_awaiting"] == "foia-response"
    assert front["park_kind"] == "external"
    assert str(front["retest_after"]) == "2026-12-10"
    assert data["task_id"] == tid and data["status"] == status
    assert data["parked_awaiting"] == "foia-response"
    assert data["park_kind"] == "external"
    assert data["retest_after"] == "2026-12-10"


def test_a_new_park_replaces_the_old_one(wworld):
    path = wworld.kb_path(KB) / "t.md"
    _cli(wworld, "park", "t", "--reason", "a", "--kind", "person", "--retest-after", "2026-12-10")
    code, data = _cli(wworld, "park", "t", "--reason", "b")
    assert code == 0, data
    front = _front(path)
    assert front["parked_awaiting"] == "b"
    assert "park_kind" not in front and "retest_after" not in front


def test_unpark_removes_the_keys_and_leaves_no_empty_values(wworld):
    path = wworld.kb_path(KB) / "ip.md"
    before = _front(path)
    _cli(wworld, "park", "ip", "--reason", "r", "--kind", "person", "--retest-after", "2026-12-10")
    code, data = _cli(wworld, "unpark", "ip")
    assert code == 0, data
    assert _park_keys(path) == []
    after = _front(path)
    after.pop("updated_at", None)
    before.pop("updated_at", None)
    assert after == before  # status, assignee and everything else as before the park
    assert sorted(data["removed"]) == ["park_kind", "parked_awaiting", "retest_after"]


def test_unpark_a_hand_edited_file_with_only_retest_after(wworld):
    path = wworld.kb_path(KB) / "hand.md"
    code, data = _cli(wworld, "unpark", "hand")
    assert code == 0, data
    assert _park_keys(path) == []
    assert data["removed"] == ["retest_after"]


def test_unpark_an_unparked_task_changes_nothing(wworld):
    path = wworld.kb_path(KB) / "t.md"
    before = path.read_bytes()
    code, data = _cli(wworld, "unpark", "t")
    assert code == 0, data
    assert data["removed"] == []
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "args",
    [
        ["--reason", ""],
        ["--reason", "   "],
        ["--reason", "r", "--kind", "someday"],
        ["--reason", "r", "--retest-after", "next week"],
        ["--reason", "r", "--retest-after", "2026-13-01"],
        ["--reason", "r", "--retest-after", "2026-12-10T09:00"],
    ],
    ids=["empty", "whitespace", "unknown-kind", "non-date", "bad-month", "datetime"],
)
def test_park_refuses_and_writes_nothing(wworld, args):
    path = wworld.kb_path(KB) / "t.md"
    before = path.read_bytes()
    code, data = _cli(wworld, "park", "t", *args)
    assert code != 0, data
    assert data.get("error_code") == "VALIDATION_FAILED", data
    assert path.read_bytes() == before


def test_park_refuses_an_entry_that_is_not_a_task(wworld):
    path = wworld.kb_path(KB) / "a-note.md"
    before = path.read_bytes()
    code, data = _cli(wworld, "park", "a-note", "--reason", "r")
    assert code != 0 and data.get("error_code") == "VALIDATION_FAILED", data
    assert path.read_bytes() == before
    code, data = _cli(wworld, "unpark", "a-note")
    assert code != 0 and data.get("error_code") == "VALIDATION_FAILED", data
    assert path.read_bytes() == before


def test_park_a_missing_task_is_entry_not_found(wworld):
    code, data = _cli(wworld, "park", "nope", "--reason", "r")
    assert code != 0
    assert data["error_code"] == "ENTRY_NOT_FOUND", data


def _mcp(world, tool, **args):
    return world.mcp("write")._dispatch_tool(tool, {"kb_name": KB, **args})


def test_cli_and_mcp_park_and_unpark_return_one_shape(wworld):
    _, cli_park = _cli(wworld, "park", "t", "--reason", "r", "--retest-after", "2026-12-10")
    mcp_park = _mcp(wworld, "task_park", task_id="ip", reason="r", retest_after="2026-12-10")
    assert "error" not in mcp_park, mcp_park
    drop = {"outcome", "task_id", "status", "park_state"}
    assert {k: v for k, v in cli_park.items() if k not in drop} == {
        k: v for k, v in mcp_park.items() if k not in drop
    }
    assert set(cli_park) - {"outcome"} == set(mcp_park)
    _, cli_un = _cli(wworld, "unpark", "t")
    mcp_un = _mcp(wworld, "task_unpark", task_id="ip")
    assert "error" not in mcp_un, mcp_un
    assert cli_un["removed"] == mcp_un["removed"] == ["parked_awaiting", "retest_after"]
    assert set(cli_un) - {"outcome"} == set(mcp_un)


def test_mcp_park_refuses_an_unknown_kind(wworld):
    path = wworld.kb_path(KB) / "t.md"
    before = path.read_bytes()
    out = _mcp(wworld, "task_park", task_id="t", reason="r", kind="someday")
    assert out.get("error_code") == "VALIDATION_FAILED", out
    assert path.read_bytes() == before


def test_mcp_park_tools_are_write_tier():
    from pyrite.server.tool_schemas import ADMIN_TOOLS, READ_TOOLS, WRITE_TOOLS

    assert {"task_park", "task_unpark"} <= set(WRITE_TOOLS)
    assert not {"task_park", "task_unpark"} & (set(READ_TOOLS) | set(ADMIN_TOOLS))
    kind = WRITE_TOOLS["task_park"]["inputSchema"]["properties"]["kind"]
    assert kind["enum"] == ["person", "external"]
    assert WRITE_TOOLS["task_park"]["inputSchema"]["required"] == ["task_id", "kb_name", "reason"]
    assert WRITE_TOOLS["task_unpark"]["inputSchema"]["required"] == ["task_id", "kb_name"]


def test_parked_then_dispatchable_through_the_commands(wworld, today):
    """The groom's acceptance, end to end through the write commands."""
    _cli(wworld, "park", "t", "--reason", "reply", "--retest-after", "2026-12-10")
    _cli(wworld, "park", "ip", "--reason", "ruling", "--retest-after", "2026-12-10")
    for day, listed, due in [
        (BEFORE, False, []),
        (ON, True, ["ip"]),
        (AFTER, True, ["ip"]),
    ]:
        today(day)
        out = _cli_list(wworld, "--dispatchable")
        ids = {t["id"]: t for t in out["tasks"]}
        assert ("t" in ids) is listed, day
        if listed:
            assert ids["t"]["recheck"] is True
        assert [d["id"] for d in out["due_rechecks"] if d["id"] in ("t", "ip")] == due
    _cli(wworld, "unpark", "t")
    today(BEFORE)
    out = _cli_list(wworld, "--dispatchable")
    assert "recheck" not in next(t for t in out["tasks"] if t["id"] == "t")


# -- QA reports, never rewrites --------------------------------------------------


def test_qa_reports_bad_park_fields_and_writes_nothing(tmp_path):
    files = {
        "q-kind": _task("q-kind", extra="parked_awaiting: x\npark_kind: someday\n"),
        "q-date": _task("q-date", extra="parked_awaiting: x\nretest_after: next week\n"),
        "q-noreason": _task("q-noreason", extra="retest_after: 2026-12-10\n"),
        "q-ok": _task(
            "q-ok", extra="parked_awaiting: x\npark_kind: person\nretest_after: 2026-12-10\n"
        ),
    }
    w = _build(tmp_path, files)
    try:
        root = w.kb_path(KB)
        before = {p.name: p.read_bytes() for p in root.glob("*.md")}
        result = w.cli("qa", "validate", "-k", KB, "--format", "json")
        data = json.loads(result.stdout)
        found = {
            (i["entry_id"], i["field"]) for i in data["issues"] if i.get("rule") == "task_park"
        }
        assert found == {
            ("q-kind", "park_kind"),
            ("q-date", "retest_after"),
            ("q-noreason", "parked_awaiting"),
        }
        assert {p.name: p.read_bytes() for p in root.glob("*.md")} == before
    finally:
        w.close()
