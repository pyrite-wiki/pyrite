"""`task start`: claim and start an open task in one write (#1685).

The property: `task start` takes an open task and leaves it `in_progress`
with the caller as assignee in ONE file write, decided by the same
compare-and-set as `task claim` (`KBService.claim_entry`). Of concurrent
starts exactly one wins; a loser changes nothing and is reported as a lost
claim is (ADR-0046 `nothing`, exit 1, `claimed: false`, `current_status`).
The file records the two edges it passed through as two `status_change_log`
items (open -> claimed, claimed -> in_progress), each judged by the task
workflow; the transition table does not change.

Expected values are written by hand from that property. Every surface is the
production one: the CLI through `world.cli`, MCP through the dispatcher,
REST through `create_app`'s own client (`tests._world`).
"""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest
import yaml

from pyrite.config import KBConfig, PyriteConfig, Settings
from tests._world import build_world

KB = "work"
WHO = "agent:a"


def _task(entry_id: str, extra: str = "", body: str = "Do the thing.\n") -> str:
    return f"---\ntype: task\nid: {entry_id}\ntitle: Task {entry_id}\n{extra}---\n\n{body}"


@pytest.fixture
def world(tmp_path):
    w = build_world(tmp_path, kbs=[KBConfig(name=KB, path=tmp_path / "kb", kb_type="generic")])
    with w.pointed():
        yield w
    w.close()


def _front(path: Path) -> dict:
    return yaml.safe_load(path.read_text().split("---", 2)[1])


def _edges(front: dict) -> list[tuple[str, str, str]]:
    return [(i["from"], i["to"], i["by"]) for i in front.get("status_change_log") or []]


def _row(world, entry_id: str) -> dict:
    rows = world.db.execute_sql(
        "SELECT status, assignee FROM entry WHERE id = :id AND kb_name = :kb",
        {"id": entry_id, "kb": KB},
    )
    return dict(rows[0])


def _cli_start(world, entry_id: str, who: str = WHO):
    result = world.cli("task", "start", entry_id, "-k", KB, "-a", who, "--format", "json")
    data = json.loads(result.stdout)
    return result.exit_code, data


# -- start-from shapes ----------------------------------------------------------


@pytest.mark.parametrize("status_line", ["status: open\n", ""], ids=["open", "unset"])
def test_start_moves_an_open_task_to_in_progress_in_one_write(world, status_line):
    path = world.add_entry(KB, _task("t", status_line))

    code, data = _cli_start(world, "t")

    assert code == 0, data
    assert data["claimed"] is True
    assert data["status"] == "in_progress"
    assert data["assignee"] == WHO
    front = _front(path)
    assert front["status"] == "in_progress"
    assert front["assignee"] == WHO
    assert _edges(front) == [("open", "claimed", WHO), ("claimed", "in_progress", WHO)]
    for item in front["status_change_log"]:
        assert isinstance(item["date"], str) and item["date"].endswith("Z")
    assert _row(world, "t") == {"status": "in_progress", "assignee": WHO}


def test_start_returns_the_task_body_and_its_parents(world):
    world.add_entry(KB, _task("p", "status: open\n", body="Parent context.\n"))
    world.add_entry(KB, _task("c", "status: open\nparent: p\n", body="Child work.\n"))

    code, data = _cli_start(world, "c")

    assert code == 0, data
    assert data["task"]["id"] == "c"
    assert data["task"]["body"].strip() == "Child work."
    assert data["parent"] == {"id": "p", "title": "Task p", "body": "Parent context."}


def test_start_without_a_parent_reports_none(world):
    world.add_entry(KB, _task("t", "status: open\n"))
    code, data = _cli_start(world, "t")
    assert code == 0, data
    assert data["parent"] is None


def test_start_of_a_task_the_same_assignee_claimed_moves_it_to_in_progress(world):
    """A worker that died between `claim` and `start` recovers by retrying start."""
    path = world.add_entry(KB, _task("t", f"status: claimed\nassignee: {WHO}\n"))

    code, data = _cli_start(world, "t")

    assert code == 0, data
    assert data["claimed"] is True and data["status"] == "in_progress"
    front = _front(path)
    assert front["status"] == "in_progress"
    assert _edges(front) == [("claimed", "in_progress", WHO)]


