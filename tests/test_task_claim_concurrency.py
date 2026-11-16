"""Concurrency test for the task-claim CAS (task-claim-concurrency-test).

`KBService.claim_entry()`'s compare-and-swap is the one concurrency
guard the whole multi-agent fleet depends on, and until now it had
never actually been executed concurrently -- only sequential
simulation (claim, then a second claim observes the conflict). This
races N real OS processes (not threads -- agents are processes, each
with its own SQLite connection) against a single open task and
asserts exactly one winner.
"""

import multiprocessing
import subprocess
import tempfile
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.task_service import TaskService
from pyrite.storage.database import PyriteDB

N_CLAIMANTS = 8

# Wall-clock budgets. Each worker is a fresh interpreter that imports pyrite;
# under `pytest -n auto` they compete with every xdist worker for the cores, and
# a fixed 30 s per-process join once reported a merely slow worker as "crashed"
# (exitcode None). One deadline for the whole group, generous, and a distinct
# failure message for "still running" vs "exited non-zero".
_GROUP_DEADLINE = 180.0
_BARRIER_TIMEOUT = 150.0


def _join_all(processes, deadline=_GROUP_DEADLINE):
    import time

    end = time.monotonic() + deadline
    for p in processes:
        p.join(timeout=max(0.0, end - time.monotonic()))
    still_running = [p.pid for p in processes if p.exitcode is None]
    for p in processes:
        if p.exitcode is None:
            p.kill()
    assert not still_running, (
        f"{len(still_running)} worker(s) still running after {deadline:.0f}s: {still_running}"
    )
    crashed = [(p.pid, p.exitcode) for p in processes if p.exitcode != 0]
    assert not crashed, f"worker process(es) crashed instead of returning a clean result: {crashed}"


def _make_config(tmpdir: Path) -> tuple[PyriteConfig, KBConfig]:
    tasks_path = tmpdir / "tasks-kb"
    kb_config = KBConfig(
        name="test-tasks",
        path=tasks_path,
        kb_type="task",
        description="Concurrency test task KB",
    )
    config = PyriteConfig(
        knowledge_bases=[kb_config],
        # Spawned workers build Settings directly (no env override), so say it
        # here: eight interpreters each loading torch is not the race under test.
        settings=Settings(index_path=tmpdir / "index.db", auto_embed=False),
    )
    return config, kb_config


def _claim_worker(tmpdir_str: str, task_id: str, assignee: str, result_queue, barrier) -> None:
    """Run in a separate process: open a fresh DB connection and race the claim.

    The barrier makes it a race: without it, under a loaded machine (xdist
    saturating every core) spawn start-up skew turns N "concurrent" claims into
    a sequence, and the CAS is never actually contended.
    """
    tmpdir = Path(tmpdir_str)
    config, _ = _make_config(tmpdir)
    db = PyriteDB(config.settings.index_path)
    svc = TaskService(config, db)
    try:
        barrier.wait(timeout=_BARRIER_TIMEOUT)
        result = svc.claim_task(task_id, "test-tasks", assignee)
        result_queue.put((assignee, result))
    finally:
        db.close()


def _reset_worker(tmpdir_str: str, task_id: str, result_queue, barrier) -> None:
    """Run in a separate process: race a stale-claim reset against a claim."""
    tmpdir = Path(tmpdir_str)
    config, _ = _make_config(tmpdir)
    db = PyriteDB(config.settings.index_path)
    svc = TaskService(config, db)
    try:
        barrier.wait(timeout=_BARRIER_TIMEOUT)
        result = svc.reset_task(task_id, "test-tasks", reason="stale worker")
        result_queue.put(("reset", result))
    except Exception as e:
        result_queue.put(("reset", {"error": str(e)}))
    finally:
        db.close()


@pytest.fixture(params=["plain folder", "git work tree"])
def concurrency_env(request):
    """Both KB kinds: in a git work tree a file write compares and replaces
    under the momentary lock; in a plain folder it does so with no lock
    (`publish_entry_text`, D1 (b)), and the index row's compare-and-swap is
    what keeps two claimers from both winning."""
    with tempfile.TemporaryDirectory() as d:
        tmpdir = Path(d)
        config, kb_config = _make_config(tmpdir)
        kb_config.path.mkdir()
        (kb_config.path / "tasks").mkdir()
        if request.param == "git work tree":
            subprocess.run(["git", "init", "-q", str(kb_config.path)], check=True, timeout=60)

        db = PyriteDB(config.settings.index_path)
        db.register_kb(
            name="test-tasks",
            kb_type="task",
            path=str(kb_config.path),
            description="Concurrency test task KB",
        )
        svc = TaskService(config, db)
        yield {"tmpdir": tmpdir, "config": config, "kb_config": kb_config, "svc": svc, "db": db}
        db.close()


