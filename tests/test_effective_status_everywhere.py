"""Every surface that filters or reports a task by status uses its effective status.

Maintainer decision, 2026-10-03 ("effective status everywhere"): a task whose
children are all resolved counts as ``done`` wherever status is asked,
though its file still says ``open``. The deleted parent rollup used to write
``done`` into that file, so the generic finders answered right by accident;
without it they must ask ``derive_completion`` too (through
``pyrite/storage/effective_status.py``).

Two parts:

1. **The inventory** walks every MCP tool schema, every REST query parameter
   and every CLI option named ``status``, and fails on one this file has not
   classified. A new status filter cannot land without a line here.
2. **The matrix** runs every surface classified ``effective`` against one
   hand-authored tree, with literal expectations: ``p`` (file ``open``, both
   children resolved) is ``done``; ``q`` (file ``open``, one open child) is
   ``open``. Both are overdue by date, assigned to ``agent:x`` and contain
   the word ``zephyr``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

sys.path.insert(0, str(Path(__file__).parent))
from _surface_inventory import _walk_routes, mcp_server  # noqa: E402

KB = "work"
STATUSES = ("open", "claimed", "in_progress", "blocked", "review", "done", "failed", "cancelled")


def _task(tid, status, parent="", extra=""):
    parent_line = f"parent: {parent}\n" if parent else ""
    return (
        f"---\nid: {tid}\ntitle: Task {tid}\ntype: task\nstatus: {status}\n"
        f"{parent_line}{extra}---\n\nWork for {tid}.\n"
    )


TOP = "assignee: agent:x\ndue_date: '2020-01-01'\n"
#: The timeline's `date IS NOT NULL` must see `p` and `q` for the matrix to
#: run its `kb_timeline` surface.
TIMELINE_DATE = "date: '2024-01-15'\n"
FILES = {
    "p": _task("p", "open", extra=TOP + TIMELINE_DATE).replace(
        "Work for p.", "Work for p, zephyr."
    ),
    "p-a": _task("p-a", "done", "p"),
    "p-b": _task("p-b", "cancelled", "p"),
    "q": _task("q", "open", extra=TOP + TIMELINE_DATE).replace(
        "Work for q.", "Work for q, zephyr."
    ),
    "q-a": _task("q-a", "open", "q"),
}
QA = "task_type: qa_validation\n"
FILES.update(
    {
        # A QA task done by its children no longer asks for QA on its target.
        "qa-done": _task("qa-done", "open", extra=QA + "target_entry: note-z\n"),
        "qa-done-a": _task("qa-done-a", "done", "qa-done"),
        "qa-open": _task("qa-open", "open", extra=QA + "target_entry: note-y\n"),
    }
)
NOTES = {n: f"---\nid: {n}\ntitle: {n}\ntype: note\n---\n\nA note.\n" for n in ("note-y", "note-z")}


def _top(tid, status="open"):
    """A top-level task every surface in the matrix can find: assigned to
    `agent:x`, overdue, dated for the timeline and holding `zephyr`."""
    return _task(tid, status, extra=TOP + TIMELINE_DATE).replace(
        f"Work for {tid}.", f"Work for {tid}, zephyr."
    )


#: #1683 (maintainer, 2026-11-17, option A; ADR-0045 decision 7 as refined
#: then): a cancelled child does not count toward its parent's completion. A
#: parent is complete by its children only when at least one child is `done`
#: and every other child is `done` or `cancelled`; otherwise it keeps its own
#: status. `p` above is the mixed shape (one done, one cancelled).
FILES.update(
    {
        # all done
        "d": _top("d"),
        "d-a": _task("d-a", "done", "d"),
        "d-b": _task("d-b", "done", "d"),
        # all cancelled
        "c": _top("c"),
        "c-a": _task("c-a", "cancelled", "c"),
        "c-b": _task("c-b", "cancelled", "c"),
        # one cancelled child alone (the issue's reproduction)
        "s": _top("s"),
        "s-a": _task("s-a", "cancelled", "s"),
        # a grandparent over each of those four parent shapes
        "g-d": _top("g-d"),
        "g-d-m": _task("g-d-m", "open", "g-d"),
        "g-d-m-a": _task("g-d-m-a", "done", "g-d-m"),
        "g-d-m-b": _task("g-d-m-b", "done", "g-d-m"),
        "g-x": _top("g-x"),
        "g-x-m": _task("g-x-m", "open", "g-x"),
        "g-x-m-a": _task("g-x-m-a", "done", "g-x-m"),
        "g-x-m-b": _task("g-x-m-b", "cancelled", "g-x-m"),
        "g-c": _top("g-c"),
        "g-c-m": _task("g-c-m", "open", "g-c"),
        "g-c-m-a": _task("g-c-m-a", "cancelled", "g-c-m"),
        "g-c-m-b": _task("g-c-m-b", "cancelled", "g-c-m"),
        "g-s": _top("g-s"),
        "g-s-m": _task("g-s-m", "open", "g-s"),
        "g-s-m-a": _task("g-s-m-a", "cancelled", "g-s-m"),
        # a parent whose own status is terminal: its file decides (basis status)
        "t": _top("t", "done"),
        "t-a": _task("t-a", "cancelled", "t"),
    }
)
#: Literal, written by hand from the property: the effective status of each
#: top-level task.
EFFECTIVE = {
    "p": "done",
    "q": "open",
    "d": "done",
    "c": "open",
    "s": "open",
    "g-d": "done",
    "g-x": "done",
    "g-c": "open",
    "g-s": "open",
    "t": "done",
}
#: The tops whose answer #1683 changed: on dev each counted as `done`.
CHANGED_BY_1683 = {"c", "s", "g-c", "g-s"}


@pytest.fixture(scope="module")
def config(tmp_path_factory):
    root = tmp_path_factory.mktemp("eff")
    kb_path = root / "kb"
    (kb_path / "tasks").mkdir(parents=True)
    (kb_path / "kb.yaml").write_text("name: work\nkb_type: generic\n", encoding="utf-8")
    for tid, text in FILES.items():
        (kb_path / "tasks" / f"{tid}.md").write_text(text, encoding="utf-8")
    for nid, text in NOTES.items():
        (kb_path / f"{nid}.md").write_text(text, encoding="utf-8")
    cfg = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type=KBType.GENERIC)],
        settings=Settings(index_path=root / "index.db"),
    )
    db = PyriteDB(cfg.settings.index_path)
    try:
        IndexManager(db, cfg).index_all()
    finally:
        db.close()
    before = {p.name: p.read_bytes() for p in kb_path.rglob("*.md")}
    yield cfg
    assert {p.name: p.read_bytes() for p in kb_path.rglob("*.md")} == before


# -- Surfaces ---------------------------------------------------------------


def _mcp(cfg, tool, args):
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(config=cfg, tier="read")
    try:
        return server._dispatch_tool(tool, args)
    finally:
        server.close()


def _ids(rows):
    return {r.get("id") or r.get("entry_id") for r in rows} & set(EFFECTIVE)


def _rest_get(cfg, path, params):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from pyrite.server import api
    from pyrite.server.api import create_app

    db = PyriteDB(cfg.settings.index_path)
    try:
        application = create_app(config=cfg)
        application.dependency_overrides[api.get_db] = lambda: db
        response = TestClient(application).get(path, params=params)
        assert response.status_code == 200, response.text
        return response.json()
    finally:
        db.close()


def _cli(cfg, args):
    import pyrite.cli.search_commands  # noqa: F401  (bound before patching, #510)

    # `search` binds its own `load_config`; the other commands read context's.
    with (
        patch("pyrite.cli.context.load_config", return_value=cfg),
        patch("pyrite.cli.search_commands.load_config", return_value=cfg),
    ):
        result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def mcp_kb_find_by_status(cfg, s):
    return _ids(_mcp(cfg, "kb_find_by_status", {"status": s, "kb_name": KB})["entries"])


def mcp_kb_find_by_status_tasks(cfg, s):
    out = _mcp(cfg, "kb_find_by_status", {"status": s, "kb_name": KB, "entry_type": "task"})
    return _ids(out["entries"])


def mcp_kb_find_by_assignee(cfg, s):
    out = _mcp(cfg, "kb_find_by_assignee", {"assignee": "agent:x", "status": s, "kb_name": KB})
    return _ids(out["entries"])


def mcp_kb_search(cfg, s):
    out = _mcp(cfg, "kb_search", {"query": "zephyr", "status": s, "kb_name": KB, "mode": "keyword"})
    return _ids(out["results"])


def mcp_task_list(cfg, s):
    return _ids(_mcp(cfg, "task_list", {"status": s, "kb_name": KB})["tasks"])


def mcp_kb_timeline(cfg, s):
    """`kb_timeline` narrows the same way (#62): the filter runs in SQL
    before `LIMIT` through the effective status."""
    out = _mcp(cfg, "kb_timeline", {"status": s, "kb_names": [KB]})
    return _ids(out["events"])


def rest_entries(cfg, s):
    out = _rest_get(cfg, "/api/entries", {"kb": KB, "status": s})
    # The page and its total are the same query.
    assert out["total"] == len(out["entries"]), out
    return _ids(out["entries"])


def rest_tasks(cfg, s):
    return _ids(_rest_get(cfg, "/api/tasks", {"kb": KB, "status": s})["tasks"])


def cli_search(cfg, s):
    out = _cli(
        cfg, ["search", "zephyr", "-k", KB, "--status", s, "--mode", "keyword", "--format", "json"]
    )
    return _ids(out["results"])


def cli_task_list(cfg, s):
    return _ids(_cli(cfg, ["task", "list", "-k", KB, "--status", s, "--format", "json"])["tasks"])


def cli_qa_duplicates(cfg, s):
    # Each status fixture has a pair: this surface only returns paired entries.
    out = _cli(
        cfg,
        ["qa", "duplicates", "-t", "task", "--status", s, "--threshold", "0", "--format", "json"],
    )
    return _ids([entry for pair in out["pairs"] for entry in (pair["a"], pair["b"])])


def semantic_filter(cfg, s):
    """The filter the semantic and hybrid searches apply (no embeddings are
    needed to run it: the same WHERE fragment over the entry table)."""
    db = PyriteDB(cfg.settings.index_path)
    try:
        backend = db._backend
        frag, params, _ = backend._semantic_filter_sql(kb_name=KB, status=s)
        rows = db._raw_conn.execute(f"SELECT e.id FROM entry e WHERE 1=1{frag}", params).fetchall()
        return _ids([{"id": r[0]} for r in rows])
    finally:
        db.close()


#: surface -> (how it is reached, the function the matrix runs).
EFFECTIVE_SURFACES = {
    "mcp:kb_find_by_status": mcp_kb_find_by_status,
    "mcp:kb_find_by_status(entry_type=task)": mcp_kb_find_by_status_tasks,
    "mcp:kb_find_by_assignee": mcp_kb_find_by_assignee,
    "mcp:kb_search": mcp_kb_search,
    "mcp:kb_timeline": mcp_kb_timeline,
    "mcp:task_list": mcp_task_list,
    "rest:GET /api/entries": rest_entries,
    "rest:GET /api/tasks": rest_tasks,
    "cli:search": cli_search,
    "cli:task list": cli_task_list,
    "cli:qa duplicates": cli_qa_duplicates,
    "storage:semantic filter": semantic_filter,
}


def _status_param(status: str):
    """`open` and `done` are where the answer changed; every other status is a
    negative control: neither task may appear under it, before or after."""
    if status in ("open", "done"):
        return status
    return pytest.param(
        status, marks=pytest.mark.control(reason="neither task has this status, effective or not")
    )


@pytest.mark.parametrize("status", [_status_param(s) for s in STATUSES])
@pytest.mark.parametrize("surface", list(EFFECTIVE_SURFACES))
def test_status_filter_matches_the_effective_status(config, surface, status):
    want = {tid for tid, eff in EFFECTIVE.items() if eff == status}
    assert EFFECTIVE_SURFACES[surface](config, status) == want


@pytest.fixture(scope="module")
def listed(config):
    """One call per (surface, status), shared by the per-shape cells below."""
    memo: dict[tuple[str, str], set] = {}

    def get(surface: str, status: str) -> set:
        if (surface, status) not in memo:
            memo[surface, status] = EFFECTIVE_SURFACES[surface](config, status)
        return memo[surface, status]

    return get


def _shape_param(tid: str):
    if tid in CHANGED_BY_1683:
        return tid
    return pytest.param(
        tid, marks=pytest.mark.control(reason="this shape's answer did not change with #1683")
    )


@pytest.mark.parametrize("tid", [_shape_param(t) for t in EFFECTIVE])
@pytest.mark.parametrize("surface", list(EFFECTIVE_SURFACES))
def test_each_shape_is_listed_under_its_effective_status_only(listed, surface, tid):
    """#1683: every status filter agrees, shape by shape: a parent whose
    remaining children are all cancelled is `open`, not `done`."""
    assert {s for s in ("open", "done") if tid in listed(surface, s)} == {EFFECTIVE[tid]}


@pytest.mark.control(reason="reads never wrote a parent's file; this keeps it so (#1683)")
def test_reads_leave_every_parent_file_byte_identical(config):
    """Completion stays derived: asking every surface about every shape
    writes no file, the parents whose only children are cancelled included."""
    kb_path = config.knowledge_bases[0].path
    before = {p.name: p.read_bytes() for p in kb_path.rglob("*.md")}
    for surface in EFFECTIVE_SURFACES.values():
        for status in ("open", "done"):
            surface(config, status)
    for tid in EFFECTIVE:
        _cli(config, ["task", "get", tid, "-k", KB, "--format", "json"])
    assert {p.name: p.read_bytes() for p in kb_path.rglob("*.md")} == before


def test_overdue_skips_a_task_done_by_its_children(config):
    """kb_find_overdue reports what is not done; `p` is done."""
    out = _mcp(config, "kb_find_overdue", {"kb_name": KB, "as_of": "2026-01-01"})
    assert _ids(out["entries"]) == {"q", "c", "s", "g-c", "g-s"}


def test_qa_queue_skips_a_qa_task_done_by_its_children(config):
    from pyrite.services.task_service import TaskService

    db = PyriteDB(config.settings.index_path)
    try:
        rows = TaskService(config, db).list_entries_needing_qa(KB)
    finally:
        db.close()
    assert {r["id"] for r in rows} == {"note-y"}


def test_duplicate_scan_skips_a_task_done_by_its_children_by_default(config):
    from pyrite.services.access_policy import UNSCOPED
    from pyrite.services.qa_service import QAService

    db = PyriteDB(config.settings.index_path)
    try:
        result = QAService(config, db).find_duplicates("task", readable_kbs=UNSCOPED, threshold=0)
    finally:
        db.close()
    assert _ids([entry for pair in result["pairs"] for entry in (pair["a"], pair["b"])]) == {
        "q",
        "c",
        "s",
        "g-c",
        "g-s",
    }


# -- The inventory ----------------------------------------------------------

#: Every filter named `status`, classified. `effective`: in the matrix above.
#: `write`: sets a status, filters nothing. `never-tasks`: returns only
#: entries of types that are not `task` (software-kb's backlog items, ADRs,
#: milestones; journalism's claims), whose status is the file's. `column`:
#: keeps the file's own status spelling, in Python, over already-loaded
#: entries (export site's --exclude-status): not the effective status.
MCP_STATUS_PARAMS = {
    "kb_find_by_assignee": "effective",
    "kb_find_by_status": "effective",
    "kb_timeline": "effective",
    "investigation_claims": "never-tasks",
    "sw_transition": "write",
    "kb_search": "effective",
    "task_list": "effective",
    "kb_update": "write",
    "task_update": "write",
    "sw_create_adr": "write",
    "sw_adrs": "never-tasks",
    "sw_backlog": "never-tasks",
    "sw_epics": "never-tasks",
    "sw_milestones": "never-tasks",
    "sw_refine": "never-tasks",
}
REST_STATUS_PARAMS = {
    "GET /api/entries": "effective",
    "GET /api/tasks": "effective",
}
CLI_STATUS_OPTIONS = {
    "search": "effective",
    "task list": "effective",
    "create": "write",
    "qa duplicates": "effective",
    "task update": "write",
    "sw new-adr": "write",
    "investigation claims": "never-tasks",
    "sw adrs": "never-tasks",
    "sw backlog": "never-tasks",
    "sw epics": "never-tasks",
    "sw milestones": "never-tasks",
    "sw refine": "never-tasks",
    "export site": "column",
}


def _mcp_status_tools() -> set[str]:
    with mcp_server() as server:
        return {
            name
            for name, meta in server.tools.items()
            if any(
                key == "status" or key.endswith("_status")
                for key in (meta.get("inputSchema") or {}).get("properties", {})
            )
        }


def _rest_status_routes() -> set[str]:
    pytest.importorskip("fastapi")
    from pyrite.server.api import create_app

    found = set()
    for path, route in _walk_routes(create_app().routes):
        if any(
            p.name == "status" or p.name.endswith("_status") for p in route.dependant.query_params
        ):
            found |= {f"{m} {path}" for m in route.methods or ()}
    return found


def _cli_status_commands() -> set[str]:
    import click
    import typer.main

    def _is_status_opt(p) -> bool:
        for opt in getattr(p, "opts", None) or []:
            if not opt.startswith("--"):
                continue
            name = opt[2:].replace("-", "_")
            if name == "status" or name.endswith("_status"):
                return True
        return False

    found = set()

    def walk(cmd, path):
        if hasattr(cmd, "list_commands"):
            ctx = click.Context(cmd)
            for name in cmd.list_commands(ctx):
                walk(cmd.get_command(ctx, name), [*path, name])
        elif any(_is_status_opt(p) for p in cmd.params):
            found.add(" ".join(path))

    walk(typer.main.get_command(app), [])
    return found


#: Literal: the tasks of this tree a worker can take now (#1686). `p` says
#: `open` in its file but is done by its children (`not_open`); `q` is open
#: with an open child (`has_children`); `qa-done` is done by its child.
DISPATCHABLE = {"q-a", "qa-open"}


def _all_ids(rows):
    return {r["id"] for r in rows}


def mcp_task_list_dispatchable(cfg):
    return _all_ids(_mcp(cfg, "task_list", {"dispatchable": True, "kb_name": KB})["tasks"])


def rest_tasks_dispatchable(cfg):
    return _all_ids(_rest_get(cfg, "/api/tasks", {"kb": KB, "dispatchable": "true"})["tasks"])


def cli_task_list_dispatchable(cfg):
    out = _cli(cfg, ["task", "list", "-k", KB, "--dispatchable", "--format", "json"])
    return _all_ids(out["tasks"])


#: Every filter named `dispatchable`: one derived view (`classify_tasks`,
#: which `TaskService.task_listing` asks for every surface), filtering on the effective status.
DISPATCHABLE_SURFACES = {
    "mcp:task_list": mcp_task_list_dispatchable,
    "rest:GET /api/tasks": rest_tasks_dispatchable,
    "cli:task list": cli_task_list_dispatchable,
}


@pytest.mark.parametrize("surface", list(DISPATCHABLE_SURFACES))
def test_dispatchable_filter_matches_the_effective_status(config, surface):
    assert DISPATCHABLE_SURFACES[surface](config) == DISPATCHABLE


def _mcp_dispatchable_tools() -> set[str]:
    with mcp_server() as server:
        return {
            f"mcp:{name}"
            for name, meta in server.tools.items()
            if "dispatchable" in (meta.get("inputSchema") or {}).get("properties", {})
        }


def _rest_dispatchable_routes() -> set[str]:
    pytest.importorskip("fastapi")
    from pyrite.server.api import create_app

    found = set()
    for path, route in _walk_routes(create_app().routes):
        if any(p.name == "dispatchable" for p in route.dependant.query_params):
            found |= {f"rest:{m} {path}" for m in route.methods or ()}
    return found


def _cli_dispatchable_commands() -> set[str]:
    import click
    import typer.main

    found = set()

    def walk(cmd, path):
        if hasattr(cmd, "list_commands"):
            ctx = click.Context(cmd)
            for name in cmd.list_commands(ctx):
                walk(cmd.get_command(ctx, name), [*path, name])
        elif any("--dispatchable" in (getattr(p, "opts", None) or []) for p in cmd.params):
            found.add("cli:" + " ".join(path))

    walk(typer.main.get_command(app), [])
    return found


def test_every_dispatchable_filter_is_in_the_matrix():
    """A new `dispatchable` parameter fails here until the matrix runs it."""
    found = _mcp_dispatchable_tools() | _rest_dispatchable_routes() | _cli_dispatchable_commands()
    assert found == set(DISPATCHABLE_SURFACES)


@pytest.mark.control(reason="structural inventory: guards future status filters, not this fix")
@pytest.mark.parametrize(
    ("surface", "discover", "classified"),
    [
        ("mcp", _mcp_status_tools, MCP_STATUS_PARAMS),
        ("rest", _rest_status_routes, REST_STATUS_PARAMS),
        ("cli", _cli_status_commands, CLI_STATUS_OPTIONS),
    ],
)
def test_every_status_filter_is_classified(surface, discover, classified):
    """A new `status` parameter fails here until it is classified, and an
    `effective` one until the matrix runs it."""
    assert discover() == set(classified)
    for name, kind in classified.items():
        if kind == "effective":
            assert f"{surface}:{name}" in EFFECTIVE_SURFACES, name