@pytest.mark.parametrize(
    ("extra", "current"),
    [
        ("status: claimed\nassignee: agent:other\n", "claimed"),
        ("status: in_progress\nassignee: agent:other\n", "in_progress"),
        ("status: in_progress\nassignee: agent:a\n", "in_progress"),
        ("status: done\n", "done"),
        ("status: cancelled\n", "cancelled"),
    ],
    ids=["claimed-by-other", "in-progress-other", "in-progress-self", "done", "cancelled"],
)
def test_start_of_a_task_that_is_not_open_is_a_lost_claim_and_changes_nothing(
    world, extra, current
):
    path = world.add_entry(KB, _task("t", extra))
    before = path.read_bytes()
    row_before = _row(world, "t")

    code, data = _cli_start(world, "t")

    assert code == 1, data
    assert data["claimed"] is False
    assert data["current_status"] == current
    assert data["outcome"] == "nothing"
    assert path.read_bytes() == before
    assert _row(world, "t") == row_before


def test_start_of_a_parent_complete_by_its_children_is_refused(world):
    path = world.add_entry(KB, _task("p", "status: open\n"))
    world.add_entry(KB, _task("c1", "status: done\nparent: p\n"))
    before = path.read_bytes()

    code, data = _cli_start(world, "p")

    assert code == 1, data
    assert data["claimed"] is False
    assert data["current_status"] == "done"
    assert path.read_bytes() == before


def test_start_when_the_file_is_no_longer_open_is_refused_and_the_index_restored(world):
    """The CAS runs on the index row; the file decides (ADR-0042 decision 10).

    The file was hand-edited to `done` and not re-indexed, so the row still
    says `open` and the CAS wins. The one write rechecks the status the file
    holds through the workflow and refuses; claim's rollback restores the row.
    """
    path = world.add_entry(KB, _task("t", "status: open\n"))
    path.write_text(_task("t", "status: done\n"))
    before = path.read_bytes()

    code, data = _cli_start(world, "t")

    assert code == 1, data
    assert data["claimed"] is False
    assert path.read_bytes() == before
    assert _row(world, "t")["status"] == "open"
    assert not _row(world, "t")["assignee"]


@pytest.mark.control(
    reason="pins dev's failure path for a plain claim: a refused file write clears the "
    "holder in the index row; holds before and after #1685"
)
def test_a_plain_claim_whose_file_write_is_refused_leaves_the_row_as_dev_does(world):
    """`task claim`'s rollback is unchanged by `start`: status back to the
    claim's `from`, and no holder, even where the row held one before."""
    from pyrite.services.task_service import TaskService

    path = world.add_entry(KB, _task("t", "status: open\nassignee: agent:old\n"))
    path.write_text(_task("t", "status: done\nassignee: agent:old\n"))
    before = path.read_bytes()

    result = TaskService(world.config, world.db).claim_task("t", KB, "agent:new")

    assert result["claimed"] is False
    assert path.read_bytes() == before
    assert _row(world, "t") == {"status": "open", "assignee": None}


def test_a_start_retry_whose_file_write_is_refused_keeps_its_holder_in_the_row(world):
    """`start`'s own rollback: a retry from `claimed` (the same assignee)
    whose file write is refused puts the holder back, so the next retry still
    finds it; a cleared holder would make that retry lose until a sync."""
    path = world.add_entry(KB, _task("t", f"status: claimed\nassignee: {WHO}\n"))
    path.write_text(_task("t", f"status: done\nassignee: {WHO}\n"))
    before = path.read_bytes()

    code, data = _cli_start(world, "t")

    assert code == 1, data
    assert data["claimed"] is False
    assert path.read_bytes() == before
    assert _row(world, "t") == {"status": "claimed", "assignee": WHO}


def test_start_refuses_a_file_its_type_cannot_load_before_the_compare_and_set(world):
    path = world.add_entry(KB, _task("t", "status: open\n"))
    row_before = _row(world, "t")
    # A title that is a list: the task loader refuses it (ADR-0042 8a).
    path.write_text(path.read_text().replace("title: Task t", "title: [a, b]"))
    before = path.read_bytes()

    result = world.cli("task", "start", "t", "-k", KB, "-a", WHO, "--format", "json")

    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["error_code"] == "ENTRY_UNREADABLE"
    assert path.read_bytes() == before
    assert _row(world, "t") == row_before


