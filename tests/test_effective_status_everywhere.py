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
FILES = {
    "p": _task("p", "open", extra=TOP).replace("Work for p.", "Work for p, zephyr."),
    "p-a": _task("p-a", "done", "p"),
    "p-b": _task("p-b", "cancelled", "p"),
    "q": _task("q", "open", extra=TOP).replace("Work for q.", "Work for q, zephyr."),
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
#: Literal: the effective status of each top-level task.
EFFECTIVE = {"p": "done", "q": "open"}


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
    "mcp:task_list": mcp_task_list,
    "rest:GET /api/entries": rest_entries,
    "rest:GET /api/tasks": rest_tasks,
    "cli:search": cli_search,
    "cli:task list": cli_task_list,
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


def test_overdue_skips_a_task_done_by_its_children(config):
    """kb_find_overdue reports what is not done; `p` is done."""
    out = _mcp(config, "kb_find_overdue", {"kb_name": KB, "as_of": "2026-01-01"})
    assert _ids(out["entries"]) == {"q"}


def test_qa_queue_skips_a_qa_task_done_by_its_children(config):
    from pyrite.services.task_service import TaskService

    db = PyriteDB(config.settings.index_path)
    try:
        rows = TaskService(config, db).list_entries_needing_qa(KB)
    finally:
        db.close()
    assert {r["id"] for r in rows} == {"note-y"}


# -- The inventory ----------------------------------------------------------

#: Every filter named `status`, classified. `effective`: in the matrix above.
#: `write`: sets a status, filters nothing. `never-tasks`: returns only
#: entries of types that are not `task` (software-kb's backlog items, ADRs,
#: milestones; journalism's claims), whose status is the file's.
MCP_STATUS_PARAMS = {
    "kb_find_by_assignee": "effective",
    "kb_find_by_status": "effective",
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
    "task update": "write",
    "sw new-adr": "write",
    "investigation claims": "never-tasks",
    "sw adrs": "never-tasks",
    "sw backlog": "never-tasks",
    "sw epics": "never-tasks",
    "sw milestones": "never-tasks",
    "sw refine": "never-tasks",
}


def _mcp_status_tools() -> set[str]:
    with mcp_server() as server:
        return {
            name
            for name, meta in server.tools.items()
            if "status" in (meta.get("inputSchema") or {}).get("properties", {})
        }


def _rest_status_routes() -> set[str]:
    pytest.importorskip("fastapi")
    from pyrite.server.api import create_app

    found = set()
    for path, route in _walk_routes(create_app().routes):
        if any(p.name == "status" for p in route.dependant.query_params):
            found |= {f"{m} {path}" for m in route.methods or ()}
    return found


def _cli_status_commands() -> set[str]:
    import click
    import typer.main

    found = set()

    def walk(cmd, path):
        if hasattr(cmd, "list_commands"):
            ctx = click.Context(cmd)
            for name in cmd.list_commands(ctx):
                walk(cmd.get_command(ctx, name), [*path, name])
        elif any("--status" in (getattr(p, "opts", None) or []) for p in cmd.params):
            found.add(" ".join(path))

    walk(typer.main.get_command(app), [])
    return found


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
