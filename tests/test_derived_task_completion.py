"""Every question about a task's completion gets one answer, from one function.

ADR-0042 decision 4 and ADR-0045 decision 7 (kb/design.md principle 4): the
`_parent_rollup` after_save hook used to write `status: done` into a parent's
file when its last child resolved. It is gone. Completion is computed by
`derive_completion` and returned under `derived`, beside the file's own
`status`. Maintainer decisions, 2026-10-03:

- Every status filter matches one *effective* status. A file's own
  `done`/`cancelled` (or `failed`) wins; otherwise a task whose children are
  all resolved counts as `done`.
- Claiming a task that counts as done by its children is refused.

The matrix below asks every entry point (list filters through the service,
CLI, MCP, REST and the drain check's exact command; get; subtree; ancestors;
claim; decompose) about every tree shape, against LITERAL expectations
written by hand, not values computed by the code under test. No KB shape may
make a call fail: a 1,500-deep chain is listed, walked and claimed.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.services import task_service
from pyrite.services.task_service import TaskService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

KB = "work"
STATUSES = ("open", "claimed", "in_progress", "blocked", "review", "done", "failed", "cancelled")


def _task(tid: str, status: str, parent: str = "") -> str:
    parent_line = f"parent: {parent}\n" if parent else ""
    return (
        f"---\nid: {tid}\ntitle: Task {tid}\ntype: task\nstatus: {status}\n"
        f"{parent_line}---\n\nWork for {tid}.\n"
    )


#: The tree: id -> (file status, parent). One shape per branch under `root`.
TREE: dict[str, tuple[str, str]] = {
    "root": ("open", ""),
    # hand_closed: closed by a person while a child is still open.
    "hc": ("done", "root"),
    "hc-a": ("done", "hc"),
    "hc-b": ("open", "hc"),
    # all_resolved: every child done or cancelled, parent never set done.
    "ar": ("open", "root"),
    "ar-a": ("done", "ar"),
    "ar-b": ("cancelled", "ar"),
    # in_progress_finished: the file says in_progress, the work is finished.
    "ipf": ("in_progress", "root"),
    "ipf-a": ("done", "ipf"),
    # cascade: three levels, every leaf resolved, nothing above says done.
    "gp2": ("open", "root"),
    "mid2": ("open", "gp2"),
    "leaf2-a": ("done", "mid2"),
    "leaf2-b": ("cancelled", "mid2"),
    "side2": ("done", "gp2"),
    # open_leaf_below: three levels, one leaf still open.
    "gp": ("open", "root"),
    "mid": ("in_progress", "gp"),
    "leaf-1": ("done", "mid"),
    "leaf-2": ("open", "mid"),
    # failed_child: a failed child does not resolve its parent.
    "fp": ("open", "root"),
    "fp-a": ("done", "fp"),
    "fp-b": ("failed", "fp"),
    # failed_parent: a person failed it; its children do not override that.
    "fparent": ("failed", "root"),
    "fparent-a": ("done", "fparent"),
    # cancelled_parent: a person cancelled it with a child still open.
    "cp": ("cancelled", "root"),
    "cp-a": ("open", "cp"),
    # dangling: `parent` names no entry.
    "orphan": ("open", "no-such-entry"),
    # cycle: two tasks name each other.
    "cy-a": ("open", "cy-b"),
    "cy-b": ("open", "cy-a"),
    # non_task_parent: the parent exists and is a note.
    "under-note": ("open", "note-x"),
}
NOTE = "---\nid: note-x\ntitle: A note\ntype: note\n---\n\nContext.\n"

#: shape -> literal expectations for its target task, written by hand.
#: effective: the status every filter matches. claim: does `task claim`
#: succeed. after_decompose: effective status once an open child is added.
#: child: a child of the target, for `task_ancestors` (None: it has none).
#: (target, file, effective, complete, basis, (children, complete ones),
#:         parent_missing, claim succeeds, effective after decompose, a child)
# fmt: off
_ROWS = {
    "hand_closed"           : ("hc", "done", "done", True, "status", (2, 1), False, False, "done", "hc-a"),
    "all_resolved"          : ("ar", "open", "done", True, "children", (2, 2), False, False, "open", "ar-a"),
    "in_progress_finished"  : ("ipf", "in_progress", "done", True, "children", (1, 1), False, False, "in_progress", "ipf-a"),
    "cascade"               : ("gp2", "open", "done", True, "children", (2, 2), False, False, "open", "mid2"),
    "cascade_middle"        : ("mid2", "open", "done", True, "children", (2, 2), False, False, "open", "leaf2-a"),
    "open_leaf_below"       : ("gp", "open", "open", False, "children", (1, 0), False, True, "open", "mid"),
    "failed_child"          : ("fp", "open", "open", False, "children", (2, 1), False, True, "open", "fp-a"),
    "failed_parent"         : ("fparent", "failed", "failed", False, "status", (1, 1), False, False, "failed", "fparent-a"),
    "cancelled_parent"      : ("cp", "cancelled", "cancelled", True, "status", (1, 0), False, False, "cancelled", "cp-a"),
    "dangling"              : ("orphan", "open", "open", False, "status", (0, 0), True, True, "open", None),
    "cycle"                 : ("cy-a", "open", "open", False, "children", (1, 0), False, True, "open", "cy-b"),
    "non_task_parent"       : ("under-note", "open", "open", False, "status", (0, 0), False, True, "open", None),
}
# fmt: on
_COLS = (
    "target",
    "file",
    "effective",
    "complete",
    "basis",
    "children",
    "parent_missing",
    "claim",
    "after_decompose",
    "child",
)
SHAPES = {shape: dict(zip(_COLS, row, strict=True)) for shape, row in _ROWS.items()}

#: Shapes reachable from `root` by `task_subtree` (the dangling task and the
#: cycle hang off no root).
UNDER_ROOT = [s for s in SHAPES if s not in ("dangling", "cycle", "non_task_parent")]


def _snapshot(kb_path: Path) -> dict[str, bytes]:
    return {str(p.relative_to(kb_path)): p.read_bytes() for p in kb_path.rglob("*.md")}


def _build(root: Path, tree: dict[str, tuple[str, str]], extra: dict[str, str] | None = None):
    kb_path = root / "kb"
    (kb_path / "tasks").mkdir(parents=True)
    (kb_path / "kb.yaml").write_text("name: work\nkb_type: generic\n", encoding="utf-8")
    for tid, (status, parent) in tree.items():
        (kb_path / "tasks" / f"{tid}.md").write_text(_task(tid, status, parent), encoding="utf-8")
    for name, text in (extra or {}).items():
        (kb_path / name).write_text(text, encoding="utf-8")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type=KBType.GENERIC)],
        settings=Settings(index_path=root / "index.db"),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()
    return config, kb_path


@pytest.fixture(scope="module")
def read_tree(tmp_path_factory):
    """One tree for every read: reads must not change a byte of it."""
    config, kb_path = _build(tmp_path_factory.mktemp("read"), TREE, {"note-x.md": NOTE})
    before = _snapshot(kb_path)
    yield config
    assert _snapshot(kb_path) == before, "a read wrote a file"


@pytest.fixture
def write_tree(tmp_path):
    """A fresh tree per claim or decompose: those write what they were asked."""
    config, kb_path = _build(tmp_path, TREE, {"note-x.md": NOTE})
    return config, kb_path


# -- Entry points: list filters ---------------------------------------------


def _list_service(config, status):
    db = PyriteDB(config.settings.index_path)
    try:
        return TaskService(config, db).list_tasks(kb_name=KB, status=status)
    finally:
        db.close()


def _cli_json(config, args):
    with patch("pyrite.cli.context.load_config", return_value=config):
        result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _list_cli(config, status):
    return _cli_json(config, ["task", "list", "-k", KB, "--status", status, "-f", "json"])["tasks"]


def _drain_check(config, status):
    """The investigation conductor's drain check runs exactly
    `kb task list -k cascade-research --status open --format json`
    (tcp-skills investigation-conductor SKILL.md:68)."""
    return _cli_json(config, ["task", "list", "-k", KB, "--status", status, "--format", "json"])[
        "tasks"
    ]


def _mcp(config, tool, args, tier="read"):
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(config=config, tier=tier)
    try:
        return server._dispatch_tool(tool, args)
    finally:
        server.close()


def _list_mcp(config, status):
    return _mcp(config, "task_list", {"kb_name": KB, "status": status})["tasks"]


def _rest(config, method, path, **kw):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from pyrite.server import api
    from pyrite.server.api import create_app

    db = PyriteDB(config.settings.index_path)
    try:
        application = create_app(config=config)
        application.dependency_overrides[api.get_db] = lambda: db
        response = getattr(TestClient(application), method)(path, **kw)
        assert response.status_code == 200, response.text
        return response.json()
    finally:
        db.close()


def _list_rest(config, status):
    return _rest(config, "get", "/api/tasks", params={"kb": KB, "status": status})["tasks"]


LISTS = {
    "service": _list_service,
    "cli": _list_cli,
    "drain_check": _drain_check,
    "mcp": _list_mcp,
    "rest": _list_rest,
}


@pytest.fixture(scope="module")
def listings(read_tree):
    """(entry point, status) -> {id: row}, each listed once per module."""
    cache: dict[tuple[str, str], dict[str, dict]] = {}

    def get(ep: str, status: str) -> dict[str, dict]:
        if (ep, status) not in cache:
            cache[(ep, status)] = {r["id"]: r for r in LISTS[ep](read_tree, status)}
        return cache[(ep, status)]

    return get


@pytest.mark.parametrize("shape", list(SHAPES))
@pytest.mark.parametrize("ep", list(LISTS))
def test_every_status_filter_matches_the_effective_status(listings, ep, shape):
    want = SHAPES[shape]
    listed_under = {s for s in STATUSES if want["target"] in listings(ep, s)}
    assert listed_under == {want["effective"]}
    row = listings(ep, want["effective"])[want["target"]]
    assert row["status"] == want["file"]
    assert row["derived"]["effective_status"] == want["effective"]
    c = row["derived"]["completion"]
    assert (c["complete"], c["basis"]) == (want["complete"], want["basis"])
    assert (c["children"], c["children_complete"]) == want["children"]
    assert row["derived"]["parent_missing"] is want["parent_missing"]


# -- Entry points: single reads ---------------------------------------------


def _get_cli(config, tid):
    return _cli_json(config, ["task", "get", tid, "-k", KB, "-f", "json"])


def _get_mcp(config, tid):
    return _mcp(config, "task_status", {"task_id": tid, "kb_name": KB})


def _subtree_service(config, tid):
    db = PyriteDB(config.settings.index_path)
    try:
        nodes = TaskService(config, db).get_subtree("root", KB)
    finally:
        db.close()
    return {n["id"]: n for n in nodes}[tid]


def _subtree_mcp(config, tid):
    nodes = _mcp(config, "task_subtree", {"task_id": "root", "kb_name": KB})["subtree"]
    return {n["id"]: n for n in nodes}[tid]


def _ancestors_mcp(config, tid, child):
    nodes = _mcp(config, "task_ancestors", {"task_id": child, "kb_name": KB})["ancestors"]
    return {n["id"]: n for n in nodes}[tid]


def _check_derived(node, want):
    d = node["derived"]
    assert d["effective_status"] == want["effective"]
    assert (d["completion"]["complete"], d["completion"]["basis"]) == (
        want["complete"],
        want["basis"],
    )
    assert (d["completion"]["children"], d["completion"]["children_complete"]) == want["children"]
    assert d["parent_missing"] is want["parent_missing"]


@pytest.mark.parametrize("shape", list(SHAPES))
@pytest.mark.parametrize("ep", ["cli_get", "mcp_task_status"])
def test_get_shows_the_same_derived_value(read_tree, ep, shape):
    want = SHAPES[shape]
    node = (_get_cli if ep == "cli_get" else _get_mcp)(read_tree, want["target"])
    assert node["status"] == want["file"]
    _check_derived(node, want)


@pytest.mark.parametrize("shape", UNDER_ROOT)
@pytest.mark.parametrize("ep", ["service", "mcp"])
def test_subtree_shows_the_same_derived_value(read_tree, ep, shape):
    want = SHAPES[shape]
    node = (_subtree_service if ep == "service" else _subtree_mcp)(read_tree, want["target"])
    _check_derived(node, want)


@pytest.mark.parametrize("shape", [s for s in SHAPES if SHAPES[s]["child"]])
def test_ancestors_show_the_same_derived_value(read_tree, shape):
    want = SHAPES[shape]
    _check_derived(_ancestors_mcp(read_tree, want["target"], want["child"]), want)


# -- Entry points: claim ----------------------------------------------------


def _claim_service(config, tid):
    db = PyriteDB(config.settings.index_path)
    try:
        return TaskService(config, db).claim_task(tid, KB, "agent:t")
    finally:
        db.close()


def _claim_cli(config, tid):
    with patch("pyrite.cli.context.load_config", return_value=config):
        result = CliRunner().invoke(
            app, ["task", "claim", tid, "-k", KB, "-a", "agent:t", "-f", "json"]
        )
    return json.loads(result.stdout)


def _claim_mcp(config, tid):
    return _mcp(
        config, "task_claim", {"task_id": tid, "kb_name": KB, "assignee": "agent:t"}, "write"
    )


def _claim_rest(config, tid):
    return _rest(
        config, "post", f"/api/tasks/{tid}/claim", params={"kb": KB}, json={"assignee": "agent:t"}
    )


CLAIMS = {"service": _claim_service, "cli": _claim_cli, "mcp": _claim_mcp, "rest": _claim_rest}


def _claim_shape(shape: str):
    """Only a task complete by its children changes answer with this PR; the
    other shapes are negative controls the claim must keep answering."""
    want = SHAPES[shape]
    if want["claim"] is False and want["basis"] == "children":
        return shape
    return pytest.param(
        shape,
        marks=pytest.mark.control(
            reason="the file's own status decides this claim; it must not change"
        ),
    )


@pytest.mark.parametrize("shape", [_claim_shape(s) for s in SHAPES])
@pytest.mark.parametrize("ep", list(CLAIMS))
def test_claim_gets_the_same_answer(write_tree, ep, shape):
    config, kb_path = write_tree
    want = SHAPES[shape]
    path = kb_path / "tasks" / f"{want['target']}.md"
    before = path.read_bytes()

    out = CLAIMS[ep](config, want["target"])

    assert out["claimed"] is want["claim"], out
    if not want["claim"]:
        assert path.read_bytes() == before
    if want["claim"] is False and want["basis"] == "children":
        # Refused for its children, not for its file's status, and says so.
        assert "children are resolved" in out["error"]
        assert out["derived"]["effective_status"] == "done"


# -- Entry points: decompose ------------------------------------------------


def _decompose_cli(config, tid):
    return _cli_json(config, ["task", "decompose", tid, "-k", KB, "-c", "One more", "-f", "json"])


def _decompose_mcp(config, tid):
    return _mcp(
        config,
        "task_decompose",
        {"parent_id": tid, "kb_name": KB, "children": [{"title": "One more"}]},
        "write",
    )


@pytest.mark.parametrize("shape", list(SHAPES))
@pytest.mark.parametrize("ep", ["cli", "mcp"])
def test_decompose_reports_the_parent_effective_status(write_tree, ep, shape):
    config, kb_path = write_tree
    want = SHAPES[shape]
    path = kb_path / "tasks" / f"{want['target']}.md"
    before = path.read_bytes()

    out = (_decompose_cli if ep == "cli" else _decompose_mcp)(config, want["target"])

    assert out["decomposed"] is True
    assert out["parent_derived"]["effective_status"] == want["after_decompose"]
    assert path.read_bytes() == before, "decompose wrote the parent's file"


# -- The one function, and shapes no call may fail on -----------------------


def test_derive_completion_is_pure_over_rows():
    rows = [{"id": tid, "status": s, "parent": p} for tid, (s, p) in TREE.items()]
    rows.append({"id": "note-x", "status": None, "parent": "", "entry_type": "note"})
    got = task_service.derive_completion(rows)
    for want in SHAPES.values():
        _check_derived({"derived": got[want["target"]]}, want)


def test_a_child_added_reopens_a_derived_parent():
    """Completion follows the children; nothing was written to undo."""
    rows = [
        {"id": "p", "status": "open", "parent": ""},
        {"id": "c1", "status": "done", "parent": "p"},
    ]
    assert task_service.derive_completion(rows)["p"]["effective_status"] == "done"
    rows.append({"id": "c2", "status": "open", "parent": "p"})
    assert task_service.derive_completion(rows)["p"]["effective_status"] == "open"


DEPTH = 1500


def test_a_deep_chain_fails_no_call(tmp_path):
    """A 1,500-deep parent chain once raised RecursionError inside
    derive_completion and failed `task list` for the whole KB."""
    chain = {f"n{i}": ("open", f"n{i - 1}" if i else "") for i in range(DEPTH)}
    chain[f"n{DEPTH - 1}"] = ("done", f"n{DEPTH - 2}")
    config, _ = _build(tmp_path, chain)

    done = {r["id"] for r in _list_cli(config, "done")}
    assert done == set(chain), "every link of a finished chain counts as done"
    assert _list_cli(config, "open") == []
    nodes = _mcp(config, "task_subtree", {"task_id": "n0", "kb_name": KB})["subtree"]
    assert len(nodes) == DEPTH - 1
    assert _claim_service(config, "n0")["claimed"] is False


def test_mcp_task_status_reads_priority_as_list_tasks_does(tmp_path):
    """An index row written before TaskEntry coerced words still holds
    a word; task_status gives the model's reading, as list_tasks does (#554).
    `high` reads as 7, not the default 5."""
    config, _ = _build(tmp_path, {"t": ("open", "")})
    db = PyriteDB(config.settings.index_path)
    try:
        db._raw_conn.execute("UPDATE entry SET priority = 'high' WHERE id = 't'")
        db._raw_conn.commit()
        listed = TaskService(config, db).list_tasks(kb_name=KB)[0]["priority"]
    finally:
        db.close()
    out = _mcp(config, "task_status", {"task_id": "t", "kb_name": KB})
    assert out["priority"] == listed == 7