def test_start_under_a_kb_strict_state_machine_walks_its_declared_edges(world):
    (world.kb_path(KB) / "kb.yaml").write_text(
        "name: work\nkb_type: generic\ntypes:\n  task:\n    state_machine:\n"
        "      states: [open, claimed, in_progress, done]\n"
        "      transitions:\n"
        "        - {from: open, to: claimed, requires: write}\n"
        "        - {from: claimed, to: in_progress, requires: write}\n"
        "        - {from: in_progress, to: done, requires: write}\n"
    )
    path = world.add_entry(KB, _task("t", "status: open\n"))

    code, data = _cli_start(world, "t")

    assert code == 0, data
    assert _edges(_front(path)) == [("open", "claimed", WHO), ("claimed", "in_progress", WHO)]


def test_start_under_a_state_machine_without_claimed_is_refused_and_rolled_back(world):
    (world.kb_path(KB) / "kb.yaml").write_text(
        "name: work\nkb_type: generic\ntypes:\n  task:\n    state_machine:\n"
        "      states: [open, in_progress, done]\n"
        "      transitions:\n"
        "        - {from: open, to: in_progress, requires: write}\n"
        "        - {from: in_progress, to: done, requires: write}\n"
    )
    path = world.add_entry(KB, _task("t", "status: open\n"))
    before = path.read_bytes()

    code, data = _cli_start(world, "t")

    assert code == 1, data
    assert data["claimed"] is False
    assert path.read_bytes() == before
    assert _row(world, "t")["status"] == "open"


# -- one shape on every surface -------------------------------------------------


def _mcp_start(world, entry_id: str, who: str = WHO) -> dict:
    server = world.mcp("write")
    return server._dispatch_tool(
        "task_start", {"task_id": entry_id, "kb_name": KB, "assignee": who}
    )


def _rest_start(world, entry_id: str, who: str = WHO) -> dict:
    response = world.client.post(
        f"/api/tasks/{entry_id}/start", params={"kb": KB}, json={"assignee": who}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _shape(data: dict) -> dict:
    """The result with what differs by task id and clock taken out."""
    data = {k: v for k, v in data.items() if k != "outcome"}
    task = dict(data.pop("task", None) or {})
    return {
        **{k: v for k, v in data.items() if k != "task_id"},
        "task_keys": sorted(task),
        "task_status": task.get("status"),
        "task_body": task.get("body"),
    }


def test_cli_mcp_and_rest_start_return_one_shape(world):
    for entry_id in ("c", "m", "r"):
        world.add_entry(KB, _task(entry_id, "status: open\n"))

    _code, cli = _cli_start(world, "c")
    mcp = _mcp_start(world, "m")
    rest = _rest_start(world, "r")

    assert cli["task_id"] == "c" and mcp["task_id"] == "m" and rest["task_id"] == "r"
    assert _shape(cli) == _shape(mcp) == _shape(rest)
    assert _shape(cli)["task_status"] == "in_progress"


def test_cli_mcp_and_rest_report_a_lost_start_alike(world):
    world.add_entry(KB, _task("t", "status: in_progress\nassignee: agent:other\n"))
    _code, cli = _cli_start(world, "t")
    mcp = _mcp_start(world, "t")
    rest = _rest_start(world, "t")
    cli.pop("outcome")
    assert cli == mcp == rest
    assert cli["claimed"] is False and cli["current_status"] == "in_progress"


# -- the race -------------------------------------------------------------------

N_STARTERS = 6
_GROUP_DEADLINE = 180.0
_BARRIER_TIMEOUT = 150.0


def _race_config(tmpdir: Path) -> PyriteConfig:
    return PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=tmpdir / "kb", kb_type="generic")],
        settings=Settings(index_path=tmpdir / "index.db", auto_embed=False),
    )


def _start_worker(tmpdir_str: str, assignee: str, result_queue, barrier) -> None:
    """A separate process with its own connection, released by the barrier."""
    from pyrite.services.task_service import TaskService
    from pyrite.storage.database import PyriteDB

    config = _race_config(Path(tmpdir_str))
    db = PyriteDB(config.settings.index_path)
    try:
        svc = TaskService(config, db)
        barrier.wait(timeout=_BARRIER_TIMEOUT)
        result = svc.start_task("t", KB, assignee)
        result_queue.put((assignee, {k: result.get(k) for k in ("claimed", "current_status")}))
    finally:
        db.close()