class TestClaimTaskConcurrency:
    def test_n_processes_race_claim_exactly_one_wins(self, concurrency_env):
        svc = concurrency_env["svc"]
        tmpdir = concurrency_env["tmpdir"]

        created = svc.create_task(kb_name="test-tasks", title="Race me")
        task_id = created["entry_id"]

        ctx = multiprocessing.get_context("spawn")
        result_queue = ctx.Queue()
        barrier = ctx.Barrier(N_CLAIMANTS)
        processes = [
            ctx.Process(
                target=_claim_worker,
                args=(str(tmpdir), task_id, f"agent:{i}", result_queue, barrier),
            )
            for i in range(N_CLAIMANTS)
        ]
        for p in processes:
            p.start()
        _join_all(processes)

        results = [result_queue.get(timeout=10) for _ in processes]

        winners = [(assignee, r) for assignee, r in results if r["claimed"] is True]
        losers = [(assignee, r) for assignee, r in results if r["claimed"] is False]

        assert len(winners) == 1, f"expected exactly one winner, got {winners}"
        assert len(losers) == N_CLAIMANTS - 1

        # Every loser must be a clean CONFLICT-class response, not a crash/corruption.
        for _assignee, r in losers:
            assert "error" in r
            assert r.get("current_status") == "claimed"

        # The on-disk file must match the winning claimant, not just the index.
        from pyrite.storage.repository import KBRepository

        entry = KBRepository(concurrency_env["kb_config"]).load(task_id)
        winning_assignee = winners[0][0]
        assert entry.assignee == winning_assignee
        assert entry.status == "claimed"

    def test_stale_claim_reset_races_an_active_claimer(self, concurrency_env):
        """The release/reset path (task-claim-concurrency-test's second
        acceptance criterion): a reset racing an active re-claim must not
        leave the entry in an inconsistent state -- exactly one of
        (reset-to-open, claimed-by-racer) applies to both index and file."""
        svc = concurrency_env["svc"]
        tmpdir = concurrency_env["tmpdir"]

        created = svc.create_task(kb_name="test-tasks", title="Stale claim")
        task_id = created["entry_id"]
        svc.claim_task(task_id, "test-tasks", "agent:original")
        svc.update_task(task_id, "test-tasks", status="in_progress")

        ctx = multiprocessing.get_context("spawn")
        result_queue = ctx.Queue()
        barrier = ctx.Barrier(2)
        processes = [
            ctx.Process(target=_reset_worker, args=(str(tmpdir), task_id, result_queue, barrier)),
            ctx.Process(
                target=_claim_worker,
                args=(str(tmpdir), task_id, "agent:racer", result_queue, barrier),
            ),
        ]
        for p in processes:
            p.start()
        _join_all(processes)

        results = dict(result_queue.get(timeout=10) for _ in processes)

        from pyrite.storage.repository import KBRepository

        entry = KBRepository(concurrency_env["kb_config"]).load(task_id)

        # The claim only succeeds if it raced in after the reset landed
        # (from_status="open"); if the reset hadn't landed yet, the racer's
        # claim correctly fails (task was still "in_progress", not "open").
        # Either way, index and file must agree on the final state.
        reset_result = results.get("reset")
        claim_result = results.get("agent:racer")
        context = f"file={{status={entry.status!r}, assignee={entry.assignee!r}}} reset={reset_result} claim={claim_result}"
        assert entry.status in ("open", "claimed"), context
        if entry.status == "claimed":
            assert entry.assignee == "agent:racer", context
            assert claim_result.get("claimed") is True, context
        else:
            assert reset_result.get("status") == "open" or "error" not in reset_result, context
            assert claim_result.get("claimed") is False, context
        # Index and file must agree on the final state.
        row = concurrency_env["db"].execute_sql(
            "SELECT status, assignee FROM entry WHERE id = :id", {"id": task_id}
        )[0]
        assert row["status"] == entry.status, context + f" index={dict(row)}"


