"""Task service — operative task operations wrapping KBService."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from ..config import PyriteConfig
from ..exceptions import (
    AmbiguousEntryError,
    EntryNotFoundError,
    PyriteError,
    ValidationError,
    public_text,
)
from ..models.task import TASK_RESOLVED_STATUSES, coerce_task_priority
from ..models.task_completion import derive_completion, no_derived
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
    atomic operations: claim, decompose, checkpoint. A parent's completion
    is derived from its children (``derived_completion``), never written.
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
        due_date: str = "",
        links: list[Any] | None = None,
        *,
        # Legacy alias
        parent_task: str = "",
    ) -> dict[str, Any]:
        """Create a new task entry.

        ``fields`` carries any additional key a KB's schema requires or
        allows for ``task`` beyond the parameters above (#397) -- e.g. the
        desk schema's ``project``/``kind``, without which a KB requiring
        them refuses every task this command tries to create. The CLI and
        MCP refuse a key that already has its own parameter. A field the
        task manages (`TaskEntry.managed_fields`: the audit trail) or Pyrite
        manages is refused here too, at every spelling, by `KBService.create`
        (#1010); a caller cannot set it through `fields`.

        Returns:
            Dict with created=True and entry details.
        """
        from ..schema import generate_entry_id

        # Support legacy parent_task parameter
        parent = parent or parent_task

        if due_date is not None and due_date != "":
            from ..schema.validators import validate_date

            if not isinstance(due_date, str) or not validate_date(due_date):
                message = f"Invalid due_date '{due_date}': must be YYYY-MM-DD"
                raise ValidationError(
                    message,
                    errors=[
                        {
                            "field": "due_date",
                            "rule": "format",
                            "expected": "YYYY-MM-DD",
                            "got": due_date,
                        }
                    ],
                )

        parsed_links = []
        if links is not None:
            if not isinstance(links, list):
                raise ValidationError(
                    "links must be a list of {target, relation} objects",
                    errors=[{"field": "links", "rule": "type", "expected": "list"}],
                )
            from .kb_service import check_link_spec

            allowed_keys = {"target", "relation"}
            for idx, link_item in enumerate(links):
                if not isinstance(link_item, dict):
                    raise ValidationError(
                        f"links[{idx}] must be a dictionary, got {type(link_item).__name__}",
                        errors=[{"field": f"links[{idx}]", "rule": "type", "expected": "dict"}],
                    )
                extra = set(link_item.keys()) - allowed_keys
                if extra:
                    invalid_key = sorted(extra)[0]
                    raise ValidationError(
                        f"links[{idx}] has undeclared key '{invalid_key}': only 'target' and 'relation' are accepted",
                        errors=[
                            {
                                "field": f"links[{idx}].{invalid_key}",
                                "rule": "undeclared",
                                "message": f"undeclared key '{invalid_key}'",
                            }
                        ],
                    )
                t = link_item.get("target")
                rel = link_item.get("relation")
                target, relation, _ = check_link_spec(t, rel)
                parsed_links.append({"target": target, "relation": relation})

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
        if due_date:
            kwargs["due_date"] = due_date
        if fields:
            kwargs.update(fields)

        # `create`, not `create_entry`: the write's schema warnings (an
        # off-list value in a KB with `enforce_enums: false`, #555) are part
        # of the answer, not dropped.
        written = self.kb_svc.create(
            kb_name,
            {**kwargs, "id": entry_id, "title": title, "entry_type": "task", "body": body},
        )
        entry = written.entry

        linked: list[dict[str, Any]] = []
        failed_links: list[dict[str, Any]] = []
        for l in parsed_links:
            target = l["target"]
            relation = l["relation"]
            try:
                self.kb_svc.add_link(
                    entry.id,
                    kb_name,
                    target,
                    relation=relation,
                )
                linked.append({"target": target, "relation": relation})
            except (PyriteError, ValueError) as e:
                failed_links.append(
                    {
                        "target": target,
                        "relation": relation,
                        "error": public_text(e),
                    }
                )

        result: dict[str, Any] = {
            "created": True,
            "entry_id": entry.id,
            "title": entry.title,
            "status": getattr(entry, "status", "open"),
            "priority": getattr(entry, "priority", 5),
            "parent": getattr(entry, "parent", ""),
            "assignee": getattr(entry, "assignee", ""),
            "kb_name": kb_name,
        }
        if getattr(entry, "due_date", ""):
            result["due_date"] = entry.due_date
        if links is not None:
            result["linked"] = linked
            result["failed_links"] = failed_links
        if written.warnings:
            result["warnings"] = written.warnings
        return result

    def update_task(
        self, task_id: str, kb_name: str, *, acting: str | None = None, **updates
    ) -> dict[str, Any]:
        """Update task fields.

        ``acting`` is the signed-in caller's key (``user:<id>``) when there is
        one. A task an account holds can then have its assignee changed --
        taken or cleared -- only by that account; a label assignee, or
        no assignee, may be changed as before.

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

        if acting is not None and "assignee" in updates:
            current = self.db.get_entry(task_id, kb_name) or {}
            holder = current.get("assignee") or ""
            if holder.startswith("user:") and holder != acting:
                from ..exceptions import Forbidden

                raise Forbidden(
                    f"Task '{task_id}' is held by another account; only its holder "
                    "can change its assignee"
                )

        # Capture the prior status so we can record the transition.
        old_status = ""
        audit: dict[str, Any] | None = None
        if comment and "status" in updates:
            from ..storage.repository import KBRepository

            kb_config = self.config.get_kb(kb_name)
            if kb_config:
                existing = KBRepository(kb_config).load(task_id)
                old_status = getattr(existing, "status", "") if existing else ""
                new_status = updates["status"]
                if old_status != new_status:
                    audit = {
                        "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "from": old_status,
                        "to": new_status,
                        "by": by or "operator",
                        "comment": comment,
                    }

        if audit is not None:
            # The audit entry is appended as one item; the earlier entries and
            # every other key keep their bytes (ADR-0042 decision 1; #955 P3b).
            from ..storage.file_operations import Append, ReplaceBody, Set

            ops: list[Any] = [Set(k, v) for k, v in updates.items() if k != "body"]
            if "body" in updates:
                ops.append(ReplaceBody(updates["body"]))
            ops.append(Append("status_change_log", audit))
            entry = self.kb_svc.apply_operations(task_id, kb_name, ops).entry
        else:
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

        kb_config = self.config.require_kb(kb_name, db=self.db)

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
        without ``kb_name`` (or with a blank one) it reads the readable KBs
        only, so a task in a KB the caller cannot read neither answers nor
        hides a readable task with the same id (P-R5). A task id held by more
        than one readable KB is refused with ``AmbiguousEntryError`` (#528),
        never answered by whichever row SQLite returns first; the candidates
        are listed in config order, readable KBs only.
        """
        sql = (
            "SELECT id, title, kb_name, status, assignee, priority, due_date, metadata "
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

        rows = self._query(sql, params)
        if not rows:
            return None
        if len(rows) > 1:
            held = {row["kb_name"] for row in rows}
            order = [kb.name for kb in self.config.all_kbs()]
            candidates = [n for n in order if n in held] + sorted(held - set(order))
            raise AmbiguousEntryError(task_id, candidates)
        return rows[0]

    def derived_completion(self, kb_name: str) -> dict[str, dict[str, Any]]:
        """Every task's derived completion in ``kb_name``, keyed by task id.

        One index read of the KB's rows, then :func:`derive_completion`.
        Readers attach the value under the ``derived`` key, beside the
        file's fields and never among them (ADR-0042 decision 9).
        """
        rows = self._query(
            "SELECT id, entry_type, "
            "COALESCE(status, json_extract(metadata, '$.status')) AS status, "
            "json_extract(metadata, '$.parent') AS parent "
            "FROM entry WHERE kb_name = :kb_name",
            {"kb_name": kb_name},
        )
        return derive_completion(rows)

    def derived_for(self, kb_name: str, task_id: str) -> dict[str, Any]:
        """One task's derived value; a task missing from the index derives nothing."""
        return self.derived_completion(kb_name).get(task_id) or no_derived()

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

        Each row carries ``derived`` (:func:`derive_completion`) beside the
        file's own ``status``. Every ``status`` filter matches
        ``derived.effective_status``: a task whose children are all resolved
        counts as ``done``, so it is not listed as ``open`` or
        ``in_progress`` and is listed as ``done`` (ADR-0042's acceptance; the
        drain check and the web worklist read the ``open`` filter).
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
        # No SQL clause for `status`: the filter matches the effective status
        # (derive_completion), which only the whole KB's rows can decide.
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
            meta = parse_metadata(row.get("metadata"))
            tasks.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "status": row.get("status") or meta.get("status", "open"),
                    "assignee": row.get("assignee") or meta.get("assignee", ""),
                    # The model's reading, not int(): an index row written
                    # before TaskEntry coerced a word still holds `medium`,
                    # and one such row ended the whole listing (#554).
                    "priority": coerce_task_priority(
                        row.get("priority") or meta.get("priority"), row["id"]
                    ),
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

        derived_by_kb: dict[str, dict[str, dict[str, Any]]] = {}
        for t in tasks:
            kb = t["kb_name"]
            if kb not in derived_by_kb:
                derived_by_kb[kb] = self.derived_completion(kb)
            t["derived"] = derived_by_kb[kb].get(t["id"]) or no_derived()
        if status:
            tasks = [t for t in tasks if t["derived"]["effective_status"] == status]

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
                    # A declared field of a task (#1025), read at its own name.
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

    def start_task(self, task_id: str, kb_name: str, assignee: str) -> dict[str, Any]:
        """Claim and start an open task in one write (#1685).

        The decision is ``claim_entry``'s compare-and-set, the one `task
        claim` runs, from ``open`` straight to ``in_progress``; the file write
        passes through ``claimed`` (both edges judged by the workflow) and
        appends one ``status_change_log`` item per edge. A task the same
        assignee already holds as ``claimed`` (a worker that died between
        claim and start) is moved on to ``in_progress`` by the same
        compare-and-set from ``claimed``. Anything else is claim's lost
        claim, unchanged: ``claimed: False``, ``error``, ``current_status``.

        On success the result adds ``task`` (the single-entry read, with its
        body) and ``parent`` (``{id, title, body}`` or None), their bodies
        bounded together by the response budget (json-contracts, "Body
        truncation").
        """
        self._refuse_not_a_task(task_id, kb_name)

        def edge(old: str, new: str):
            return _log_item(old, new, assignee)

        result: dict[str, Any] = self.kb_svc.claim_entry(
            task_id,
            kb_name,
            assignee,
            from_status="open",
            to_status="in_progress",
            via=("claimed",),
            ops=[edge("open", "claimed"), edge("claimed", "in_progress")],
        )
        if not result.get("claimed") and result.get("current_status") == "claimed":
            holder = self._query(
                "SELECT assignee FROM entry WHERE id = :id AND kb_name = :kb_name",
                {"id": task_id, "kb_name": kb_name},
            )
            if holder and (holder[0].get("assignee") or "") == assignee:
                result = self.kb_svc.claim_entry(
                    task_id,
                    kb_name,
                    assignee,
                    from_status="claimed",
                    to_status="in_progress",
                    ops=[edge("claimed", "in_progress")],
                    # Only the holder read above moves it on: every check
                    # the claim passes matches it, so a claim taken in
                    # between is refused, never replaced.
                    holder=assignee,
                )
        if not result.get("claimed"):
            return result
        return {**result, **self._started_context(task_id, kb_name)}

    def _refuse_not_a_task(self, task_id: str, kb_name: str) -> None:
        rows = self._query(
            "SELECT entry_type FROM entry WHERE id = :id AND kb_name = :kb_name",
            {"id": task_id, "kb_name": kb_name},
        )
        if rows and rows[0].get("entry_type") != "task":
            raise ValidationError(
                f"'{task_id}' is a {rows[0].get('entry_type')!r}, not a task: "
                "`task start` and `task finish` move tasks only."
            )

    def _started_context(self, task_id: str, kb_name: str) -> dict[str, Any]:
        """The started task and its parent, as a worker needs them to begin."""
        from .body_bounds import load_body_bounds

        task = self.kb_svc.get_entry(task_id, kb_name, readable_kbs=UNSCOPED) or {}
        parent_id = task.get("parent") or ""
        parent = None
        if parent_id:
            found = self.kb_svc.get_entry(parent_id, kb_name, readable_kbs=UNSCOPED)
            if found:
                parent = {
                    "id": found.get("id"),
                    "title": found.get("title"),
                    "body": found.get("body") or "",
                }
        bounded = load_body_bounds().fill_budget([task, *([parent] if parent else [])])
        return {"task": bounded[0], "parent": bounded[1] if parent else None}

    def finish_task(
        self,
        task_id: str,
        kb_name: str,
        *,
        walk: bool = False,
        reason: str = "",
        by: str = "",
        operator_override: bool = False,
    ) -> dict[str, Any]:
        """Move a held task to ``done`` in one write (#1685).

        Finish requires the caller to hold the task; operators have an
        explicit override. The caller is ``by`` (the surface sets it: the
        signed-in account, or the label a caller with no account names), and
        must be the task's current assignee. A caller who is not, or no caller
        at all, is refused (``Forbidden``) before any write. With
        ``operator_override`` the operator (``acting() is OPERATOR``: the local
        process, an operator key) finishes a task it does not hold; the log
        items record ``by: operator`` and ``override: true``. The override is
        never implied, and anyone but the operator asking for it is refused.
        The write rechecks the holder in the bytes it replaces.

        The path is the workflow's shortest declared one (``status_walk``);
        without ``walk`` only a direct edge is taken and the refusal names
        the path ``walk`` would take. One write sets ``status``,
        ``status_reason`` (when ``reason`` is given) and appends one
        ``status_change_log`` item per edge; the ``before_save`` workflow hook
        judges every edge and refuses unless the path starts at the status
        the file holds when the write reads it. A task nothing holds (the path
        passes through the workflow's initial state) is refused: ``cancelled``
        retires one.
        """
        from ..models.task import resolve_workflow_for_type, status_walk
        from ..storage.file_operations import Set
        from ..storage.repository import KBRepository

        kb_config = self.config.require_kb(kb_name, db=self.db)
        entry = KBRepository(kb_config).load(task_id)
        if not entry:
            raise EntryNotFoundError(f"Task '{task_id}' not found in KB '{kb_name}'")
        if entry.entry_type != "task":
            raise ValidationError(
                f"'{task_id}' is a {entry.entry_type!r}, not a task: "
                "`task finish` moves tasks only."
            )
        holder = getattr(entry, "assignee", "") or ""
        who, override = _finisher(task_id, holder, by, operator_override)
        workflow = resolve_workflow_for_type("task", getattr(kb_config, "kb_schema", None))
        initial = workflow.get("initial", "open")
        target = "done"
        current = getattr(entry, "status", "") or initial
        if current == target:
            raise ValidationError(f"Task '{task_id}' is already '{target}'; nothing to finish.")
        path = status_walk(workflow, current, target)
        if path is None:
            raise ValidationError(
                f"Cannot finish task '{task_id}': its workflow has no path from "
                f"'{current}' to '{target}'."
            )
        if initial in path:
            raise ValidationError(
                f"Cannot finish task '{task_id}' from '{current}': nothing holds it "
                f"(the path to '{target}' passes through '{initial}'). Start it with "
                f"`pyrite task start`, or retire it with `task update --status cancelled`."
            )
        if len(path) > 2 and not walk:
            raise ValidationError(
                f"Cannot move task '{task_id}' from '{current}' to '{target}' in one step. "
                f"`--walk` (MCP `walk: true`) would pass through: {' → '.join(path)}."
            )
        ops: list[Any] = [Set("status", target)]
        if reason:
            ops.append(Set("status_reason", reason))
        ops.extend(
            _log_item(old, new, who, reason, override=override)
            for old, new in zip(path, path[1:], strict=False)
        )
        written = self.kb_svc.apply_operations(
            task_id, kb_name, ops, status_path=path, expect_holder=holder
        )
        return {
            "finished": True,
            "task_id": task_id,
            "status": getattr(written.entry, "status", target),
            "path": path,
            "status_reason": getattr(written.entry, "status_reason", "") or "",
            "by": who,
            "override": override,
        }

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

        kb_config = self.config.require_kb(kb_name, db=self.db)

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

        kb_config = self.config.require_kb(kb_name, db=self.db)

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
            meta = parse_metadata(row.get("metadata"))
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

        meta = parse_metadata(task.get("metadata") or {})
        parent_id = meta.get("parent") or task.get("parent", "")
        if not parent_id:
            return None

        child_evidence = meta.get("evidence", []) or task.get("evidence", []) or []
        if not child_evidence:
            return None

        parent = self.get_task(parent_id, kb_name, readable_kbs=UNSCOPED)
        if not parent:
            return None

        parent_meta = parse_metadata(parent.get("metadata") or {})
        parent_evidence = parent_meta.get("evidence", []) or parent.get("evidence", []) or []

        # Only what the parent lacks, each appended as one item in the
        # child's order; the parent's own items keep their bytes (#955 P3b).
        from ..storage.file_operations import Append

        missing = [e for e in dict.fromkeys(child_evidence) if e not in parent_evidence]
        if not missing:
            return None  # Nothing new to add

        self.kb_svc.apply_operations(parent_id, kb_name, [Append("evidence", e) for e in missing])
        return {
            "parent_id": parent_id,
            "evidence_added": len(missing),
            "total_evidence": len(parent_evidence) + len(missing),
        }

    def list_entries_needing_qa(self, kb_name: str | None = None) -> list[dict[str, Any]]:
        """Find entries that have open/unclaimed QA validation tasks.

        Returns entries (not tasks) that need QA review.
        """
        from ..storage import effective_status

        params: dict[str, Any] = {}
        # The QA task's effective status: one done by its children is not open.
        keys = effective_status.derived_done_keys(self.db.session, kb_name)
        t_status = effective_status.status_sql("t.", keys, params)
        query = f"""SELECT DISTINCT e.id, e.title, e.kb_name, e.entry_type
                   FROM entry t
                   JOIN entry e ON json_extract(t.metadata, '$.target_entry') = e.id
                                AND t.kb_name = e.kb_name
                   WHERE t.entry_type = 'task'
                   AND {t_status} IN ('open', NULL)
                   AND (t.assignee IS NULL OR t.assignee = '')
                   AND json_extract(t.metadata, '$.task_type') = 'qa_validation'"""
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

        meta = parse_metadata(task.get("metadata") or {})
        evidence = meta.get("evidence", []) or task.get("evidence", []) or []

        if assessment_id not in evidence:
            from ..storage.file_operations import Append

            evidence.append(assessment_id)
            # One appended item; the other items keep their bytes (#955 P3b).
            self.kb_svc.apply_operations(task_id, kb_name, [Append("evidence", assessment_id)])

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

        def _children(parent_id: str) -> list[dict[str, Any]]:
            return self._query(
                """SELECT id, title, status, entry_type,
                          json_extract(metadata, '$.parent') as parent,
                          json_extract(metadata, '$.assignee') as assignee,
                          importance as priority
                   FROM entry
                   WHERE kb_name = :kb_name AND entry_type = 'task'
                   AND json_extract(metadata, '$.parent') = :parent_id""",
                {"kb_name": kb_name, "parent_id": parent_id},
            )

        # Depth-first pre-order (the order the recursive version returned)
        # with an explicit stack: any depth of chain is a valid KB shape and
        # must not raise RecursionError.
        stack = list(reversed(_children(task_id)))
        while stack:
            child = stack.pop()
            cid = child["id"]
            if cid in visited:
                continue
            visited.add(cid)
            result.append(child)
            stack.extend(reversed(_children(cid)))
        derived = self.derived_completion(kb_name) if result else {}
        for node in result:
            node["derived"] = derived.get(node["id"]) or no_derived()
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

            meta = parse_metadata(task.get("metadata") or {})
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

        derived = self.derived_completion(kb_name) if result else {}
        for node in result:
            node["derived"] = derived.get(node["id"]) or no_derived()
        return result

    def get_blocked_by(self, task_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Get unresolved tasks in the transitive dependency chain."""
        derived = self.derived_completion(kb_name)
        root = self.get_task(task_id, kb_name, readable_kbs=UNSCOPED)
        if not root or _effective_status(task_id, derived) in TASK_RESOLVED_STATUSES:
            return []

        result: list[dict[str, Any]] = []
        visited = {task_id}
        stack = [(root, iter(_task_dependency_ids(root)))]

        # Explicit iterator frames preserve the recursive pre-order while
        # keeping both the call stack and dependency queries bounded by depth.
        while stack:
            current, dependencies = stack[-1]
            try:
                dep_id = next(dependencies)
            except StopIteration:
                stack.pop()
                continue

            if dep_id in visited:
                continue
            visited.add(dep_id)
            dep = self.get_task(dep_id, kb_name, readable_kbs=UNSCOPED)
            if not dep or _effective_status(dep_id, derived) in TASK_RESOLVED_STATUSES:
                continue

            result.append(_task_dependency_summary(dep, derived))
            stack.append((dep, iter(_task_dependency_ids(dep))))

        return result

    def critical_path(self, task_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Find the longest chain of unresolved dependencies iteratively.

        Returns dependency tasks in order, excluding resolved tasks. A
        post-order DFS with memoization preserves the longest path through a
        shared DAG, while active-path detection bounds cycles.
        """
        derived = self.derived_completion(kb_name)
        root = self.get_task(task_id, kb_name, readable_kbs=UNSCOPED)
        if not root or _effective_status(task_id, derived) in TASK_RESOLVED_STATUSES:
            return []

        tasks = {task_id: root}
        dependencies_by_task = {task_id: _task_dependency_ids(root)}
        longest_length: dict[str, int] = {}
        next_dependency: dict[str, str | None] = {}
        active = {task_id}
        frames = [(task_id, iter(dependencies_by_task[task_id]))]

        while frames:
            current_id, dependencies = frames[-1]
            try:
                dep_id = next(dependencies)
            except StopIteration:
                best_id = None
                best_length = 0
                for candidate_id in dependencies_by_task[current_id]:
                    if (
                        candidate_id not in longest_length
                        or _effective_status(candidate_id, derived) in TASK_RESOLVED_STATUSES
                    ):
                        continue
                    candidate_length = 1 + longest_length[candidate_id]
                    if candidate_length > best_length:
                        best_id = candidate_id
                        best_length = candidate_length
                longest_length[current_id] = best_length
                next_dependency[current_id] = best_id
                active.remove(current_id)
                frames.pop()
                continue

            if (
                dep_id in active
                or dep_id in longest_length
                or _effective_status(dep_id, derived) in TASK_RESOLVED_STATUSES
            ):
                continue

            dep = self.get_task(dep_id, kb_name, readable_kbs=UNSCOPED)
            if not dep:
                continue
            tasks[dep_id] = dep
            dependencies_by_task[dep_id] = _task_dependency_ids(dep)
            active.add(dep_id)
            frames.append((dep_id, iter(dependencies_by_task[dep_id])))

        result: list[dict[str, Any]] = []
        next_id = next_dependency[task_id]
        while next_id is not None:
            result.append(_task_dependency_summary(tasks[next_id], derived))
            next_id = next_dependency[next_id]
        return result


def _finisher(task_id: str, holder: str, by: str, operator_override: bool) -> tuple[str, bool]:
    """Who finishes the task and whether by override: finish requires the
    caller to hold the task; operators have an explicit override."""
    from ..exceptions import Forbidden
    from .acting import OPERATOR, acting

    if by and holder and by == holder:
        return by, False
    if operator_override:
        if acting() is not OPERATOR:
            raise Forbidden(
                f"Task '{task_id}': only the operator may finish a task it does not hold "
                "(the operator override). Nothing was written."
            )
        return "operator", True
    who = f"'{by}'" if by else "a caller with no identity"
    held = f"held by '{holder}'" if holder else "held by no one"
    refusal = Forbidden(
        f"Task '{task_id}' is {held}: finish requires the caller to hold the task, "
        f"and {who} does not. Nothing was written."
    )
    # `suggestion` is a public attribute every refusal may carry
    # (exceptions.PUBLIC_ATTRIBUTES); Forbidden does not declare it.
    cast(Any, refusal).suggestion = (
        "Finish it as its holder (`--by <holder>`; MCP: sign in as the holder or pass `by`), "
        "or, as the operator, pass the explicit override (`--operator-override`; "
        "MCP `operator_override: true`)."
    )
    raise refusal


def _log_item(old: str, new: str, by: str, comment: str = "", *, override: bool = False):
    """One ``status_change_log`` item for the edge ``old`` -> ``new``, as an
    ``Append`` (the earlier items keep their bytes). ``comment`` only when
    there is one: nothing is written that was not asked for."""
    from ..storage.file_operations import Append

    item: dict[str, Any] = {
        "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "from": old,
        "to": new,
        "by": by,
    }
    if comment:
        item["comment"] = comment
    if override:
        item["override"] = True
    return Append("status_change_log", item)


def _task_dependency_ids(task: dict[str, Any]) -> list[str]:
    """Return a task's dependency IDs in their stored order."""
    metadata = parse_metadata(task.get("metadata") or {})
    dependencies = metadata.get("dependencies") or task.get("dependencies") or []
    if isinstance(dependencies, str):
        return [dependencies] if dependencies else []
    if not isinstance(dependencies, list | tuple):
        return []
    return [dependency for dependency in dependencies if isinstance(dependency, str)]


def _effective_status(task_id: str, derived: dict[str, dict[str, Any]]) -> str:
    """Return the status task readers should use for dependency decisions."""
    return str((derived.get(task_id) or no_derived()).get("effective_status", "open"))


def _task_dependency_summary(
    task: dict[str, Any], derived: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Build the read-only row returned by dependency tools."""
    task_id = task["id"]
    return {
        "id": task_id,
        "title": task.get("title", ""),
        "status": task.get("status", ""),
        "entry_type": task.get("entry_type", "task"),
        "derived": derived.get(task_id) or no_derived(),
    }


# =============================================================================
# Core hook — moved from kb_service.py in extract-hookrunner-from-kb-service
# step 3. A platform-level lifecycle hook specific to tasks (transition
# validation). It lives here because it belongs with task semantics, not with
# generic CRUD. KBService registers it on its HookRunner at startup via
# register_task_hooks().
#
# It only refuses (kb/design.md principle 4: a hook may refuse a write, it may
# not change one). The parent rollup that used to sit beside it wrote the
# parent's file; it is replaced by derive_completion
# (pyrite/models/task_completion.py).
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

    from ..models.task import TASK_WORKFLOW, resolve_workflow_for_type

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
    status_path = context.get("status_path")
    if status_path is not None:
        # A multi-step change (`task start`, `task finish --walk`; #1685):
        # every edge of the path is judged below, as one change each, and the
        # path must start at the status the file holds now (the file decides,
        # ADR-0042 decision 10) and end at the one written. No fail-open here.
        path = list(status_path)
        if len(path) < 2 or path[0] != old_status or path[-1] != new_status:
            raise ValidationError(
                f"Cannot move '{entry.id}' along {' → '.join(map(str, path))}: "
                f"the file's status is '{old_status}', and this write sets '{new_status}'. "
                "Nothing was written; read the task again and retry."
            )
        for before, after in zip(path, path[1:], strict=False):
            _judge_status_edge(entry, context, workflow, before, after)
        return entry
    # Fails OPEN on a missing old status. That is safe only because every
    # update hands this hook the old status of a fully loaded file: a file its
    # type cannot load is refused before any hook (ADR-0042 8a), never given
    # a context without one (#1311 round 2 did, and `open -> done` landed).
    # tests/test_unloadable_file_is_never_written.py pins it.
    if not old_status or not new_status or old_status == new_status:
        return entry

    # A task already complete by its children can record that fact explicitly.
    # Keep this out of TASK_WORKFLOW: plugin types also fall back to that table.
    if entry.entry_type == "task" and old_status == "open" and new_status == "done":
        completion = TaskService(context["config"], context["db"]).derived_for(
            context["kb_name"], entry.id
        )["completion"]
        if completion.get("complete") is True and completion.get("basis") == "children":
            return entry

    _judge_status_edge(entry, context, workflow, old_status, new_status)
    return entry


def _judge_status_edge(
    entry: Entry, context: dict, workflow: dict, old_status: str, new_status: str
) -> None:
    """Refuse one status edge ``old_status`` -> ``new_status`` that ``workflow``
    does not allow, through ``validate_status_change`` (the one judge)."""
    from ..models.task import validate_status_change

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
            refusal = ValidationError(
                f"Cannot move task from '{old_status}' to '{new_status}'. "
                f"Allowed next: {allowed_msg} "
                f"Tasks follow open → claimed → in_progress → done/failed/blocked/review; "
                f"walk through the intermediate states rather than skipping "
                f"(or use 'cancelled' to retire an obsolete task from any unfinished state)."
            )
            if (
                entry.entry_type == "task"
                and new_status == "open"
                and old_status in {"in_progress", "blocked"}
            ):
                refusal.suggestion = (
                    "Use `pyrite task reset` to release a stale claim with an audit trail."
                )
            raise refusal
        raise ValidationError(err)


def register_task_hooks(runner: HookRunner) -> None:
    """Register the task-system core hooks on a HookRunner.

    Called by KBService at construction time. Keeping the registration
    explicit (rather than auto-registering at module import) means tests can
    build a clean HookRunner and opt into task hooks deliberately, and the
    next-extracted service has a worked example to follow.
    """
    runner.register_core_hook("before_save", _task_validate_transition)
    # No after_save hook: a parent's completion is derived at read time
    # (derive_completion), never written into the parent's file (ADR-0042
    # decision 4). tests/test_after_save_hooks_write_no_other_entry.py fails
    # if any after_save hook writes another entry.