def _race_worker(tmpdir_str: str, verb: str, assignee: str, result_queue, barrier) -> None:
    """`start` or `claim` in a separate process, released by the barrier."""
    from pyrite.services.task_service import TaskService
    from pyrite.storage.database import PyriteDB

    config = _race_config(Path(tmpdir_str))
    db = PyriteDB(config.settings.index_path)
    try:
        svc = TaskService(config, db)
        barrier.wait(timeout=_BARRIER_TIMEOUT)
        call = svc.start_task if verb == "start" else svc.claim_task
        result = call("t", KB, assignee)
        result_queue.put(
            (verb, assignee, {k: result.get(k) for k in ("claimed", "current_status")})
        )
    finally:
        db.close()


def _seed_race(tmp_path: Path) -> Path:
    from pyrite.services.task_service import TaskService
    from pyrite.storage.database import PyriteDB

    config = _race_config(tmp_path)
    (tmp_path / "kb").mkdir()
    path = tmp_path / "kb" / "t.md"
    path.write_text(_task("t", "status: open\n"))
    db = PyriteDB(config.settings.index_path)
    db.register_kb(name=KB, kb_type="generic", path=str(tmp_path / "kb"), description="")
    TaskService(config, db).kb_svc.index_kb(KB)
    db.close()
    return path


def _run_all(procs) -> None:
    import time

    for p in procs:
        p.start()
    end = time.monotonic() + _GROUP_DEADLINE
    for p in procs:
        p.join(timeout=max(0.0, end - time.monotonic()))
    for p in procs:
        if p.exitcode is None:
            p.kill()
    assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]


def test_starts_racing_claims_exactly_one_wins_and_the_file_says_which(tmp_path):
    """`start` and `claim` are decided by one compare-and-set, so of starts
    and claims racing for one open task exactly one wins, and the file is the
    winner's: in_progress with both edges for a start, claimed with no log
    item for a claim (a claim writes none; #1680 item 5)."""
    path = _seed_race(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    barrier = ctx.Barrier(N_STARTERS)
    verbs = ["start" if i % 2 == 0 else "claim" for i in range(N_STARTERS)]
    procs = [
        ctx.Process(target=_race_worker, args=(str(tmp_path), verb, f"agent:{i}", queue, barrier))
        for i, verb in enumerate(verbs)
    ]
    _run_all(procs)
    results = [queue.get(timeout=10) for _ in procs]

    winners = [(verb, who) for verb, who, r in results if r["claimed"] is True]
    assert len(winners) == 1, results
    verb, who = winners[0]
    front = _front(path)
    assert front["assignee"] == who
    if verb == "start":
        assert front["status"] == "in_progress"
        assert _edges(front) == [("open", "claimed", who), ("claimed", "in_progress", who)]
        expected_loss = "in_progress"
    else:
        assert front["status"] == "claimed"
        assert _edges(front) == []
        expected_loss = "claimed"
    for _verb, _who, r in results:
        if r["claimed"] is False:
            assert r["current_status"] == expected_loss, results


def test_concurrent_starts_exactly_one_wins_and_the_file_records_both_edges(tmp_path):
    from pyrite.services.task_service import TaskService
    from pyrite.storage.database import PyriteDB

    config = _race_config(tmp_path)
    (tmp_path / "kb").mkdir()
    path = tmp_path / "kb" / "t.md"
    path.write_text(_task("t", "status: open\n"))
    db = PyriteDB(config.settings.index_path)
    db.register_kb(name=KB, kb_type="generic", path=str(tmp_path / "kb"), description="")
    TaskService(config, db).kb_svc.index_kb(KB)
    db.close()

    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    barrier = ctx.Barrier(N_STARTERS)
    procs = [
        ctx.Process(target=_start_worker, args=(str(tmp_path), f"agent:{i}", queue, barrier))
        for i in range(N_STARTERS)
    ]
    for p in procs:
        p.start()
    import time

    end = time.monotonic() + _GROUP_DEADLINE
    for p in procs:
        p.join(timeout=max(0.0, end - time.monotonic()))
    for p in procs:
        if p.exitcode is None:
            p.kill()
    assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]
    results = [queue.get(timeout=10) for _ in procs]

    winners = [who for who, r in results if r["claimed"] is True]
    losers = [r for _who, r in results if r["claimed"] is False]
    assert len(winners) == 1, results
    assert len(losers) == N_STARTERS - 1
    assert all(r["current_status"] == "in_progress" for r in losers), losers
    front = _front(path)
    assert front["status"] == "in_progress"
    assert front["assignee"] == winners[0]
    assert _edges(front) == [
        ("open", "claimed", winners[0]),
        ("claimed", "in_progress", winners[0]),
    ]
