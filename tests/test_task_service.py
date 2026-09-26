"""Tests for TaskService — operative task operations."""

import uuid
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.task_service import TaskService
from pyrite.storage.database import PyriteDB
from pyrite.storage.repository import KBRepository
from pyrite.services.access_policy import UNSCOPED


@pytest.fixture(scope="class")
def task_env():
    """Create a temp KB environment for task tests (class-scoped for speed)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tasks_path = tmpdir / "tasks-kb"
        tasks_path.mkdir()
        (tasks_path / "tasks").mkdir()

        kb_config = KBConfig(
            name="test-tasks",
            path=tasks_path,
            kb_type="task",
            description="Test task KB",
        )
        config = PyriteConfig(
            knowledge_bases=[kb_config],
            settings=Settings(index_path=tmpdir / "index.db"),
        )
        db = PyriteDB(config.settings.index_path)
        db.register_kb(
            name="test-tasks",
            kb_type="task",
            path=str(tasks_path),
            description="Test task KB",
        )

        svc = TaskService(config, db)
        yield {
            "svc": svc,
            "config": config,
            "db": db,
            "kb_config": kb_config,
            "tasks_path": tasks_path,
        }
        db.close()


class TestCreateTask:
    def test_create_task(self, task_env):
        svc = task_env["svc"]
        result = svc.create_task(kb_name="test-tasks", title="Investigate target")
        assert result["created"] is True
        assert result["entry_id"]
        assert result["title"] == "Investigate target"
        assert result["status"] == "open"
        assert result["priority"] == 5

    def test_create_task_with_options(self, task_env):
        svc = task_env["svc"]
        result = svc.create_task(
            kb_name="test-tasks",
            title="Sub task",
            body="Do the thing",
            parent="parent-123",
            priority=3,
            assignee="agent:test",
            dependencies=["dep-1"],
        )
        assert result["created"] is True
        assert result["parent"] == "parent-123"
        assert result["assignee"] == "agent:test"
        assert result["priority"] == 3

    def test_create_task_with_tags(self, task_env):
        svc = task_env["svc"]
        result = svc.create_task(
            kb_name="test-tasks",
            title="Tagged task",
            tags=["qa", "auto-generated"],
        )
        assert result["created"] is True

    def test_create_indexes_entry(self, task_env):
        svc = task_env["svc"]
        result = svc.create_task(kb_name="test-tasks", title="Indexed task")
        entry_id = result["entry_id"]

        row = (
            task_env["db"]
            ._raw_conn.execute(
                "SELECT * FROM entry WHERE id = ? AND kb_name = ?",
                (entry_id, "test-tasks"),
            )
            .fetchone()
        )
        assert row is not None
        assert row["title"] == "Indexed task"


class TestUpdateTask:
    def test_update_task(self, task_env):
        svc = task_env["svc"]
        created = svc.create_task(kb_name="test-tasks", title="Update me")
        entry_id = created["entry_id"]

        result = svc.update_task(entry_id, "test-tasks", status="claimed", assignee="agent:a")
        assert result["updated"] is True
        assert result["status"] == "claimed"
        assert result["assignee"] == "agent:a"
        assert result["updates"] == {"status": "claimed", "assignee": "agent:a"}

    def test_illegal_transition_raises_helpful_validation_error(self, task_env):
        """Jumping a never-claimed task straight to 'done' must raise a typed
        ValidationError with a helpful message — not a bare ValueError that the
        CLI would render as a raw traceback."""
        from pyrite.exceptions import ValidationError

        svc = task_env["svc"]
        created = svc.create_task(kb_name="test-tasks", title="Redundant task")
        entry_id = created["entry_id"]

        with pytest.raises(ValidationError) as exc_info:
            svc.update_task(entry_id, "test-tasks", status="done")

        msg = str(exc_info.value)
        assert "open" in msg and "done" in msg
        assert "Allowed next" in msg  # tells the caller what they *can* do

    def test_legal_multi_step_path_to_done(self, task_env):
        """Walking the task through the legal states reaches 'done'."""
        svc = task_env["svc"]
        created = svc.create_task(kb_name="test-tasks", title="Worked task")
        entry_id = created["entry_id"]

        svc.update_task(entry_id, "test-tasks", status="claimed", assignee="agent:a")
        svc.update_task(entry_id, "test-tasks", status="in_progress")
        result = svc.update_task(entry_id, "test-tasks", status="done")
        assert result["status"] == "done"

    def test_comment_appends_status_change_log(self, task_env):
        """update_task(..., comment=...) on a status change appends a structured
        status_change_log entry with from/to/by/comment
        (task-update-comment-flag)."""
        svc = task_env["svc"]
        created = svc.create_task(kb_name="test-tasks", title="Logged task")
        eid = created["entry_id"]

        svc.update_task(
            eid,
            "test-tasks",
            status="claimed",
            comment="claimed for the alpha sweep",
            by="agent:a",
        )
        repo = KBRepository(task_env["kb_config"])
        log = getattr(repo.load(eid), "status_change_log", [])
        assert len(log) == 1
        assert log[0]["from"] == "open"
        assert log[0]["to"] == "claimed"
        assert log[0]["by"] == "agent:a"
        assert log[0]["comment"] == "claimed for the alpha sweep"
        assert log[0].get("date")  # a timestamp was stamped

    def test_comment_accumulates_across_transitions(self, task_env):
        """Each commented transition adds a log entry; earlier ones are kept."""
        svc = task_env["svc"]
        created = svc.create_task(kb_name="test-tasks", title="Multi-step")
        eid = created["entry_id"]
        svc.update_task(eid, "test-tasks", status="claimed", comment="claim", by="a")
        svc.update_task(eid, "test-tasks", status="in_progress", comment="start", by="a")
        svc.update_task(eid, "test-tasks", status="done", comment="ship", by="a")

        repo = KBRepository(task_env["kb_config"])
        log = getattr(repo.load(eid), "status_change_log", [])
        assert [e["to"] for e in log] == ["claimed", "in_progress", "done"]

    def test_no_log_entry_without_status_change(self, task_env):
        """A comment on a non-status update (e.g. priority) does not log a
        phantom transition."""
        svc = task_env["svc"]
        created = svc.create_task(kb_name="test-tasks", title="Priority bump")
        eid = created["entry_id"]
        svc.update_task(eid, "test-tasks", priority=8, comment="reprioritized")
        repo = KBRepository(task_env["kb_config"])
        assert getattr(repo.load(eid), "status_change_log", []) == []


class TestCancelledState:
    """A `cancelled` terminal state lets an obsolete task close honestly in one
    call without claiming phantom work (add-cancelled-terminal-state...)."""

    def test_open_to_cancelled_one_call(self, task_env):
        """A never-claimed obsolete task → cancelled directly, no phantom work."""
        svc = task_env["svc"]
        created = svc.create_task(kb_name="test-tasks", title="Obsolete task")
        result = svc.update_task(created["entry_id"], "test-tasks", status="cancelled")
        assert result["status"] == "cancelled"

    def test_in_progress_to_cancelled(self, task_env):
        """A task recognized as obsolete mid-work can also be cancelled."""
        svc = task_env["svc"]
        created = svc.create_task(kb_name="test-tasks", title="Abandon me")
        eid = created["entry_id"]
        svc.update_task(eid, "test-tasks", status="claimed", assignee="agent:a")
        svc.update_task(eid, "test-tasks", status="in_progress")
        result = svc.update_task(eid, "test-tasks", status="cancelled")
        assert result["status"] == "cancelled"

    def test_rollup_counts_cancelled_as_resolved(self, task_env):
        """A parent auto-completes when all children are terminal, treating a
        cancelled child as resolved (so an all-resolved parent doesn't hang)."""
        svc = task_env["svc"]
        parent = svc.create_task(kb_name="test-tasks", title="Parent")
        parent_id = parent["entry_id"]
        svc.update_task(parent_id, "test-tasks", status="claimed")
        svc.update_task(parent_id, "test-tasks", status="in_progress")

        c1 = svc.create_task(kb_name="test-tasks", title="Done child", parent=parent_id)
        c2 = svc.create_task(kb_name="test-tasks", title="Cancelled child", parent=parent_id)

        svc.update_task(c1["entry_id"], "test-tasks", status="claimed")
        svc.update_task(c1["entry_id"], "test-tasks", status="in_progress")
        svc.update_task(c1["entry_id"], "test-tasks", status="done")
        # Cancel the second child — parent should still roll up.
        svc.update_task(c2["entry_id"], "test-tasks", status="cancelled")

        repo = KBRepository(task_env["kb_config"])
        assert repo.load(parent_id).status == "done"

    def test_cancelled_blocker_satisfies_dependency(self, task_env):
        """A cancelled blocker is resolved-by-removal: unblock_dependents treats
        it the same as a done blocker and releases the blocked dependent."""
        svc = task_env["svc"]
        blocker = svc.create_task(kb_name="test-tasks", title="Blocker")
        blocker_id = blocker["entry_id"]
        dependent = svc.create_task(
            kb_name="test-tasks",
            title="Dependent",
            dependencies=[blocker_id],
        )
        dep_id = dependent["entry_id"]
        # Walk dependent to blocked (open → claimed → in_progress → blocked).
        svc.update_task(dep_id, "test-tasks", status="claimed", assignee="agent:a")
        svc.update_task(dep_id, "test-tasks", status="in_progress")
        svc.update_task(dep_id, "test-tasks", status="blocked")

        # Cancel the blocker, then run the (explicit) unblock pass.
        svc.update_task(blocker_id, "test-tasks", status="cancelled")
        unblocked = svc.unblock_dependents(blocker_id, "test-tasks")

        assert any(u["id"] == dep_id for u in unblocked), (
            "a cancelled blocker should release its dependents"
        )
        repo = KBRepository(task_env["kb_config"])
        assert repo.load(dep_id).status != "blocked"


class TestResetTask:
    """`reset_task` releases a stale claim back to `open` (privileged recovery
    path) without the lossy `blocked` + clear-assignee workaround
    (add-task-reset-command-for-stale-claims)."""

    def _to_in_progress(self, svc, title=None, assignee="agent:dead"):
        # Unique per call: task_env is shared across this class, and create no
        # longer silently replaces an entry with the same title-derived id.
        title = title or f"Stale task {uuid.uuid4().hex[:8]}"
        created = svc.create_task(kb_name="test-tasks", title=title)
        eid = created["entry_id"]
        svc.update_task(eid, "test-tasks", status="claimed", assignee=assignee)
        svc.update_task(eid, "test-tasks", status="in_progress")
        return eid

    def test_reset_in_progress_to_open(self, task_env):
        svc = task_env["svc"]
        eid = self._to_in_progress(svc)
        result = svc.reset_task(eid, "test-tasks", reason="stale claim from prior tick")
        assert result["status"] == "open"

    def test_reset_clears_assignee(self, task_env):
        svc = task_env["svc"]
        eid = self._to_in_progress(svc, assignee="agent:crashed")
        svc.reset_task(eid, "test-tasks")
        repo = KBRepository(task_env["kb_config"])
        entry = repo.load(eid)
        assert entry.status == "open"
        assert (getattr(entry, "assignee", "") or "") == ""

    def test_reset_appends_work_log(self, task_env):
        svc = task_env["svc"]
        eid = self._to_in_progress(svc)
        svc.reset_task(eid, "test-tasks", reason="worker died", operator="conductor")
        repo = KBRepository(task_env["kb_config"])
        body = repo.load(eid).body or ""
        assert "Reset from in_progress" in body
        assert "worker died" in body

    def test_reset_blocked_to_open(self, task_env):
        svc = task_env["svc"]
        eid = self._to_in_progress(svc)
        svc.update_task(eid, "test-tasks", status="blocked")
        result = svc.reset_task(eid, "test-tasks")
        assert result["status"] == "open"

    def test_reset_refuses_open_and_done(self, task_env):
        from pyrite.exceptions import ValidationError

        svc = task_env["svc"]
        created = svc.create_task(kb_name="test-tasks", title="Already open")
        with pytest.raises(ValidationError):
            svc.reset_task(created["entry_id"], "test-tasks")


class TestClaimTask:
    def test_claim_success(self, task_env):
        svc = task_env["svc"]
        created = svc.create_task(kb_name="test-tasks", title="Claim me")
        entry_id = created["entry_id"]

        result = svc.claim_task(entry_id, "test-tasks", "agent:claimer")
        assert result["claimed"] is True
        assert result["assignee"] == "agent:claimer"
        assert result["status"] == "claimed"

    def test_claim_already_claimed(self, task_env):
        svc = task_env["svc"]
        created = svc.create_task(kb_name="test-tasks", title="Already claimed")
        entry_id = created["entry_id"]

        svc.claim_task(entry_id, "test-tasks", "agent:first")

        result = svc.claim_task(entry_id, "test-tasks", "agent:second")
        assert result["claimed"] is False
        assert "not 'open'" in result["error"]
        assert result["current_status"] == "claimed"

    def test_claim_not_found(self, task_env):
        svc = task_env["svc"]
        result = svc.claim_task("nonexistent", "test-tasks", "agent:x")
        assert result["claimed"] is False
        assert "not found" in result["error"]

    def test_claim_file_failure_rollback(self, task_env):
        svc = task_env["svc"]
        created = svc.create_task(kb_name="test-tasks", title="Rollback test")
        entry_id = created["entry_id"]

        with patch.object(svc.kb_svc, "update_entry", side_effect=OSError("disk full")):
            result = svc.claim_task(entry_id, "test-tasks", "agent:fail")

        assert result["claimed"] is False
        assert "File update failed" in result["error"]

        row = (
            task_env["db"]
            ._raw_conn.execute(
                "SELECT status FROM entry WHERE id = ?",
                (entry_id,),
            )
            .fetchone()
        )
        assert row["status"] == "open"


class TestClaimEntry:
    """Tests for the protocol-level claim_entry on KBService."""

    def test_claim_entry_directly(self, task_env):
        svc = task_env["svc"]
        created = svc.create_task(kb_name="test-tasks", title="Direct claim")
        entry_id = created["entry_id"]

        result = svc.kb_svc.claim_entry(entry_id, "test-tasks", "agent:direct")
        assert result["claimed"] is True
        assert result["assignee"] == "agent:direct"

    def test_claim_entry_custom_statuses(self, task_env):
        svc = task_env["svc"]
        created = svc.create_task(kb_name="test-tasks", title="Custom claim")
        entry_id = created["entry_id"]

        # Claim with default statuses first
        svc.kb_svc.claim_entry(entry_id, "test-tasks", "agent:x")

        # Try custom from_status/to_status
        result = svc.kb_svc.claim_entry(
            entry_id, "test-tasks", "agent:y", from_status="claimed", to_status="in_progress"
        )
        assert result["claimed"] is True
        assert result["status"] == "in_progress"


class TestGetTaskReadConsistency:
    """Regression coverage for
    `bug-task-status-single-item-single-item-json-read-intermittently-empty`.

    `claim_entry` mutates the index via a raw SQL UPDATE that bypasses the
    ORM identity map. A subsequent single-item read via `session.get()`
    returned the stale identity-mapped object (or empty), while the list
    view (a fresh SQL query) reported the new state correctly. `get_task`
    must read the SAME fresh path as `list_tasks` so the two never diverge.
    """

    def test_get_task_reflects_claim_made_after_first_read(self, task_env):
        svc = task_env["svc"]
        created = svc.create_task(kb_name="test-tasks", title="Read consistency")
        entry_id = created["entry_id"]

        # Prime the session identity map with a pre-claim read.
        first = svc.get_task(entry_id, "test-tasks", readable_kbs=UNSCOPED)
        assert first is not None
        assert (first.get("status") or "open") == "open"

        # Claim mutates the row via raw SQL UPDATE (bypasses identity map).
        claim = svc.claim_task(entry_id, "test-tasks", "agent:reader")
        assert claim["claimed"] is True

        # Single-item read must now agree with the list view.
        after = svc.get_task(entry_id, "test-tasks", readable_kbs=UNSCOPED)
        assert after is not None, "get_task returned empty after claim (read-path divergence)"
        listed = {t["id"]: t for t in svc.list_tasks(kb_name="test-tasks")}[entry_id]
        assert after.get("status") == listed["status"] == "claimed", (
            f"get_task status {after.get('status')!r} diverged from "
            f"list_tasks status {listed['status']!r}"
        )
        assert (after.get("assignee") or "") == "agent:reader"


class TestDecomposeTask:
    def test_decompose_creates_children(self, task_env):
        svc = task_env["svc"]
        parent = svc.create_task(kb_name="test-tasks", title="Epic task")
        parent_id = parent["entry_id"]

        children = [
            {"title": "Subtask 1"},
            {"title": "Subtask 2", "priority": 3},
            {"title": "Subtask 3", "assignee": "agent:worker"},
        ]
        results = svc.decompose_task(parent_id, "test-tasks", children)

        assert len(results) == 3
        assert all(r["created"] for r in results)

        child_tasks = svc.list_tasks(kb_name="test-tasks", parent=parent_id)
        assert len(child_tasks) == 3
        for ct in child_tasks:
            assert ct["parent"] == parent_id

    def test_decompose_parent_not_found(self, task_env):
        from pyrite.exceptions import EntryNotFoundError

        svc = task_env["svc"]
        with pytest.raises(EntryNotFoundError, match="not found"):
            svc.decompose_task("nonexistent", "test-tasks", [{"title": "child"}])


class TestCheckpointTask:
    def test_checkpoint_appends_to_body(self, task_env):
        svc = task_env["svc"]
        created = svc.create_task(kb_name="test-tasks", title="Checkpoint me", body="Initial body")
        entry_id = created["entry_id"]

        result = svc.checkpoint_task(entry_id, "test-tasks", "Found 3 public records")
        assert result["checkpointed"] is True
        assert result["message"] == "Found 3 public records"

        repo = KBRepository(task_env["kb_config"])
        entry = repo.load(entry_id)
        assert "Initial body" in entry.body
        assert "Found 3 public records" in entry.body
        assert "## Checkpoint" in entry.body

    def test_checkpoint_with_evidence(self, task_env):
        svc = task_env["svc"]
        created = svc.create_task(kb_name="test-tasks", title="Evidence task")
        entry_id = created["entry_id"]

        result = svc.checkpoint_task(
            entry_id,
            "test-tasks",
            "Analyzed connections",
            confidence=0.85,
            partial_evidence=["record-1", "record-2"],
        )
        assert result["confidence"] == 0.85
        assert result["evidence"] == ["record-1", "record-2"]

        repo = KBRepository(task_env["kb_config"])
        entry = repo.load(entry_id)
        assert "85%" in entry.body
        assert "`record-1`" in entry.body

    def test_checkpoint_updates_agent_context(self, task_env):
        svc = task_env["svc"]
        created = svc.create_task(kb_name="test-tasks", title="Context task")
        entry_id = created["entry_id"]

        svc.checkpoint_task(entry_id, "test-tasks", "Progress", confidence=0.7)

        repo = KBRepository(task_env["kb_config"])
        entry = repo.load(entry_id)
        assert entry.agent_context["confidence"] == 0.7
        assert entry.agent_context["last_message"] == "Progress"
        assert "last_checkpoint" in entry.agent_context


class TestRollupParent:
    def test_rollup_all_done(self, task_env):
        svc = task_env["svc"]
        parent = svc.create_task(kb_name="test-tasks", title="Parent task")
        parent_id = parent["entry_id"]

        c1 = svc.create_task(kb_name="test-tasks", title="Child 1", parent=parent_id)
        c2 = svc.create_task(kb_name="test-tasks", title="Child 2", parent=parent_id)

        svc.update_task(parent_id, "test-tasks", status="claimed")
        svc.update_task(parent_id, "test-tasks", status="in_progress")

        # Complete first child
        svc.update_task(c1["entry_id"], "test-tasks", status="claimed")
        svc.update_task(c1["entry_id"], "test-tasks", status="in_progress")
        svc.update_task(c1["entry_id"], "test-tasks", status="done")

        # Complete second child — core after_save hook triggers automatic rollup
        svc.update_task(c2["entry_id"], "test-tasks", status="claimed")
        svc.update_task(c2["entry_id"], "test-tasks", status="in_progress")
        svc.update_task(c2["entry_id"], "test-tasks", status="done")

        # Parent should have been auto-rolled-up by the core hook
        repo = KBRepository(task_env["kb_config"])
        parent_entry = repo.load(parent_id)
        assert parent_entry.status == "done"

    def test_rollup_partial(self, task_env):
        svc = task_env["svc"]
        parent = svc.create_task(kb_name="test-tasks", title="Partial parent")
        parent_id = parent["entry_id"]

        c1 = svc.create_task(kb_name="test-tasks", title="Done child", parent=parent_id)
        svc.create_task(kb_name="test-tasks", title="Open child", parent=parent_id)

        svc.update_task(c1["entry_id"], "test-tasks", status="claimed")
        svc.update_task(c1["entry_id"], "test-tasks", status="in_progress")
        svc.update_task(c1["entry_id"], "test-tasks", status="done")

        result = svc.rollup_parent(parent_id, "test-tasks")
        assert result is None

    def test_rollup_cascading(self, task_env):
        svc = task_env["svc"]

        # Grandparent → parent → child
        gp = svc.create_task(kb_name="test-tasks", title="Grandparent")
        gp_id = gp["entry_id"]
        p = svc.create_task(kb_name="test-tasks", title="Parent", parent=gp_id)
        p_id = p["entry_id"]
        c = svc.create_task(kb_name="test-tasks", title="Child", parent=p_id)
        c_id = c["entry_id"]

        # Advance grandparent and parent to in_progress
        for tid in [gp_id, p_id]:
            svc.update_task(tid, "test-tasks", status="claimed")
            svc.update_task(tid, "test-tasks", status="in_progress")

        # Complete child — core hook should cascade: child done → parent done → grandparent done
        svc.update_task(c_id, "test-tasks", status="claimed")
        svc.update_task(c_id, "test-tasks", status="in_progress")
        svc.update_task(c_id, "test-tasks", status="done")

        # Grandparent should be done (cascading via core hooks)
        repo = KBRepository(task_env["kb_config"])
        gp_entry = repo.load(gp_id)
        assert gp_entry.status == "done"


class TestListTasks:
    def test_list_all(self, task_env):
        svc = task_env["svc"]
        before = svc.list_tasks(kb_name="test-tasks")
        svc.create_task(kb_name="test-tasks", title="Task A")
        svc.create_task(kb_name="test-tasks", title="Task B")

        tasks = svc.list_tasks(kb_name="test-tasks")
        assert len(tasks) == len(before) + 2

    def test_list_filter_status(self, task_env):
        svc = task_env["svc"]
        before_open = svc.list_tasks(kb_name="test-tasks", status="open")
        svc.create_task(kb_name="test-tasks", title="Open task")
        t2 = svc.create_task(kb_name="test-tasks", title="Claimed task")
        svc.claim_task(t2["entry_id"], "test-tasks", "agent:x")

        open_tasks = svc.list_tasks(kb_name="test-tasks", status="open")
        assert len(open_tasks) == len(before_open) + 1
        titles = [t["title"] for t in open_tasks]
        assert "Open task" in titles

    def test_list_preserves_priority(self, task_env):
        """Priority set at creation should be readable via list_tasks."""
        svc = task_env["svc"]
        result = svc.create_task(kb_name="test-tasks", title="High Pri Task", priority=9)
        assert result["priority"] == 9

        tasks = svc.list_tasks(kb_name="test-tasks")
        task = next(t for t in tasks if t["id"] == result["entry_id"])
        assert task["priority"] == 9, f"Expected priority 9, got {task['priority']}"

    def test_list_filter_assignee(self, task_env):
        svc = task_env["svc"]
        t1 = svc.create_task(kb_name="test-tasks", title="Assigned")
        svc.claim_task(t1["entry_id"], "test-tasks", "agent:alpha")
        svc.create_task(kb_name="test-tasks", title="Unassigned")

        assigned = svc.list_tasks(kb_name="test-tasks", assignee="agent:alpha")
        assert len(assigned) >= 1
        titles = [t["title"] for t in assigned]
        assert "Assigned" in titles

    def test_parked_awaiting_survives_claim_and_surfaces_in_listing(self, task_env):
        """Regression for
        issue-pyrite-task-update-strips-non-schema-frontmatter-fields-silently-unparks-monitors.

        `parked_awaiting:` is not a TaskEntry schema field — it's a
        convention the conductor/dispatch pipeline reads out of frontmatter
        directly. Simulates the exact reported failure: a worker hand-adds
        `parked_awaiting:` to a task file, then a CLI call (`task claim`,
        which round-trips the file through TaskEntry.from_frontmatter ->
        mutate -> to_frontmatter) must not drop it — and once present, both
        the unfiltered and assignee-filtered `list_tasks` paths must surface
        it (write-path and read-path bugs respectively).
        """
        svc = task_env["svc"]
        kb_config = task_env["kb_config"]

        created = svc.create_task(kb_name="test-tasks", title="Monitor: RFP deadline")
        entry_id = created["entry_id"]

        # Simulate a worker hand-editing the file to add parked_awaiting,
        # the way the human-task / monitor convention requires (no CLI
        # flag exists for this field).
        repo = KBRepository(kb_config)
        entry = repo.load(entry_id)
        entry.metadata["parked_awaiting"] = "rfp-due-2026-09-11"
        repo.save(entry)

        # This is the exact call the ticket reports as lossy.
        svc.claim_task(entry_id, "test-tasks", "agent:regression-test")

        # Write path: the field must still be on disk after claim.
        reloaded = repo.load(entry_id)
        assert reloaded.metadata.get("parked_awaiting") == "rfp-due-2026-09-11", (
            "task claim dropped parked_awaiting from the file's frontmatter"
        )

        # Read path: list_tasks (unfiltered — no assignee, no N+1 hydration)
        # must surface it directly off the indexed metadata.
        unfiltered = {t["id"]: t for t in svc.list_tasks(kb_name="test-tasks")}
        assert unfiltered[entry_id]["parked_awaiting"] == "rfp-due-2026-09-11", (
            "list_tasks (unfiltered) did not surface parked_awaiting from the index"
        )

        # Read path: assignee-filtered listing (the hydration path) must
        # also surface it.
        filtered = {
            t["id"]: t
            for t in svc.list_tasks(kb_name="test-tasks", assignee="agent:regression-test")
        }
        assert filtered[entry_id]["parked_awaiting"] == "rfp-due-2026-09-11", (
            "list_tasks (assignee-filtered) did not surface parked_awaiting"
        )


# =========================================================================
# Migration: pyrite task migrate-relaxed-mode — Tier A r1175 fire 3/4
# =========================================================================


@pytest.fixture
def migration_env():
    """Fresh per-test env so migration counters are deterministic.

    The class-scoped task_env accumulates state across tests which makes
    scanned/migrated counts unstable for assertions.
    """
    import tempfile
    from pathlib import Path

    from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
    from pyrite.services.task_service import TaskService
    from pyrite.storage.database import PyriteDB

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        kb_path = tmpdir / "kb"
        kb_path.mkdir()

        kb_config = KBConfig(
            name="migration-kb",
            path=kb_path,
            kb_type=KBType.GENERIC,
        )
        config = PyriteConfig(
            knowledge_bases=[kb_config],
            settings=Settings(index_path=tmpdir / "index.db"),
        )
        db = PyriteDB(config.settings.index_path)
        db.register_kb(
            name="migration-kb",
            kb_type="generic",
            path=str(kb_path),
            description="",
        )
        svc = TaskService(config, db)
        yield {"svc": svc, "config": config, "db": db, "kb_config": kb_config}
        db.close()


class TestMigrateRelaxedMode:
    """`TaskService.migrate_relaxed_mode(kb_name, dry_run=False)` backfills
    status_reason='pre-relaxed-mode' for tasks of types that have
    opted into relaxed-reason mode but lack a reason.

    Strict-mode tasks are skipped (back-compat). Tasks that already
    have a status_reason are skipped (idempotency).
    """

    def _patch_relaxed_workflow(self, kb_config):
        """Inject a state_machine on the `task` type's schema so the
        resolver sees it as a relaxed-reason type. Real-world this
        would live in kb.yaml; here we shortcut to the schema layer."""
        from pyrite.schema import TypeSchema

        relaxed = {
            "states": ["open", "claimed", "in_progress", "blocked", "done"],
            "initial": "open",
            "field": "status",
            "transitions": [],
            "enforce_transitions": False,
            "require_reason_on_transition": True,
        }
        kb_config.kb_schema.types["task"] = TypeSchema(name="task", state_machine=relaxed)

    def test_strict_mode_kb_migrates_nothing(self, migration_env):
        """A KB whose `task` type has NO state_machine override means
        TASK_WORKFLOW (strict, no reason required) → no tasks need
        backfilling."""
        svc = migration_env["svc"]
        svc.create_task(kb_name="migration-kb", title="t1")
        svc.create_task(kb_name="migration-kb", title="t2")

        result = svc.migrate_relaxed_mode("migration-kb")
        assert result["scanned"] == 2
        assert result["migrated"] == 0
        assert result["skipped"] == 2
        assert result["dry_run"] is False

    def test_relaxed_mode_migrates_tasks_without_reason(self, migration_env):
        """Under a relaxed-reason type, tasks lacking status_reason
        get backfilled with 'pre-relaxed-mode'."""
        svc = migration_env["svc"]
        kb_config = migration_env["kb_config"]

        svc.create_task(kb_name="migration-kb", title="t1")
        svc.create_task(kb_name="migration-kb", title="t2")

        self._patch_relaxed_workflow(kb_config)

        result = svc.migrate_relaxed_mode("migration-kb")
        assert result["scanned"] == 2
        assert result["migrated"] == 2
        assert result["skipped"] == 0
        assert len(result["migrated_ids"]) == 2

        # Verify the reason actually landed on disk
        for tid in result["migrated_ids"]:
            entry = svc.kb_svc.get_entry(tid, "migration-kb", readable_kbs=UNSCOPED)
            assert entry["status_reason"] == "pre-relaxed-mode"

    def test_dry_run_does_not_write(self, migration_env):
        svc = migration_env["svc"]
        kb_config = migration_env["kb_config"]

        svc.create_task(kb_name="migration-kb", title="t1")
        self._patch_relaxed_workflow(kb_config)

        result = svc.migrate_relaxed_mode("migration-kb", dry_run=True)
        assert result["dry_run"] is True
        assert result["migrated"] == 1

        # Confirm no write happened — entry still has empty reason
        tid = result["migrated_ids"][0]
        entry = svc.kb_svc.get_entry(tid, "migration-kb", readable_kbs=UNSCOPED)
        assert (entry.get("status_reason") or "") == ""

    def test_idempotent_skips_tasks_with_existing_reason(self, migration_env):
        """A second migration run finds nothing left to do."""
        svc = migration_env["svc"]
        kb_config = migration_env["kb_config"]

        svc.create_task(kb_name="migration-kb", title="t1")
        self._patch_relaxed_workflow(kb_config)

        first = svc.migrate_relaxed_mode("migration-kb")
        assert first["migrated"] == 1

        # Second pass: same KB, same data, but now every task already
        # has a reason — nothing to migrate.
        second = svc.migrate_relaxed_mode("migration-kb")
        assert second["scanned"] == 1
        assert second["migrated"] == 0
        assert second["skipped"] == 1

    def test_unknown_kb_raises(self, migration_env):
        from pyrite.exceptions import KBNotFoundError

        svc = migration_env["svc"]
        with pytest.raises(KBNotFoundError):
            svc.migrate_relaxed_mode("no-such-kb")
