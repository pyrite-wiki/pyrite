"""Task service — operative task operations wrapping KBService."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..config import PyriteConfig
from ..exceptions import EntryNotFoundError, KBNotFoundError, ValidationError
from ..storage.backends.base_backend import kb_names_clause
from ..storage.database import PyriteDB
from ..utils.metadata import parse_metadata
from .access_policy import UNSCOPED, named_kb

if TYPE_CHECKING:
    from ..models import Entry
    from .hook_runner import HookRunner

logger = logging.getLogger(__name__)


class TaskService:
    """Service for task-specific operations.

    Wraps KBService for standard CRUD and adds task-specific
    atomic operations: claim, decompose, checkpoint, rollup.
    """

    def __init__(self, config: PyriteConfig, db: PyriteDB, kb_svc=None):
        self.config = config
        self.db = db
        self._kb_svc = kb_svc

    @property
    def kb_svc(self):
        if self._kb_svc is None:
            from .kb_service import KBService

            self._kb_svc = KBService(self.config, self.db)
        return self._kb_svc

    def _query(self, sql: str, params: dict | None = None) -> list[dict]:
        """Execute SQL through session connection for consistency with ORM writes."""
        return self.db.execute_sql(sql, params)

    def create_task(
        self,
        kb_name: str,
        title: str,
        body: str = "",
        parent: str = "",
        priority: int = 5,
        assignee: str = "",
        dependencies: list[str] | None = None,
        tags: list[str] | None = None,
        fields: dict[str, Any] | None = None,
        *,
        # Legacy alias
        parent_task: str = "",
    ) -> dict[str, Any]:
        """Create a new task entry.

        ``fields`` carries any additional key a KB's schema requires or
        allows for ``task`` beyond the parameters above (#397) -- e.g. the
        desk schema's ``project``/``kind``, without which a KB requiring
        them refuses every task this command tries to create. The CLI and
        MCP are responsible for refusing a key that already has its own
        parameter or that `TaskEntry.managed_fields` reserves; this method
        does not re-check that, so an in-process caller passing `fields`
        directly is trusted the way `create_entry`'s own `**kwargs` is.

        Returns:
            Dict with created=True and entry details.
        """
        from ..schema import generate_entry_id

        # Support legacy parent_task parameter
        parent = parent or parent_task

        entry_id = generate_entry_id(title)
        kwargs: dict[str, Any] = {"status": "open", "priority": priority}
        if parent:
            kwargs["parent"] = parent
        if assignee:
            kwargs["assignee"] = assignee
        if dependencies:
            kwargs["dependencies"] = dependencies
        if tags:
            kwargs["tags"] = tags
        if fields:
            kwargs.update(fields)

        entry = self.kb_svc.create_entry(
            kb_name=kb_name,
            entry_id=entry_id,
            title=title,
            entry_type="task",
            body=body,
            **kwargs,
        )
        return {
            "created": True,
            "entry_id": entry.id,
            "title": entry.title,
            "status": getattr(entry, "status", "open"),
            "priority": getattr(entry, "priority", 5),
            "parent": getattr(entry, "parent", ""),
            "assignee": getattr(entry, "assignee", ""),
            "kb_name": kb_name,
        }

    def update_task(self, task_id: str, kb_name: str, **updates) -> dict[str, Any]:
        """Update task fields.

        A ``comment`` (and optional ``by``) records *why* a status transition
        happened: when this update changes ``status`` and a comment is supplied,
        a structured entry is appended to the task's ``status_change_log``
        (audit trail lives with the task, not only in git history). A comment on
        a non-status update is ignored — no phantom transition is logged.

        Returns:
            Dict with updated=True and entry details.
        """
        comment = updates.pop("comment", "") or ""
        by = updates.pop("by", "") or ""

        # Capture the prior status so we can record the transition.
        old_status = ""
        if comment and "status" in updates:
            from ..storage.repository import KBRepository

            kb_config = self.config.get_kb(kb_name)
            if kb_config:
                existing = KBRepository(kb_config).load(task_id)
                old_status = getattr(existing, "status", "") if existing else ""
                new_status = updates["status"]
                if old_status != new_status:
                    log = list(getattr(existing, "status_change_log", []) or [])
                    log.append(
                        {
                            "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "from": old_status,
                            "to": new_status,
                            "by": by or "operator",
                            "comment": comment,
                        }
                    )
                    updates["status_change_log"] = log

        entry = self.kb_svc.update_entry(task_id, kb_name, **updates)
        return {
            "updated": True,
            "task_id": entry.id,
            "title": entry.title,
            "status": getattr(entry, "status", "open"),
            "priority": getattr(entry, "priority", 5),
            "assignee": getattr(entry, "assignee", ""),
            "updates": updates,
        }

    def migrate_relaxed_mode(self, kb_name: str, dry_run: bool = False) -> dict[str, Any]:
        """Backfill status_reason='pre-relaxed-mode' for tasks of types
        that have opted into relaxed mode.

        Walks every task in ``kb_name``, resolves each one's workflow
        via the KB schema, and stamps ``status_reason='pre-relaxed-mode'``
        on any task whose type declares
        ``require_reason_on_transition=true`` AND that lacks a
        ``status_reason``.

        Idempotent: a task with an existing reason is skipped.

        Tier A r1175 — provides a migration path so existing tasks
        don't fail validation the next time their status changes.

        Args:
            kb_name: KB to migrate.
            dry_run: When True, return the plan without writing.

        Returns:
            ``{"kb_name", "scanned", "migrated", "skipped", "dry_run"}``.
        """
        from ..models.task import resolve_workflow_for_type

        kb_config = self.config.get_kb(kb_name)
        if kb_config is None:
            from ..exceptions import KBNotFoundError

            raise KBNotFoundError(f"KB not found: {kb_name}")

        kb_schema = getattr(kb_config, "kb_schema", None)

        # Pull every task in this KB. The migration is per-KB so we
        # don't have to worry about cross-KB schema resolution.
        rows = self._query(
            "SELECT id, entry_type FROM entry WHERE kb_name = :kb AND entry_type = 'task'",
            {"kb": kb_name},
        )

        scanned = len(rows)
        migrated = 0
        skipped = 0
        migrated_ids: list[str] = []

        for row in rows:
            entry_type = row.get("entry_type") or "task"
            workflow = resolve_workflow_for_type(entry_type, kb_schema)
            if not workflow.get("require_reason_on_transition", False):
                # Type isn't in relaxed-reason mode — nothing to do.
                skipped += 1
                continue

            # Load the entry to check the reason field.
            entry = self.kb_svc.get_entry(row["id"], kb_name, readable_kbs=UNSCOPED)
            if entry is None:
                skipped += 1
                continue
            existing = (entry.get("status_reason") or "").strip()
            if existing:
                # Already has a reason — idempotency.
                skipped += 1
                continue

            migrated += 1
            migrated_ids.append(row["id"])
            if not dry_run:
                self.kb_svc.update_entry(row["id"], kb_name, status_reason="pre-relaxed-mode")

        return {
            "kb_name": kb_name,
            "scanned": scanned,
            "migrated": migrated,
            "skipped": skipped,
            "migrated_ids": migrated_ids,
            "dry_run": dry_run,
        }

    def get_task(
        self, task_id: str, kb_name: str | None = None, *, readable_kbs: set[str] | None
    ) -> dict[str, Any] | None:
        """Get task details from the index.

        Reads via the SAME fresh SQL path as :meth:`list_tasks` rather than
        the ORM ``session.get`` used by ``KBService.get_entry``. Task claims
        mutate the row through a raw SQL UPDATE; routing the single-item read
        through identity-mapped ``session.get`` risked returning stale/empty
        data that diverged from the list view (the read-after-write window
        documented in the task-status read-inconsistency bug). Using one SQL
        read path for both makes that divergence structurally impossible.

        ``readable_kbs`` (required; ``UNSCOPED`` for none) bounds the lookup:
        without ``kb_name`` it takes the first match among the readable KBs
        only, so a task in a KB the caller cannot read neither answers nor
        hides a readable task with the same id (P-R5).
        """
        sql = (
            "SELECT id, title, kb_name, status, assignee, priority, metadata "
            "FROM entry WHERE entry_type = 'task' AND id = :id"
        )
        params: dict[str, Any] = {"id": task_id}
        kb_name = named_kb(kb_name)
        if kb_name:
            sql += " AND kb_name = :kb_name"
            params["kb_name"] = kb_name
        scope = kb_names_clause("kb_name", readable_kbs, params)
        if scope:
            sql += f" AND {scope}"
        sql += " LIMIT 1"

        rows = self._query(sql, params)
        if not rows:
            return None
        return rows[0]

    def list_tasks(
        self,
        kb_name: str | None = None,
        status: str | None = None,
        assignee: str | None = None,
        parent: str | None = None,
        kb_names: set[str] | list[str] | None = None,
        priority: int | None = None,
    ) -> list[dict[str, Any]]:
        """List tasks with optional filters.

        ``kb_names`` restricts the result to the caller's readable KBs;
        ``None`` means unrestricted, an empty set means no rows.
        """
        query = "SELECT id, title, kb_name, status, assignee, priority, metadata, updated_at FROM entry WHERE entry_type = 'task'"
        params: dict[str, Any] = {}
        if kb_name:
            query += " AND kb_name = :kb_name"
            params["kb_name"] = kb_name
        if kb_names is not None:
            names = list(kb_names)
            if not names:
                # "may read nothing" must match no rows; IN () is not SQL.
                query += " AND 1 = 0"
            else:
                keys = []
                for i, name in enumerate(names):
                    params[f"kbn_{i}"] = name
                    keys.append(f":kbn_{i}")
                query += f" AND kb_name IN ({', '.join(keys)})"
        if status:
            if status == "open":
                query += " AND (status = 'open' OR status IS NULL)"
            else:
                query += " AND status = :status"
                params["status"] = status
        if assignee:
            query += " AND assignee = :assignee"
            params["assignee"] = assignee
        if priority is not None:
            query += " AND priority = :priority"
            params["priority"] = priority
        if parent:
            query += " AND json_extract(metadata, '$.parent') = :parent"
            params["parent"] = parent
        query += " ORDER BY created_at DESC"

        rows = self._query(query, params)
        tasks = []
        for row in rows:
            meta = _parse_metadata(row.get("metadata"))
            tasks.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "status": row.get("status") or meta.get("status", "open"),
                    "assignee": row.get("assignee") or meta.get("assignee", ""),
                    "priority": int(row.get("priority") or meta.get("priority", 5)),
                    "parent": meta.get("parent", ""),
                    "kb_name": row["kb_name"],
                    # Why a task is parked. Drives the human worklist board's
                    # columns; see kb/scripts/human-worklist-migrate.py for the
                    # vocabulary (browser-session / decision / foia-response /
                    # outreach / external-clock).
                    #
                    # TaskEntry now preserves unknown top-level frontmatter
                    # keys (like this one) into entry.metadata on load/save,
                    # so this reads correctly off the raw DB row for tasks
                    # saved since that fix. Older rows saved before the fix
                    # (or entries whose metadata JSON predates a reindex)
                    # still fall through to the hydration pass below, which
                    # reads the file directly and stays as a safety net.
                    # See: issue-pyrite-task-update-strips-non-schema-frontmatter-fields-silently-unparks-monitors
                    "parked_awaiting": meta.get("parked_awaiting", ""),
                    "updated_at": row.get("updated_at") or "",
                }
            )

        # Hydrate parked_awaiting from the entries themselves. Only done when
        # a caller is looking at a bounded set (a specific assignee, e.g. the
        # human worklist) -- an unfiltered listing would turn this into an
        # N+1 across the whole backlog for a field it doesn't use.
        if assignee:
            for t in tasks:
                if t["parked_awaiting"]:
                    continue
                try:
                    entry = self.kb_svc.get_entry(t["id"], t["kb_name"], readable_kbs=UNSCOPED)
                except Exception:
                    continue
                if entry:
                    t["parked_awaiting"] = (entry.get("parked_awaiting") or "").strip()

        return tasks

    # -- Protocol finders (ADR-0017) -------------------------------------
    # Cross-type queries over the protocol columns. The MCP `kb_find_*`
    # tools call these (#380). `kb_names` narrows in SQL, before LIMIT.

    def find_by_assignee(
        self,
        assignee: str,
        kb_name: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Entries of any type assigned to ``assignee``, newest first."""
        return self.db.find_by_assignee(
            assignee=assignee,
            kb_name=kb_name,
            status=status,
            limit=limit,
            offset=offset,
            kb_names=kb_names,
        )

    def find_overdue(
        self,
        as_of: str | None = None,
        kb_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Entries not done or failed whose ``due_date`` is before ``as_of`` (default today)."""
        return self.db.find_overdue(
            as_of=as_of, kb_name=kb_name, limit=limit, offset=offset, kb_names=kb_names
        )

    def find_by_status(
        self,
        status: str,
        kb_name: str | None = None,
        entry_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Entries of any type with ``status``, newest first."""
        return self.db.find_by_status(
            status=status,
            kb_name=kb_name,
            entry_type=entry_type,
            limit=limit,
            offset=offset,
            kb_names=kb_names,
        )

    def find_by_location(
        self,
        location: str,
        kb_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Entries of any type whose ``location`` contains ``location``, newest first."""
        return self.db.find_by_location(
            location=location, kb_name=kb_name, limit=limit, offset=offset, kb_names=kb_names
        )

    def claim_task(self, task_id: str, kb_name: str, assignee: str) -> dict[str, Any]:
        """Atomically claim an open task. Delegates to KBService.claim_entry()."""
        return self.kb_svc.claim_entry(task_id, kb_name, assignee)

    def reset_task(
        self,
        task_id: str,
        kb_name: str,
        reason: str = "",
        operator: str = "operator",
    ) -> dict[str, Any]:
        """Release a stale claim back to `open` (privileged recovery path).

        For tasks stuck `in_progress` or `blocked` because a worker crashed,
        was killed, or finished without updating status. Returns the task to
        `open` so the conductor can re-dispatch it, clears the assignee, and
        appends a work-log entry for the audit trail. Refuses tasks that aren't
        `in_progress`/`blocked` (there's nothing to release).
        """
        from ..storage.repository import KBRepository

        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            raise KBNotFoundError(f"KB not found: {kb_name}")

        repo = KBRepository(kb_config)
        entry = repo.load(task_id)
        if not entry:
            raise EntryNotFoundError(f"Task '{task_id}' not found in KB '{kb_name}'")

        prior_status = getattr(entry, "status", "")
        if prior_status not in ("in_progress", "blocked"):
            raise ValidationError(
                f"Cannot reset task in status '{prior_status}'. Reset only "
                f"releases a stale 'in_progress' or 'blocked' claim back to "
                f"'open'."
            )

        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        why = reason or "stale-claim recovery"
        log_line = (
            f"\n\n## Reset {timestamp}\n\n"
            f"Reset from {prior_status} by {operator} on {timestamp}. "
            f"Reason: {why}."
        )
        new_body = (entry.body or "") + log_line

        self.kb_svc.update_entry(
            task_id,
            kb_name,
            status="open",
            assignee="",
            status_reason=why,
            body=new_body,
        )
        return {
            "reset": True,
            "task_id": task_id,
            "status": "open",
            "prior_status": prior_status,
            "reason": why,
        }

    def decompose_task(
        self, parent_id: str, kb_name: str, children: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Decompose a parent task into child tasks."""
        parent = self.kb_svc.get_entry(parent_id, kb_name, readable_kbs=UNSCOPED)
        if not parent:
            raise EntryNotFoundError(f"Parent task '{parent_id}' not found in KB '{kb_name}'")

        specs = []
        for child in children:
            spec = {
                "entry_type": "task",
                "title": child["title"],
                "body": child.get("body", ""),
                "parent": parent_id,
                "status": "open",
                "priority": child.get("priority", 5),
            }
            if child.get("assignee"):
                spec["assignee"] = child["assignee"]
            specs.append(spec)

        return self.kb_svc.bulk_create_entries(kb_name, specs)

    def checkpoint_task(
        self,
        task_id: str,
        kb_name: str,
        message: str,
        confidence: float = 0.0,
        partial_evidence: list[str] | None = None,
    ) -> dict[str, Any]:
        """Append a timestamped checkpoint to a task."""
        from ..storage.repository import KBRepository

        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            raise KBNotFoundError(f"KB not found: {kb_name}")

        repo = KBRepository(kb_config)
        entry = repo.load(task_id)
        if not entry:
            raise EntryNotFoundError(f"Task '{task_id}' not found in KB '{kb_name}'")

        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Build checkpoint section
        section = f"\n\n## Checkpoint {timestamp}\n\n{message}"
        if confidence > 0:
            section += f"\n\n**Confidence**: {int(confidence * 100)}%"
        if partial_evidence:
            evidence_str = ", ".join(f"`{e}`" for e in partial_evidence)
            section += f"\n\n**Evidence**: {evidence_str}"

        new_body = (entry.body or "") + section

        # Update agent_context
        agent_ctx = dict(getattr(entry, "agent_context", {}) or {})
        agent_ctx["last_checkpoint"] = timestamp
        agent_ctx["last_message"] = message
        if confidence > 0:
            agent_ctx["confidence"] = confidence
        if partial_evidence:
            existing = agent_ctx.get("evidence", [])
            agent_ctx["evidence"] = list(set(existing + partial_evidence))

        updates: dict[str, Any] = {"body": new_body, "agent_context": agent_ctx}
        if partial_evidence:
            existing_evidence = list(getattr(entry, "evidence", []) or [])
            merged = list(set(existing_evidence + partial_evidence))
            updates["evidence"] = merged

        self.kb_svc.update_entry(task_id, kb_name, **updates)

        return {
            "checkpointed": True,
            "task_id": task_id,
            "timestamp": timestamp,
            "message": message,
            "confidence": confidence,
            "evidence": partial_evidence or [],
        }

    def rollup_parent(self, parent_id: str, kb_name: str) -> dict[str, Any] | None:
        """Auto-complete a parent task when all children are resolved (done or
        cancelled). A cancelled child counts as resolved-by-retirement."""
        from ..models.task import TASK_RESOLVED_STATUSES

        rows = self._query(
            """SELECT id, status
               FROM entry
               WHERE kb_name = :kb_name
               AND json_extract(metadata, '$.parent') = :parent_id""",
            {"kb_name": kb_name, "parent_id": parent_id},
        )

        if not rows:
            return None

        all_resolved = all(row["status"] in TASK_RESOLVED_STATUSES for row in rows)
        if not all_resolved:
            return None

        parent_rows = self._query(
            """SELECT status
               FROM entry WHERE id = :parent_id AND kb_name = :kb_name""",
            {"parent_id": parent_id, "kb_name": kb_name},
        )
        if not parent_rows:
            return None
        parent_status = parent_rows[0]["status"]
        if parent_status in ("done", "failed"):
            return None

        entry = self.kb_svc.update_entry(parent_id, kb_name, status="done")

        result = {
            "rolled_up": True,
            "parent_id": parent_id,
            "children_count": len(rows),
        }

        # Cascade: check if the parent itself has a parent
        grandparent_id = getattr(entry, "parent", "")
        if grandparent_id:
            try:
                self.rollup_parent(grandparent_id, kb_name)
            except Exception as e:
                logger.warning("Cascading rollup failed for %s: %s", grandparent_id, e)

        return result

    def unblock_dependents(self, task_id: str, kb_name: str) -> list[dict[str, Any]]:
        """When a task resolves, auto-unblock tasks that depended on it.

        Finds tasks with status='blocked' whose dependencies all resolve to a
        terminal-resolved status (done or cancelled — a cancelled blocker is
        resolved-by-removal), and transitions them onward.
        """
        from ..models.task import TASK_RESOLVED_STATUSES

        # Find tasks that have this task as a dependency
        rows = self._query(
            """SELECT id, metadata FROM entry
               WHERE kb_name = :kb_name AND entry_type = 'task'
               AND status = 'blocked'""",
            {"kb_name": kb_name},
        )

        unblocked = []
        for row in rows:
            meta = _parse_metadata(row.get("metadata"))
            deps = meta.get("dependencies", [])
            if not deps or task_id not in deps:
                continue

            # Check if ALL dependencies are now resolved (done or cancelled)
            all_done = True
            for dep_id in deps:
                dep_rows = self._query(
                    "SELECT status FROM entry WHERE id = :id AND kb_name = :kb_name",
                    {"id": dep_id, "kb_name": kb_name},
                )
                if not dep_rows or dep_rows[0].get("status") not in TASK_RESOLVED_STATUSES:
                    all_done = False
                    break

            if all_done:
                self.kb_svc.update_entry(row["id"], kb_name, status="in_progress")
                unblocked.append({"id": row["id"], "title": row.get("title", "")})

        return unblocked

    def aggregate_evidence_to_parent(self, task_id: str, kb_name: str) -> dict[str, Any] | None:
        """Aggregate evidence from a child task up to its parent.

        When a child task accumulates evidence links, copy them to the parent
        so querying the parent shows all evidence from its subtree.
        """
        task = self.get_task(task_id, kb_name, readable_kbs=UNSCOPED)
        if not task:
            return None

        meta = _parse_metadata(task.get("metadata") or {})
        parent_id = meta.get("parent") or task.get("parent", "")
        if not parent_id:
            return None

        child_evidence = meta.get("evidence", []) or task.get("evidence", []) or []
        if not child_evidence:
            return None

        parent = self.get_task(parent_id, kb_name, readable_kbs=UNSCOPED)
        if not parent:
            return None

        parent_meta = _parse_metadata(parent.get("metadata") or {})
        parent_evidence = parent_meta.get("evidence", []) or parent.get("evidence", []) or []

        # Merge without duplicates
        new_evidence = list(set(parent_evidence) | set(child_evidence))
        if len(new_evidence) == len(parent_evidence):
            return None  # Nothing new to add

        self.kb_svc.update_entry(parent_id, kb_name, evidence=new_evidence)
        added = len(new_evidence) - len(parent_evidence)
        return {
            "parent_id": parent_id,
            "evidence_added": added,
            "total_evidence": len(new_evidence),
        }

    def list_entries_needing_qa(self, kb_name: str | None = None) -> list[dict[str, Any]]:
        """Find entries that have open/unclaimed QA validation tasks.

        Returns entries (not tasks) that need QA review.
        """
        query = """SELECT DISTINCT e.id, e.title, e.kb_name, e.entry_type
                   FROM entry t
                   JOIN entry e ON json_extract(t.metadata, '$.target_entry') = e.id
                                AND t.kb_name = e.kb_name
                   WHERE t.entry_type = 'task'
                   AND t.status IN ('open', NULL)
                   AND (t.assignee IS NULL OR t.assignee = '')
                   AND json_extract(t.metadata, '$.task_type') = 'qa_validation'"""
        params: dict[str, str] = {}
        if kb_name:
            query += " AND t.kb_name = :kb_name"
            params["kb_name"] = kb_name
        query += " ORDER BY e.updated_at DESC"

        return self._query(query, params)

    def link_qa_assessment(self, task_id: str, assessment_id: str, kb_name: str) -> dict[str, Any]:
        """Link a QA assessment entry as evidence on a QA task.

        When a QA agent creates an assessment entry, this links it
        to the corresponding task for traceability.
        """
        task = self.get_task(task_id, kb_name, readable_kbs=UNSCOPED)
        if not task:
            return {"linked": False, "error": "Task not found"}

        meta = _parse_metadata(task.get("metadata") or {})
        evidence = meta.get("evidence", []) or task.get("evidence", []) or []

        if assessment_id not in evidence:
            evidence.append(assessment_id)
            self.kb_svc.update_entry(task_id, kb_name, evidence=evidence)

        return {
            "linked": True,
            "task_id": task_id,
            "assessment_id": assessment_id,
            "total_evidence": len(evidence),
        }

    # ── DAG traversal methods ──────────────────────────────────────

    def get_subtree(self, task_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Get all descendants of a task (children, grandchildren, etc.)."""
        result: list[dict[str, Any]] = []
        visited: set[str] = set()

        def _collect(parent_id: str) -> None:
            children = self._query(
                """SELECT id, title, status, entry_type,
                          json_extract(metadata, '$.parent') as parent,
                          json_extract(metadata, '$.assignee') as assignee,
                          importance as priority
                   FROM entry
                   WHERE kb_name = :kb_name AND entry_type = 'task'
                   AND json_extract(metadata, '$.parent') = :parent_id""",
                {"kb_name": kb_name, "parent_id": parent_id},
            )
            for child in children:
                cid = child["id"]
                if cid in visited:
                    continue
                visited.add(cid)
                result.append(child)
                _collect(cid)

        _collect(task_id)
        return result

    def get_ancestors(self, task_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Get parent chain from task to root. Returns [parent, grandparent, ...]."""
        result: list[dict[str, Any]] = []
        visited: set[str] = set()
        current_id = task_id

        while True:
            if current_id in visited:
                break
            visited.add(current_id)

            task = self.get_task(current_id, kb_name, readable_kbs=UNSCOPED)
            if not task:
                break

            meta = _parse_metadata(task.get("metadata") or {})
            parent_id = meta.get("parent") or task.get("parent", "")
            if not parent_id:
                break

            parent = self.get_task(parent_id, kb_name, readable_kbs=UNSCOPED)
            if not parent:
                break

            result.append(
                {
                    "id": parent["id"],
                    "title": parent.get("title", ""),
                    "status": parent.get("status", ""),
                    "entry_type": parent.get("entry_type", "task"),
                }
            )
            current_id = parent_id

        return result

    def get_blocked_by(self, task_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Get transitive dependency chain — all tasks blocking this one."""
        result: list[dict[str, Any]] = []
        visited: set[str] = set()

        def _collect_deps(tid: str) -> None:
            if tid in visited:
                return
            visited.add(tid)

            task = self.get_task(tid, kb_name, readable_kbs=UNSCOPED)
            if not task:
                return

            meta = _parse_metadata(task.get("metadata") or {})
            deps = meta.get("dependencies", []) or task.get("dependencies", []) or []

            for dep_id in deps:
                if dep_id in visited:
                    continue
                dep = self.get_task(dep_id, kb_name, readable_kbs=UNSCOPED)
                if dep:
                    result.append(
                        {
                            "id": dep["id"],
                            "title": dep.get("title", ""),
                            "status": dep.get("status", ""),
                            "entry_type": dep.get("entry_type", "task"),
                        }
                    )
                    _collect_deps(dep_id)

        _collect_deps(task_id)
        return result

    def critical_path(self, task_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Find the longest chain of unresolved dependencies (critical path).

        Returns the ordered list of tasks in the longest blocking chain.
        Handles cycles gracefully via visited set.
        """
        visited: set[str] = set()

        def _longest_chain(tid: str) -> list[dict[str, Any]]:
            if tid in visited:
                return []
            visited.add(tid)

            task = self.get_task(tid, kb_name, readable_kbs=UNSCOPED)
            if not task:
                return []

            meta = _parse_metadata(task.get("metadata") or {})
            deps = meta.get("dependencies", []) or task.get("dependencies", []) or []

            if not deps:
                return []

            best_chain: list[dict[str, Any]] = []
            for dep_id in deps:
                dep = self.get_task(dep_id, kb_name, readable_kbs=UNSCOPED)
                if not dep:
                    continue
                sub_chain = _longest_chain(dep_id)
                candidate = [
                    {
                        "id": dep["id"],
                        "title": dep.get("title", ""),
                        "status": dep.get("status", ""),
                        "entry_type": dep.get("entry_type", "task"),
                    }
                ] + sub_chain
                if len(candidate) > len(best_chain):
                    best_chain = candidate

            return best_chain

        return _longest_chain(task_id)


def _parse_metadata(raw) -> dict[str, Any]:
    """Parse metadata JSON from a DB row."""
    return parse_metadata(raw)


# =============================================================================
# Core hooks — moved from kb_service.py in extract-hookrunner-from-kb-service
# step 3. These are platform-level lifecycle hooks specific to tasks
# (transition validation, parent rollup). They live here because they belong
# with task semantics, not with generic CRUD. KBService registers them on its
# HookRunner at startup via register_task_hooks().
#
# Both hooks are polymorphic over Entry: _task_validate_transition early-exits
# unless entry_type == "task"; _parent_rollup runs for any Parentable entry
# that reaches a terminal status. Hence the Entry type hint rather than
# TaskEntry.
# =============================================================================


def _task_validate_transition(entry: Entry, context: dict) -> Entry:
    """Validate status transitions against the type's workflow on update.

    Dispatches through ``validate_status_change`` (Tier A r1175), which
    chooses strict vs relaxed mode based on the workflow's
    ``enforce_transitions`` flag. The core ``TASK_WORKFLOW`` defaults
    to strict so every existing task keeps its pre-r1175 behavior; a
    plugin's entry type opts into relaxed mode by declaring a
    ``state_machine`` block on its TypeSchema with
    ``enforce_transitions=false`` (and typically
    ``require_reason_on_transition=true``).

    Per-entity-type workflow resolution (Tier A r1175 fire 3): the
    hook now calls ``resolve_workflow_for_type`` which reads the
    entry-type's ``state_machine`` from the KB schema when present,
    falling back to ``TASK_WORKFLOW`` for the ``task`` type. The hook
    fires for any entry whose type has a state_machine override OR is
    the core ``task`` type — so plugins that want this workflow on
    their own types (e.g. ``sw_ticket``) just declare a state_machine.
    """
    if context.get("operation") != "update":
        return entry
    if not hasattr(entry, "entry_type"):
        return entry

    from ..models.task import (
        TASK_WORKFLOW,
        resolve_workflow_for_type,
        validate_status_change,
    )

    # Resolve the workflow for this entry-type. If we're not the core
    # `task` type AND no state_machine is declared, the resolver falls
    # back to TASK_WORKFLOW; gate on whether this is the task type or
    # the type has explicit state_machine config to avoid firing on
    # arbitrary types that happen to have a `status` field.
    kb_schema = context.get("kb_schema")
    workflow = resolve_workflow_for_type(entry.entry_type, kb_schema)
    is_task_or_opted_in = (
        entry.entry_type == "task"
        or (workflow is not TASK_WORKFLOW)  # type opted in via state_machine
    )
    if not is_task_or_opted_in:
        return entry

    old_status = context.get("old_status")
    new_status = getattr(entry, "status", None)
    if not old_status or not new_status or old_status == new_status:
        return entry

    # The reason field carries through frontmatter as `status_reason`; an
    # absent attribute is treated as empty string.
    status_reason = getattr(entry, "status_reason", "") or ""

    ok, err = validate_status_change(
        workflow,
        old_status=old_status,
        new_status=new_status,
        status_reason=status_reason,
        user_role="write",
    )
    if not ok:
        # Under strict mode, preserve the task-specific guidance suffix
        # (empirically helpful in conductor logs). Under relaxed mode,
        # the validator's message is already specific to the rejection
        # cause (missing reason, status not in states); don't pile on.
        if workflow.get("enforce_transitions", True):
            # Strip the "Cannot move from..." prefix from validate_status_change
            # so we can substitute the task-specific one.
            allowed_msg = (
                err.split("Allowed next:", 1)[-1].strip() if "Allowed next:" in err else ""
            )
            raise ValidationError(
                f"Cannot move task from '{old_status}' to '{new_status}'. "
                f"Allowed next: {allowed_msg} "
                f"Tasks follow open → claimed → in_progress → done/failed/blocked/review; "
                f"walk through the intermediate states rather than skipping "
                f"(or use 'cancelled' to retire an obsolete task from any state)."
            )
        raise ValidationError(err)

    return entry


def _parent_rollup(entry: Entry, context: dict) -> Entry:
    """Auto-complete parent when all Parentable children reach terminal status."""
    from ..models.task import TASK_RESOLVED_STATUSES

    if not hasattr(entry, "entry_type"):
        return entry
    # Trigger on any *resolved* terminal state (done or cancelled) so an
    # all-resolved parent rolls up even when some children were cancelled.
    if getattr(entry, "status", "") not in TASK_RESOLVED_STATUSES:
        return entry

    parent_id = getattr(entry, "parent", "")
    if not parent_id:
        return entry

    kb_name = context.get("kb_name", "")
    if not kb_name:
        return entry

    try:
        config = context.get("config")
        db = context.get("db")
        if not config or not db:
            return entry

        svc = TaskService(config, db)
        svc.rollup_parent(parent_id, kb_name)
    except Exception as e:
        logger.warning("Parent rollup failed for %s: %s", parent_id, e)

    return entry


def register_task_hooks(runner: HookRunner) -> None:
    """Register the task-system core hooks on a HookRunner.

    Called by KBService at construction time. Keeping the registration
    explicit (rather than auto-registering at module import) means tests can
    build a clean HookRunner and opt into task hooks deliberately, and the
    next-extracted service has a worked example to follow.
    """
    runner.register_core_hook("before_save", _task_validate_transition)
    runner.register_core_hook("after_save", _parent_rollup)
