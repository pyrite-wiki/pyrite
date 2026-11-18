"""`task list --dispatchable`: the tasks a worker can take now, from one derived view (#1686).

The property (the groom of #1686, section 3): a task is dispatchable if and
only if its effective status (ADR-0045 decision 7) is ``open``, its index row
still has a file, it has no children, no assignee, is not parked, and every
dependency exists and is resolved. Each excluded open task carries exactly
one reason from ``DISPATCH_REASONS``. Every surface (CLI, MCP, REST) asks
``TaskService.list_tasks``, so all three return the same tasks in the same
order -- priority highest first, then oldest ``created_at``, then KB, then
id -- with the same reason for each excluded task.

The fixture is the groom's Shapes list, one file per shape, and every
expected value below is written by hand from the property, never computed
by the classifier under test.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from pyrite.config import KBConfig, KBType

sys.path.insert(0, str(Path(__file__).parent))
from _world import build_world  # noqa: E402

WORK = "work"
OTHER = "other"
EMPTY = "empty"
PARKED_ONLY = "parked-only"


def _task(
    tid: str,
    *,
    status: str | None = "open",
    priority: int = 5,
    created: str = "2026-01-02T00:00:00",
    extra: str = "",
    body: str = "Work.",
) -> str:
    status_line = f"status: {status}\n" if status is not None else ""
    return (
        f"---\nid: {tid}\ntitle: Task {tid}\ntype: task\n{status_line}"
        f"priority: {priority}\ncreated_at: '{created}'\n{extra}---\n\n{body}\n"
    )


def _t(minute: int) -> str:
    return f"2026-01-01T00:{minute:02d}:00"


#: KB `work`: the groom's shapes. Comment = the shape it stands for.
WORK_FILES = {
    # -- parked_awaiting (7 shapes, plus the spellings of "empty") --------
    "pk-absent": _task("pk-absent", created=_t(30)),  # absent
    "pk-empty": _task("pk-empty", created=_t(5), extra='parked_awaiting: ""\n'),  # ""
    "pk-ws": _task("pk-ws", created=_t(40), extra="parked_awaiting: '   '\n"),  # whitespace
    # quoted empty: `''` in YAML, and the literal two-apostrophe text a shell
    # can leave behind; neither says why the task waits.
    "pk-quoted": _task("pk-quoted", created=_t(40), extra="parked_awaiting: ''\n"),
    "pk-literal-quotes": _task(
        "pk-literal-quotes", created=_t(41), extra="parked_awaiting: \"''\"\n"
    ),
    "pk-null": _task("pk-null", created=_t(1), extra="parked_awaiting: null\n"),  # non-string
    "pk-emptylist": _task("pk-emptylist", created=_t(42), extra="parked_awaiting: []\n"),
    "pk-list": _task("pk-list", extra="parked_awaiting: [browser-session]\n"),  # a list
    "pk-real": _task("pk-real", extra="parked_awaiting: decision\n"),  # a real reason
    "pk-body": _task(  # the word only in body prose
        "pk-body",
        created=_t(50),
        body="Was once `parked_awaiting: decision`; the word in prose parks nothing.",
    ),
    # -- status: the 8 TASK_STATUSES, unset, and complete by children ------
    "st-open": _task("st-open", created=_t(55)),
    "st-unset": _task("st-unset", status=None, created=_t(56)),
    "st-claimed": _task("st-claimed", status="claimed"),
    "st-in_progress": _task("st-in_progress", status="in_progress"),
    "st-blocked": _task("st-blocked", status="blocked"),
    "st-review": _task("st-review", status="review"),
    "st-done": _task("st-done", status="done"),
    "st-failed": _task("st-failed", status="failed"),
    "st-cancelled": _task("st-cancelled", status="cancelled"),
    "par-done": _task("par-done"),  # file says open, its only child is done
    "par-done-a": _task("par-done-a", status="done", extra="parent: par-done\n"),
    # -- dependencies -----------------------------------------------------
    "dep-list-ok": _task(
        "dep-list-ok", priority=6, created=_t(20), extra="dependencies: [st-done]\n"
    ),
    "dep-list-open": _task("dep-list-open", extra="dependencies: [st-done, st-open]\n"),
    "dep-str": _task("dep-str", extra="dependencies: st-open\n"),  # a bare string
    "dep-missing": _task("dep-missing", extra="dependencies: [no-such-task]\n"),
    "dep-kb-ok": _task("dep-kb-ok", created=_t(57), extra="dependencies: ['other:o-done']\n"),
    "dep-kb-open": _task("dep-kb-open", extra="dependencies: ['other:o-open']\n"),
    "dep-parent-done": _task(  # a dependency complete by its children
        "dep-parent-done", priority=4, created=_t(15), extra="dependencies: [par-done]\n"
    ),
    "dep-cancelled": _task(
        "dep-cancelled", priority=4, created=_t(12), extra="dependencies: [st-cancelled]\n"
    ),
    "cyc-a": _task("cyc-a", extra="dependencies: [cyc-b]\n"),
    "cyc-b": _task("cyc-b", extra="dependencies: [cyc-a]\n"),
    # -- children: via `parent`, via legacy `parent_task` -------------------
    # A high-priority parent with low-priority children: the children are
    # dispatchable, the parent is not.
    "hp-parent": _task("hp-parent", priority=9),
    "hp-c1": _task("hp-c1", priority=2, created=_t(3), extra="parent: hp-parent\n"),
    "hp-c2": _task("hp-c2", priority=1, created=_t(2), extra="parent: hp-parent\n"),
    "lg-parent": _task("lg-parent"),
    "lg-child": _task("lg-child", priority=3, created=_t(0), extra="parent_task: lg-parent\n"),
    # -- assignee ---------------------------------------------------------
    "as-empty": _task("as-empty", priority=8, created=_t(10), extra='assignee: ""\n'),
    "as-agent": _task("as-agent", extra="assignee: agent:x\n"),
    "as-person": _task("as-person", extra="assignee: mark\n"),
    # -- an index row whose file is gone (deleted after indexing) -----------
    "orph": _task("orph", priority=10),
}
OTHER_FILES = {
    "o-done": _task("o-done", status="done"),
    "o-open": _task("o-open", priority=1, created=_t(1)),
}
PARKED_FILES = {
    "only-a": _task("only-a", extra="parked_awaiting: decision\n"),
    "only-b": _task("only-b", extra="parked_awaiting: outreach\n"),
}

#: Literal: the dispatchable tasks across `work` and `other`, in order.
#: Priority 8, 6, then the 5s oldest first (pk-quoted/pk-ws share a minute:
#: id decides), then 4, 3, 2, and the two 1s oldest first.
DISPATCHABLE_ORDER = [
    "as-empty",
    "dep-list-ok",
    "pk-null",
    "pk-empty",
    "pk-absent",
    "pk-quoted",
    "pk-ws",
    "pk-literal-quotes",
    "pk-emptylist",
    "pk-body",
    "st-open",
    "st-unset",
    "dep-kb-ok",
    "dep-cancelled",
    "dep-parent-done",
    "lg-child",
    "hp-c1",
    "o-open",
    "hp-c2",
]

#: Literal: each open task that is not dispatchable, and its one reason.
EXCLUDED = {
    "orph": "orphaned",
    "hp-parent": "has_children",
    "lg-parent": "has_children",
    "as-agent": "assigned",
    "as-person": "assigned",
    "pk-list": "parked",
    "pk-real": "parked",
    "dep-list-open": "blocked",
    "dep-str": "blocked",
    "dep-missing": "blocked",
    "dep-kb-open": "blocked",
    "cyc-a": "blocked",
    "cyc-b": "blocked",
}

#: Literal: tasks whose effective status is not open (counted, not listed).
NOT_OPEN = {
    "st-claimed",
    "st-in_progress",
    "st-blocked",
    "st-review",
    "st-done",
    "st-failed",
    "st-cancelled",
    "par-done",  # file `open`, done by its child (ADR-0045 decision 7)
    "par-done-a",
    "o-done",
}

#: Literal: what `counts` says over `work` + `other`.
COUNTS = {
    "dispatchable": 19,
    "not_open": 10,
    "orphaned": 1,
    "has_children": 2,
    "assigned": 2,
    "parked": 2,
    "blocked": 6,
}

#: The reason each shape is expected to produce (None = dispatchable).
EXPECTED_REASON: dict[str, str | None] = {
    **dict.fromkeys(DISPATCHABLE_ORDER),
    **EXCLUDED,
    **dict.fromkeys(NOT_OPEN, "not_open"),
}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("dispatch")
    seeds = {WORK: WORK_FILES, OTHER: OTHER_FILES, PARKED_ONLY: PARKED_FILES, EMPTY: {}}
    kbs = []
    for name, files in seeds.items():
        path = root / name
        path.mkdir()
        (path / "kb.yaml").write_text(f"name: {name}\nkb_type: generic\n", encoding="utf-8")
        for tid, text in files.items():
            (path / f"{tid}.md").write_text(text, encoding="utf-8")
        kbs.append(KBConfig(name=name, path=path, kb_type=KBType.GENERIC))
    w = build_world(root, kbs=kbs)
    (root / WORK / "orph.md").unlink()  # the index row stays; the file is gone
    try:
        yield w
    finally:
        w.close()


# -- Surfaces -----------------------------------------------------------------


def cli(world, *args):
    result = world.cli("task", "list", *args, "--format", "json")
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def mcp(world, **args):
    out = world.mcp(tier="read")._dispatch_tool("task_list", args)
    assert "error" not in out, out
    return out


def rest(world, **params):
    response = world.client.get("/api/tasks", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _scoped(out):
    """Drop the two KBs the cross-surface fixture does not name."""
    tasks = [t for t in out["tasks"] if t["kb_name"] in (WORK, OTHER)]
    excluded = [e for e in out.get("excluded", []) if e["kb_name"] in (WORK, OTHER)]
    return tasks, excluded


SURFACES = {
    "cli": lambda w: cli(w, "--dispatchable"),
    "mcp": lambda w: mcp(w, dispatchable=True),
    "rest": lambda w: rest(w, dispatchable="true"),
}


class _Answers(dict):
    """Each surface's answer, asked once, inside the first test that reads it
    (so a surface that refuses fails that test rather than erroring setup)."""

    def __init__(self, world):
        super().__init__()
        self._world = world

    def __missing__(self, name):
        self[name] = SURFACES[name](self._world)
        return self[name]


@pytest.fixture(scope="module")
def answers(world):
    return _Answers(world)


# -- Acceptance -----------------------------------------------------------------


@pytest.mark.parametrize("surface", list(SURFACES))
def test_dispatchable_ids_in_priority_then_age_order(answers, surface):
    tasks, _ = _scoped(answers[surface])
    assert [t["id"] for t in tasks] == DISPATCHABLE_ORDER


@pytest.mark.parametrize("surface", list(SURFACES))
def test_each_excluded_open_task_has_its_one_reason(answers, surface):
    _, excluded = _scoped(answers[surface])
    assert {e["id"]: e["reason"] for e in excluded} == EXCLUDED
    assert len(excluded) == len(EXCLUDED)  # one row per task, never two


def test_the_three_surfaces_agree_row_for_row(answers):
    """Same tasks, same order, same rows, same reasons, same counts."""
    cli_out, mcp_out, rest_out = answers["cli"], answers["mcp"], answers["rest"]
    for key in ("tasks", "excluded", "counts", "count"):
        assert cli_out[key] == mcp_out[key] == rest_out[key], key


def test_counts_cover_every_task_in_scope(world):
    out = cli(world, "--dispatchable", "-k", WORK)
    # `-k work`: o-open and o-done are not listed; `other:` dependencies are no
    # longer in this listing's scope, so dep-kb-ok is blocked too.
    want = dict(COUNTS, dispatchable=17, not_open=9, blocked=7)
    assert out["counts"] == want
    assert out["count"] == 17 == len(out["tasks"])
    assert {e["id"]: e["reason"] for e in out["excluded"]}["dep-kb-ok"] == "blocked"


@pytest.mark.parametrize("shape", sorted(EXPECTED_REASON))
def test_each_shape_gets_its_reason(answers, shape):
    """The groom's Shapes list, one cell per shape, on the CLI's answer."""
    tasks, excluded = _scoped(answers["cli"])
    listed = {t["id"] for t in tasks}
    reasons = {e["id"]: e["reason"] for e in excluded}
    want = EXPECTED_REASON[shape]
    if want is None:
        assert shape in listed and shape not in reasons
    elif want == "not_open":
        assert shape not in listed and shape not in reasons
    else:
        assert shape not in listed and reasons.get(shape) == want


def test_every_reason_has_a_shape():
    """A new reason cannot land without a fixture shape that produces it."""
    from pyrite.models.task_queue import DISPATCH_REASONS

    produced = {r for r in EXPECTED_REASON.values() if r is not None}
    assert produced == set(DISPATCH_REASONS)
    assert set(COUNTS) == {"dispatchable", *DISPATCH_REASONS}


@pytest.mark.parametrize("surface", ["cli", "mcp", "rest"])
def test_fields_projects_each_row(world, surface):
    if surface == "cli":
        out = cli(world, "--dispatchable", "--fields", "id,priority", "-k", WORK)
    elif surface == "mcp":
        out = mcp(world, dispatchable=True, fields=["id", "priority"], kb_name=WORK)
    else:
        out = rest(world, dispatchable="true", fields="id,priority", kb=WORK)
    # The identity pair is always kept (read_shaping.IDENTITY_FIELDS).
    assert {frozenset(t) for t in out["tasks"]} == {frozenset({"id", "kb_name", "priority"})}
    assert out["tasks"][0] == {"id": "as-empty", "kb_name": WORK, "priority": 8}


def test_fields_without_dispatchable_projects_the_plain_listing(world):
    out = cli(world, "--fields", "id,status", "-k", WORK)
    assert {frozenset(t) for t in out["tasks"]} == {frozenset({"id", "kb_name", "status"})}
    assert "counts" not in out and "excluded" not in out


def test_rich_fields_table_shows_the_named_columns(world):
    result = world.cli("task", "list", "-k", WORK, "--dispatchable", "--fields", "id,priority",
                       "--format", "rich")  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "as-empty" in result.stdout and "priority" in result.stdout
    assert "Task as-empty" not in result.stdout  # the title was not asked for


# -- Regimes ----------------------------------------------------------------------


def test_empty_kb_is_done_with_count_zero(world):
    out = cli(world, "--dispatchable", "-k", EMPTY)
    assert out["outcome"] == "done"
    assert out["count"] == 0 and out["tasks"] == [] and out["excluded"] == []
    assert out["counts"] == dict.fromkeys(COUNTS, 0)


def test_kb_with_only_parked_tasks(world):
    out = cli(world, "--dispatchable", "-k", PARKED_ONLY)
    assert out["outcome"] == "done" and out["count"] == 0
    assert out["counts"] == dict(dict.fromkeys(COUNTS, 0), parked=2)
    assert {e["id"]: e["reason"] for e in out["excluded"]} == {
        "only-a": "parked",
        "only-b": "parked",
    }


# -- No flag, no change -------------------------------------------------------------

#: Literal: the keys of a `task list` row on dev before #1686.
TODAYS_ROW_KEYS = {
    "id",
    "title",
    "status",
    "assignee",
    "priority",
    "parent",
    "kb_name",
    "parked_awaiting",
    "updated_at",
    "derived",
}


@pytest.mark.control(reason="pins today's listing: no flag must mean no change")
@pytest.mark.parametrize("surface", ["cli", "mcp", "rest"])
def test_without_the_flag_the_listing_is_todays(world, surface):
    if surface == "cli":
        out = cli(world, "-k", WORK)
        assert set(out) == {"count", "tasks", "outcome"}
    elif surface == "mcp":
        out = mcp(world, kb_name=WORK)
        assert set(out) == {"count", "tasks"}
    else:
        out = rest(world, kb=WORK)
        assert set(out) == {"count", "tasks"}
    assert all(set(t) == TODAYS_ROW_KEYS for t in out["tasks"])
    # Every task in the index, newest first, orphan included.
    assert out["count"] == len(WORK_FILES)
    assert out["tasks"][-1]["id"] == "lg-child"  # created 00:00, the oldest


def test_a_write_is_never_made(world):
    """A read: listing changes no file."""
    root = world.kb_path(WORK)
    before = {p.name: p.read_bytes() for p in root.glob("*.md")}
    cli(world, "--dispatchable")
    mcp(world, dispatchable=True)
    rest(world, dispatchable="true")
    assert {p.name: p.read_bytes() for p in root.glob("*.md")} == before