def _claim_in_the_file_only(kb_config: KBConfig, task_id: str, assignee: str) -> bytes:
    """Mark the task claimed in its markdown file and leave the index row
    alone: what a `git pull` of another agent's claim, or a hand edit, does.
    Returns the file's bytes after the edit."""
    path = kb_config.path / "tasks" / f"{task_id}.md"
    text = path.read_text()
    assert "status: open\n" in text, text
    path.write_text(text.replace("status: open\n", f"status: claimed\nassignee: {assignee}\n"))
    return path.read_bytes()


def _index_row(db: PyriteDB, task_id: str) -> dict:
    rows = db.execute_sql(
        "SELECT status, assignee FROM entry WHERE id = :id AND kb_name = 'test-tasks'",
        {"id": task_id},
    )
    return dict(rows[0])


class TestClaimDecidesOnTheFile:
    """The file is the record and the index a cache of it: a claim reads the
    status and the assignee it decides on from the file, so a row that has
    fallen behind the file (a pull, a hand edit, a sync in flight) cannot hand
    a held task to a second agent."""

    def test_a_stale_open_row_does_not_let_a_second_agent_take_a_claimed_task(
        self, concurrency_env
    ):
        svc, db = concurrency_env["svc"], concurrency_env["db"]
        task_id = svc.create_task(kb_name="test-tasks", title="Held")["entry_id"]
        held = _claim_in_the_file_only(concurrency_env["kb_config"], task_id, "agent-A")
        assert _index_row(db, task_id)["status"] == "open"  # the stale row

        result = svc.claim_task(task_id, "test-tasks", "agent-B")

        assert result["claimed"] is False, result
        assert result["current_status"] == "claimed"
        assert result["error"] == f"Entry '{task_id}' is 'claimed', not 'open'"
        path = concurrency_env["kb_config"].path / "tasks" / f"{task_id}.md"
        assert path.read_bytes() == held
        # The refusal leaves the row saying what the file says.
        assert _index_row(db, task_id) == {"status": "claimed", "assignee": "agent-A"}

    def test_a_stale_claimed_row_does_not_block_a_task_the_file_says_is_open(self, concurrency_env):
        """The other direction: the claim was released in the file (pulled, or
        edited) while the row still says claimed."""
        svc, db = concurrency_env["svc"], concurrency_env["db"]
        task_id = svc.create_task(kb_name="test-tasks", title="Released")["entry_id"]
        assert svc.claim_task(task_id, "test-tasks", "agent-A")["claimed"] is True
        path = concurrency_env["kb_config"].path / "tasks" / f"{task_id}.md"
        text = path.read_text()
        assert "status: claimed\n" in text and "assignee: agent-A\n" in text, text
        path.write_text(
            text.replace("status: claimed\n", "status: open\n").replace("assignee: agent-A\n", "")
        )
        assert _index_row(db, task_id) == {"status": "claimed", "assignee": "agent-A"}

        result = svc.claim_task(task_id, "test-tasks", "agent-B")

        assert result["claimed"] is True, result
        from pyrite.storage.repository import KBRepository

        entry = KBRepository(concurrency_env["kb_config"]).load(task_id)
        assert (entry.status, entry.assignee) == ("claimed", "agent-B")
        assert _index_row(db, task_id) == {"status": "claimed", "assignee": "agent-B"}

    @pytest.mark.control(
        reason="the index row stays the mutex: this refusal is the one the "
        "compare-and-swap always gave, kept so a claimer between its swap and "
        "its file write is not overtaken"
    )
    def test_a_row_swapped_by_a_claimer_that_has_not_published_yet_still_refuses(
        self, concurrency_env
    ):
        """A claimer moves the row first and writes the file second. Between
        the two the file still says open, and the row -- built from the bytes
        the file holds, its `content_hash` says -- is that claimer's mark, not
        a stale row: a second claimer is refused and nothing is rebuilt."""
        from sqlalchemy import text

        svc, db = concurrency_env["svc"], concurrency_env["db"]
        task_id = svc.create_task(kb_name="test-tasks", title="In flight")["entry_id"]
        path = concurrency_env["kb_config"].path / "tasks" / f"{task_id}.md"
        before = path.read_bytes()
        db.session.execute(
            text("UPDATE entry SET status = 'claimed', assignee = 'agent-A' WHERE id = :id"),
            {"id": task_id},
        )
        db.session.commit()

        result = svc.claim_task(task_id, "test-tasks", "agent-B")

        assert result["claimed"] is False, result
        assert result["current_status"] == "claimed"
        assert path.read_bytes() == before
        assert _index_row(db, task_id) == {"status": "claimed", "assignee": "agent-A"}

    @pytest.mark.control(reason="a missing status was open before this change too")
    def test_a_file_with_no_status_is_open(self, concurrency_env):
        svc, db = concurrency_env["svc"], concurrency_env["db"]
        task_id = svc.create_task(kb_name="test-tasks", title="No status")["entry_id"]
        path = concurrency_env["kb_config"].path / "tasks" / f"{task_id}.md"
        text = path.read_text()
        assert "status: open\n" in text
        path.write_text(text.replace("status: open\n", ""))

        result = svc.claim_task(task_id, "test-tasks", "agent-B")

        assert result["claimed"] is True, result
        assert _index_row(db, task_id) == {"status": "claimed", "assignee": "agent-B"}

    def test_n_processes_race_a_stale_open_row_and_nobody_wins(self, concurrency_env):
        svc, db, tmpdir = (concurrency_env[k] for k in ("svc", "db", "tmpdir"))
        task_id = svc.create_task(kb_name="test-tasks", title="Held, raced")["entry_id"]
        held = _claim_in_the_file_only(concurrency_env["kb_config"], task_id, "agent-A")
        assert _index_row(db, task_id)["status"] == "open"

        ctx = multiprocessing.get_context("spawn")
        result_queue = ctx.Queue()
        barrier = ctx.Barrier(N_CLAIMANTS)
        processes = [
            ctx.Process(
                target=_claim_worker,
                args=(str(tmpdir), task_id, f"agent:{i}", result_queue, barrier),
            )
            for i in range(N_CLAIMANTS)
        ]
        for p in processes:
            p.start()
        _join_all(processes)
        results = [result_queue.get(timeout=10) for _ in processes]

        winners = [(assignee, r) for assignee, r in results if r["claimed"] is True]
        assert winners == []
        assert [r.get("current_status") for _a, r in results] == ["claimed"] * N_CLAIMANTS
        path = concurrency_env["kb_config"].path / "tasks" / f"{task_id}.md"
        assert path.read_bytes() == held
        assert _index_row(db, task_id) == {"status": "claimed", "assignee": "agent-A"}

    def test_a_file_claimed_between_the_row_swap_and_the_file_write_is_not_overwritten(
        self, concurrency_env, monkeypatch
    ):
        """The condition is checked on the bytes the write replaces, not only
        before the swap: a claim that arrives in the file (a pull) after this
        claimer won the row still wins."""
        svc, db = concurrency_env["svc"], concurrency_env["db"]
        task_id = svc.create_task(kb_name="test-tasks", title="Pulled late")["entry_id"]
        kb_svc = svc.kb_svc
        swap = kb_svc._swap_claim_row
        held: list[bytes] = []

        def swap_then_pull(*args, **kwargs):
            won = swap(*args, **kwargs)
            held.append(_claim_in_the_file_only(concurrency_env["kb_config"], task_id, "agent-A"))
            return won

        monkeypatch.setattr(kb_svc, "_swap_claim_row", swap_then_pull)

        result = svc.claim_task(task_id, "test-tasks", "agent-B")

        assert result["claimed"] is False, result
        assert result["current_status"] == "claimed"
        path = concurrency_env["kb_config"].path / "tasks" / f"{task_id}.md"
        assert path.read_bytes() == held[0]
        assert _index_row(db, task_id) == {"status": "claimed", "assignee": "agent-A"}

    def test_a_failed_file_write_leaves_the_row_saying_what_the_file_says(
        self, concurrency_env, monkeypatch
    ):
        """Not `from_status` with no assignee, which is what the swap is
        reversed to: the file here is open and carries a label assignee."""
        svc, db = concurrency_env["svc"], concurrency_env["db"]
        task_id = svc.create_task(kb_name="test-tasks", title="Labelled")["entry_id"]
        path = concurrency_env["kb_config"].path / "tasks" / f"{task_id}.md"
        path.write_text(
            path.read_text().replace("status: open\n", "status: open\nassignee: desk\n")
        )
        before = path.read_bytes()

        def disk_full(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("pyrite.services.kb_service.publish_entry_text", disk_full)

        result = svc.claim_task(task_id, "test-tasks", "agent-B")

        assert result["claimed"] is False, result
        assert "File update failed" in result["error"]
        assert path.read_bytes() == before
        assert _index_row(db, task_id) == {"status": "open", "assignee": "desk"}

    def test_the_holder_rule_reads_the_file(self, concurrency_env):
        """The file says an account holds the task; the row, behind it, names
        nobody. A caller who is not that account is refused before the row is
        touched."""
        from pyrite.exceptions import Forbidden
        from pyrite.services.acting import acting_as

        svc, db = concurrency_env["svc"], concurrency_env["db"]
        task_id = svc.create_task(kb_name="test-tasks", title="Account held")["entry_id"]
        path = concurrency_env["kb_config"].path / "tasks" / f"{task_id}.md"
        path.write_text(
            path.read_text().replace("status: open\n", "status: open\nassignee: user:alice\n")
        )
        before = path.read_bytes()
        row_before = _index_row(db, task_id)
        assert not row_before["assignee"]

        with acting_as("user:bob"), pytest.raises(Forbidden):
            svc.claim_task(task_id, "test-tasks", "user:bob")

        assert path.read_bytes() == before
        assert _index_row(db, task_id) == row_before

    def test_a_task_file_the_index_has_no_row_for_can_be_claimed(self, concurrency_env):
        """A miss in the index is a reason to ask the files (design principle
        5): a task pulled in and not yet synced is claimable, and gets its row."""
        svc, db = concurrency_env["svc"], concurrency_env["db"]
        path = concurrency_env["kb_config"].path / "tasks" / "pulled-in.md"
        path.write_text("---\nid: pulled-in\ntitle: Pulled in\ntype: task\nstatus: open\n---\n")

        result = svc.claim_task("pulled-in", "test-tasks", "agent-B")

        assert result["claimed"] is True, result
        assert "status: claimed\n" in path.read_text()
        assert _index_row(db, "pulled-in") == {"status": "claimed", "assignee": "agent-B"}


class TestIndexSyncRacingAClaim:
    """`index sync` parses the files when it plans and writes their rows
    later (`IndexManager.plan_reconcile`, then `_write_claim`). A claim that
    lands between the two is overwritten in the index by the row planned
    before it."""

    @staticmethod
    def _sync_with_a_claim_between_plan_and_write(env, monkeypatch, task_id: str) -> None:
        from pyrite.storage.index import IndexManager

        svc = env["svc"]
        path = env["kb_config"].path / "tasks" / f"{task_id}.md"
        # Changed since it was indexed, so the plan reads and parses it.
        path.write_text(path.read_text().replace("title: ", "title: Edited "))
        plan_reconcile = IndexManager.plan_reconcile

        def plan_then_claim(self, *args, **kwargs):
            plan = plan_reconcile(self, *args, **kwargs)
            assert svc.claim_task(task_id, "test-tasks", "agent-A")["claimed"] is True
            return plan

        monkeypatch.setattr(IndexManager, "plan_reconcile", plan_then_claim)
        IndexManager(env["db"], env["config"]).reconcile_kb(env["kb_config"])
        monkeypatch.setattr(IndexManager, "plan_reconcile", plan_reconcile)

    @pytest.mark.xfail(
        strict=True,
        reason="not fixed on this branch: `_write_claim` stores the entry parsed at "
        "plan time under the hash of the file as it is at write time, so the row says "
        "open, carries the claimed file's content_hash, and no later sync rewrites it",
    )
    def test_a_sync_does_not_overwrite_a_claim_made_while_it_ran(
        self, concurrency_env, monkeypatch
    ):
        svc, db = concurrency_env["svc"], concurrency_env["db"]
        task_id = svc.create_task(kb_name="test-tasks", title="Synced over")["entry_id"]

        self._sync_with_a_claim_between_plan_and_write(concurrency_env, monkeypatch, task_id)

        assert _index_row(db, task_id) == {"status": "claimed", "assignee": "agent-A"}

    def test_the_row_a_sync_left_behind_does_not_let_a_second_agent_take_the_task(
        self, concurrency_env, monkeypatch
    ):
        svc, db = concurrency_env["svc"], concurrency_env["db"]
        task_id = svc.create_task(kb_name="test-tasks", title="Synced over twice")["entry_id"]
        self._sync_with_a_claim_between_plan_and_write(concurrency_env, monkeypatch, task_id)
        path = concurrency_env["kb_config"].path / "tasks" / f"{task_id}.md"
        held = path.read_bytes()
        assert b"assignee: agent-A" in held

        result = svc.claim_task(task_id, "test-tasks", "agent-B")

        assert result["claimed"] is False, result
        assert result["current_status"] == "claimed"
        assert path.read_bytes() == held
        assert _index_row(db, task_id) == {"status": "claimed", "assignee": "agent-A"}
