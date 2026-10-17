"""
QA Service — structural validation and assessment for knowledge bases.

Orchestrates QA operations by delegating to focused services:
- Validation and assessment: handled directly (tightly coupled)
- Auto-fix: delegated to QAFixService
- Gap analysis / staleness: delegated to QAAnalyticsService
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from ..config import PyriteConfig
from ..exceptions import (
    AINotConfiguredError,
    EntryNotFoundError,
    EvaluationFailedError,
    InvalidQATierError,
    KBUnreadableError,
    PyriteError,
)
from ..plugins.registry import get_registry
from ..schema import validate_date, validate_importance
from ..schema.core_types import SYSTEM_INTENT, resolve_type_metadata
from ..storage.backends.base_backend import kb_names_clause
from ..storage.database import PyriteDB
from ..utils.metadata import parse_metadata

logger = logging.getLogger(__name__)

#: The tiers QA runs: 1 = structural and deterministic rubric checks, 2 = tier
#: 1 plus the model-judged rubric. Every surface that takes a tier refuses any
#: other value (``InvalidQATierError``): a record never names a tier no check
#: stands behind (#795). `qa_assessment`'s schema mentions a tier 3 that
#: nothing implements.
QA_TIERS = (1, 2)


class QAService:
    """Structural quality assurance for knowledge bases."""

    def __init__(self, config: PyriteConfig, db: PyriteDB, llm_service=None, kb_svc=None):
        self.config = config
        self.db = db
        self._llm_service = llm_service
        self._llm_evaluator = None
        self._fix_service = None
        self._analytics_service = None
        self._kb_svc_injected = kb_svc

    @property
    def _fix_svc(self):
        """Lazy-init QAFixService."""
        if getattr(self, "_fix_service", None) is None:
            from .qa_fix_service import QAFixService

            self._fix_service = QAFixService(
                getattr(self, "config", None), getattr(self, "db", None)
            )
        return self._fix_service

    @property
    def _analytics_svc(self):
        """Lazy-init QAAnalyticsService."""
        if getattr(self, "_analytics_service", None) is None:
            from .qa_analytics_service import QAAnalyticsService

            self._analytics_service = QAAnalyticsService(
                getattr(self, "config", None), getattr(self, "db", None)
            )
        return self._analytics_service

    @property
    def llm_evaluator(self):
        """Lazy-init LLM rubric evaluator."""
        if self._llm_evaluator is None and self._llm_service is not None:
            from .llm_rubric_evaluator import LLMRubricEvaluator

            self._llm_evaluator = LLMRubricEvaluator(self._llm_service)
        return self._llm_evaluator

    @property
    def llm_available(self) -> bool:
        """Whether LLM evaluation is available."""
        return self.llm_evaluator is not None and self.llm_evaluator.is_available()

    # =========================================================================
    # Public API
    # =========================================================================

    def validate_entry(
        self, entry_id: str, kb_name: str, *, readable_kbs: set[str] | None
    ) -> dict[str, Any]:
        """Validate a single entry. Returns {entry_id, kb_name, issues: [...]}.

        ``readable_kbs`` is the caller's ``ReadScope`` set (``None``:
        unscoped): link checks then treat a target the caller cannot read
        exactly as a missing one (P-R5).
        """
        issues: list[dict[str, Any]] = []

        rows = self.db.execute_sql(
            "SELECT id, kb_name, entry_type, title, date, importance, body, status, metadata "
            "FROM entry WHERE id = :entry_id AND kb_name = :kb_name",
            {"entry_id": entry_id, "kb_name": kb_name},
        )

        if not rows:
            return {
                "entry_id": entry_id,
                "kb_name": kb_name,
                "issues": [
                    {
                        "entry_id": entry_id,
                        "kb_name": kb_name,
                        "rule": "entry_not_found",
                        "severity": "error",
                        "field": "id",
                        "message": f"Entry '{entry_id}' not found in KB '{kb_name}'",
                    }
                ],
            }

        entry = rows[0]
        self._check_entry_fields(entry, issues)
        self._check_entry_links(entry_id, kb_name, issues, readable_kbs=readable_kbs)
        # The checks below read the KB's folder (its files, its kb.yaml).
        if not self._report_unreadable_kb(issues, kb_name):
            sources = self._check_file_values(issues, kb_name, entry_id=entry_id)
            self._check_schema_validation(entry, issues, sources.get(entry_id))
            self._check_rubric_evaluation(entry, issues)

        return {"entry_id": entry_id, "kb_name": kb_name, "issues": issues}

    def validate_kb(
        self,
        kb_name: str,
        check_staleness: bool = False,
        staleness_days: int = 90,
        *,
        readable_kbs: set[str] | None,
        tier: int = 1,
    ) -> dict[str, Any]:
        """Validate all entries in a KB. Returns {kb_name, total, checked, issues: [...]}.

        ``readable_kbs`` bounds the link checks (P-R4, P-R5): a target the
        caller cannot read is reported as a missing one, and a link from a
        source it cannot read does not stop an entry being an orphan.

        ``tier`` 2 adds the model-judged rubric, one model call per entry
        whose type has judgment items, through the evaluator `assess_entry`
        uses (#795). Refused with no model configured; an entry the model did
        not answer for is an ``evaluation_failed`` error, never a pass.
        """
        self.require_tier(tier)
        issues: list[dict[str, Any]] = []

        # Count entries
        total_rows = self.db.execute_sql(
            "SELECT COUNT(*) as cnt FROM entry WHERE kb_name = :kb_name", {"kb_name": kb_name}
        )
        total = total_rows[0]["cnt"] if total_rows else 0

        # Bulk SQL pass — fast structural checks
        self._check_missing_titles(issues, kb_name)
        self._check_empty_bodies(issues, kb_name)
        self._check_events_missing_dates(issues, kb_name)
        self._check_invalid_dates(issues, kb_name)
        self._check_importance_range(issues, kb_name)
        self._check_broken_links(issues, kb_name, readable_kbs=readable_kbs)
        self._check_orphans(issues, kb_name, readable_kbs=readable_kbs)

        # Rubric evaluation pass (index only)
        self._check_rubric_all(issues, kb_name)

        # Everything below reads the KB's folder: its files, its kb.yaml. A
        # folder that is missing or unreadable is one error for the KB, and
        # those checks are skipped, never passed or crashed (#950 round 2).
        if not self._report_unreadable_kb(issues, kb_name, tier=tier):
            # Per-file pass: what the index cannot show, because it keeps only
            # Pyrite's reading -- reserved keys the KB uses for fields of its own
            # (#557), a task priority read from a word (#554)
            sources = self._check_file_values(issues, kb_name)

            # Per-entry schema pass (only if kb.yaml exists): the kb.yaml itself,
            # then each entry, its typed attributes read from its file
            self._check_schema_retypes_core_fields(issues, kb_name)
            self._check_schema_all(issues, kb_name, sources)

            if tier >= 2:
                self._check_llm_rubric_all(issues, kb_name)

        # Optional staleness check (delegated to analytics service)
        if check_staleness:
            self._analytics_svc._check_staleness(issues, kb_name, staleness_days)

        return {
            "kb_name": kb_name,
            "total": total,
            "checked": total,
            "issues": issues,
        }

    def _report_unreadable_kb(
        self, issues: list[dict[str, Any]], kb_name: str, *, tier: int = 1
    ) -> bool:
        """Add one ``kb_unreadable`` error when the KB's folder cannot be read.

        Returns True when it did: the caller then skips every check that
        reads the folder. A KB that resolves to no config is not judged here
        (#959).
        """
        problem = self._kb_folder_problem(kb_name)
        if problem is None:
            return False
        skipped = "file, schema and kb.yaml checks" + (
            " and the tier 2 rubric" if tier >= 2 else ""
        )
        issues.append(
            {
                "entry_id": "",
                "kb_name": kb_name,
                "rule": "kb_unreadable",
                "severity": "error",
                "field": "path",
                "message": f"KB '{kb_name}': {problem}, so its {skipped} did not run",
            }
        )
        return True

    def _require_readable_kb(self, kb_name: str) -> None:
        """Refuse to assess a KB whose folder cannot be read (``KB_UNREADABLE``).

        An assessment is a record written into the KB: a folder that is not
        there, or cannot be read, can hold none, and writing one would
        recreate a missing folder (#950 round 2).
        """
        problem = self._kb_folder_problem(kb_name)
        if problem is not None:
            raise KBUnreadableError(f"KB '{kb_name}': {problem}; nothing was assessed")

    def _kb_folder_problem(self, kb_name: str) -> str | None:
        """Why the KB's folder cannot be read, or None when it can.

        Names no path: the message can reach an MCP or REST caller.
        """
        import os
        from pathlib import Path

        kb_config = self.config.get_kb(kb_name)
        if kb_config is None:
            return None
        path = Path(kb_config.path)
        try:
            if not path.exists():
                return "its folder is missing"
            if not path.is_dir():
                return "its path is not a folder"
            with os.scandir(path) as listing:
                next(listing, None)
            kb_yaml = path / "kb.yaml"
            if kb_yaml.exists():
                kb_yaml.open("rb").close()
        except OSError as exc:
            return f"its folder cannot be read ({exc.strerror or type(exc).__name__})"
        return None

    def validate_all(
        self,
        kb_names: set[str] | list[str] | None = None,
        *,
        readable_kbs: set[str] | None,
        tier: int = 1,
    ) -> dict[str, Any]:
        """Validate all KBs. Returns {kbs: [{kb_name, total, checked, issues}]}.

        ``kb_names`` restricts the sweep to those KBs -- the caller's
        readable set. A KB left out contributes neither issues nor its
        name, so the aggregate totals in ``get_status`` are computed over
        readable rows only. ``readable_kbs`` bounds each KB's link checks
        (see `validate_kb`, which also says what ``tier`` does).
        """
        self.require_tier(tier)
        kbs = []
        for kb in self.config.all_kbs():
            if kb_names is not None and kb.name not in kb_names:
                continue
            result = self.validate_kb(kb.name, readable_kbs=readable_kbs, tier=tier)
            kbs.append(result)
        return {"kbs": kbs}

    def get_status(
        self,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
        *,
        readable_kbs: set[str] | None,
    ) -> dict[str, Any]:
        """Get QA status dashboard; ``readable_kbs`` bounds the link checks."""
        if kb_name:
            result = self.validate_kb(kb_name, readable_kbs=readable_kbs)
            issues = result["issues"]
            total_entries = result["total"]
        else:
            all_result = self.validate_all(kb_names=kb_names, readable_kbs=readable_kbs)
            issues = []
            total_entries = 0
            for kb in all_result["kbs"]:
                issues.extend(kb["issues"])
                total_entries += kb["total"]

        # Aggregate
        issues_by_severity: dict[str, int] = {}
        issues_by_rule: dict[str, int] = {}
        for issue in issues:
            sev = issue.get("severity", "info")
            rule = issue.get("rule", "unknown")
            issues_by_severity[sev] = issues_by_severity.get(sev, 0) + 1
            issues_by_rule[rule] = issues_by_rule.get(rule, 0) + 1

        return {
            "total_entries": total_entries,
            "total_issues": len(issues),
            "issues_by_severity": issues_by_severity,
            "issues_by_rule": issues_by_rule,
        }

    def require_tier(self, tier: int) -> None:
        """Refuse a tier QA does not run, or tier 2 with no model to run it.

        Called before anything is checked or written, so a refused tier
        leaves no record (#795, maintainer decision 2026-10-15).
        """
        if isinstance(tier, bool) or tier not in QA_TIERS:
            raise InvalidQATierError(
                f"QA tier {tier!r} does not exist; choose one of "
                f"{', '.join(str(t) for t in QA_TIERS)}"
            )
        if tier >= 2 and not self.llm_available:
            raise AINotConfiguredError(
                "QA tier 2 runs the model-judged rubric and no model is configured; "
                "nothing was assessed"
            )

    # =========================================================================
    # Assessment API
    # =========================================================================

    def _get_kb_service(self):
        """Lazy-load KBService for creating assessment entries."""
        if self._kb_svc_injected is not None:
            return self._kb_svc_injected
        if not hasattr(self, "_kb_svc"):
            from .kb_service import KBService

            self._kb_svc = KBService(self.config, self.db)
        return self._kb_svc

    def assess_entry(
        self,
        entry_id: str,
        kb_name: str,
        tier: int = 1,
        create_task_on_fail: bool = False,
        *,
        readable_kbs: set[str] | None,
    ) -> dict[str, Any]:
        """Assess a single entry and create a qa_assessment entry.

        ``readable_kbs`` bounds the link checks, as in `validate_entry`.

        Returns dict with assessment_id, qa_status, issues_found, issues,
        llm_available.

        Tier 1: structural validation + deterministic rubric checks.
        Tier 2: tier 1 + LLM judgment rubric evaluation.

        A tier that does not run writes no record (#795): an unknown tier
        raises ``InvalidQATierError``, tier 2 with no model
        ``AINotConfiguredError``, and a model that did not answer
        ``EvaluationFailedError``, each before anything is written. With
        ``create_task_on_fail``, a task that could not be created comes back
        as ``task_failed: {entry_id, reason}``.
        """
        self.require_tier(tier)
        self._require_readable_kb(kb_name)
        result = self.validate_entry(entry_id, kb_name, readable_kbs=readable_kbs)
        issues = result["issues"]

        # Tier 2: LLM rubric evaluation, before anything is written
        if tier >= 2:
            llm_issues = self._evaluate_llm_rubric(entry_id, kb_name)
            issues.extend(llm_issues)

        errors = [i for i in issues if i.get("severity") == "error"]
        warnings = [i for i in issues if i.get("severity") == "warning"]

        if errors:
            qa_status = "fail"
        elif warnings:
            qa_status = "warn"
        else:
            qa_status = "pass"

        now = datetime.now(UTC).isoformat()
        ms_ts = int(time.time() * 1000)
        assessment_id = f"qa-{entry_id}-{ms_ts}"[:80]

        body = self._format_assessment_body(entry_id, kb_name, qa_status, issues)

        svc = self._get_kb_service()
        svc.create_entry(
            kb_name=kb_name,
            entry_id=assessment_id,
            title=f"QA: {entry_id}",
            entry_type="qa_assessment",
            body=body,
            target_entry=entry_id,
            target_kb=kb_name,
            tier=tier,
            qa_status=qa_status,
            issues=issues,
            issues_found=len(issues),
            issues_resolved=0,
            assessed_at=now,
            tags=["qa", f"qa-{qa_status}"],
        )

        task_failed = None
        if create_task_on_fail and qa_status == "fail":
            task_failed = self._maybe_create_task(entry_id, kb_name, assessment_id, issues)

        out = {
            "assessment_id": assessment_id,
            "target_entry": entry_id,
            "kb_name": kb_name,
            "qa_status": qa_status,
            "issues_found": len(issues),
            "issues": issues,
            "llm_available": self.llm_available,
        }
        if task_failed is not None:
            out["task_failed"] = task_failed
        return out

    def assess_kb(
        self,
        kb_name: str,
        tier: int = 1,
        max_age_hours: int = 24,
        create_task_on_fail: bool = False,
        *,
        readable_kbs: set[str] | None,
    ) -> dict[str, Any]:
        """Assess all entries in a KB (skipping qa_assessment entries and recently assessed).

        ``readable_kbs`` bounds each entry's link checks, as in `validate_entry`.

        Returns dict with kb_name, assessed count, skipped count, results list,
        ``failed`` (each entry that was not assessed: ``{entry_id, error_code,
        reason}``; it has no record) and ``tasks_failed`` (tasks
        ``create_task_on_fail`` could not create). One failed entry does not
        stop the rest. A tier refused outright (unknown, or 2 with no model)
        raises before any entry is touched.
        """
        self.require_tier(tier)
        self._require_readable_kb(kb_name)
        rows = self.db.execute_sql(
            "SELECT id, entry_type FROM entry WHERE kb_name = :kb_name",
            {"kb_name": kb_name},
        )

        results = []
        failed: list[dict[str, Any]] = []
        tasks_failed: list[dict[str, Any]] = []
        skipped = 0

        for row in rows:
            eid = row["id"]
            etype = row["entry_type"]

            # Skip assessment entries themselves
            if etype == "qa_assessment":
                skipped += 1
                continue

            # Skip recently assessed
            if max_age_hours > 0 and self._is_recently_assessed(eid, kb_name, max_age_hours):
                skipped += 1
                continue

            try:
                result = self.assess_entry(
                    eid,
                    kb_name,
                    tier=tier,
                    create_task_on_fail=create_task_on_fail,
                    readable_kbs=readable_kbs,
                )
            except Exception as exc:
                # A model that did not answer (EvaluationFailedError, with its
                # reason) or a record whose write raised: the entry has no
                # record, so it is listed, never dropped.
                logger.warning("Assessment of %s failed", eid, exc_info=True)
                failed.append(
                    {
                        "entry_id": eid,
                        "error_code": getattr(exc, "error_code", "INTERNAL_ERROR"),
                        "reason": getattr(exc, "reason", None) or f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            results.append(result)
            if "task_failed" in result:
                tasks_failed.append(result["task_failed"])

        return {
            "kb_name": kb_name,
            "assessed": len(results),
            "skipped": skipped,
            "results": results,
            "failed": failed,
            "tasks_failed": tasks_failed,
        }

    def _format_assessment_body(
        self, entry_id: str, kb_name: str, qa_status: str, issues: list[dict]
    ) -> str:
        """Format assessment body as markdown."""
        lines = [f"Assessment of `{entry_id}` in `{kb_name}`: **{qa_status}**\n"]
        if not issues:
            lines.append("No issues found.")
            return "\n".join(lines)

        lines.append("| Severity | Rule | Field | Message |")
        lines.append("|----------|------|-------|---------|")
        for issue in issues:
            sev = issue.get("severity", "info")
            rule = issue.get("rule", "")
            fld = issue.get("field", "") or ""
            msg = issue.get("message", "")
            lines.append(f"| {sev} | {rule} | {fld} | {msg} |")

        return "\n".join(lines)

    def _is_recently_assessed(self, entry_id: str, kb_name: str, max_age_hours: int) -> bool:
        """Check if entry was assessed within max_age_hours."""
        rows = self.db.execute_sql(
            "SELECT json_extract(metadata, '$.assessed_at') as assessed_at "
            "FROM entry WHERE kb_name = :kb_name AND entry_type = 'qa_assessment' "
            "AND json_extract(metadata, '$.target_entry') = :entry_id "
            "ORDER BY json_extract(metadata, '$.assessed_at') DESC LIMIT 1",
            {"kb_name": kb_name, "entry_id": entry_id},
        )

        if not rows or not rows[0]["assessed_at"]:
            return False

        try:
            assessed = datetime.fromisoformat(rows[0]["assessed_at"])
            age_hours = (datetime.now(UTC) - assessed).total_seconds() / 3600
            return age_hours < max_age_hours
        except (ValueError, TypeError):
            return False

    def _maybe_create_task(
        self, entry_id: str, kb_name: str, assessment_id: str, issues: list[dict]
    ) -> dict[str, Any] | None:
        """Create a task for failed assessment.

        Returns ``None`` when the task was created, else ``{entry_id,
        reason}``: a task that was not created is reported, never only
        logged (#788).
        """
        try:
            from .task_service import TaskService

            task_svc = TaskService(self.config, self.db)
            error_count = sum(1 for i in issues if i.get("severity") == "error")
            task_svc.create_task(
                kb_name=kb_name,
                title=f"Fix QA issues: {entry_id}",
                body=f"Assessment `{assessment_id}` found {error_count} error(s).\n\nSee assessment entry for details.",
                priority=8,
                tags=["qa", "auto-generated"],
            )
        except Exception as e:
            logger.warning("Task creation failed for %s: %s", entry_id, e, exc_info=True)
            return {"entry_id": entry_id, "reason": f"{type(e).__name__}: {e}"}
        return None

    # =========================================================================
    # Query API
    # =========================================================================

    def get_assessments(
        self,
        kb_name: str,
        target_entry: str | None = None,
        qa_status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query assessment entries with optional filters."""
        sql = (
            "SELECT id, title, body, metadata FROM entry "
            "WHERE kb_name = :kb_name AND entry_type = 'qa_assessment'"
        )
        params: dict[str, Any] = {"kb_name": kb_name}

        if target_entry:
            sql += " AND json_extract(metadata, '$.target_entry') = :target_entry"
            params["target_entry"] = target_entry
        if qa_status:
            sql += " AND json_extract(metadata, '$.qa_status') = :qa_status"
            params["qa_status"] = qa_status

        sql += " ORDER BY json_extract(metadata, '$.assessed_at') DESC LIMIT :limit"
        params["limit"] = limit

        rows = self.db.execute_sql(sql, params)
        results = []
        for row in rows:
            meta = parse_metadata(row["metadata"])
            results.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "target_entry": meta.get("target_entry", ""),
                    "target_kb": meta.get("target_kb", ""),
                    "qa_status": meta.get("qa_status", "pass"),
                    "tier": meta.get("tier", 1),
                    "issues_found": meta.get("issues_found", 0),
                    "assessed_at": meta.get("assessed_at", ""),
                }
            )
        return results

    def get_unassessed(self, kb_name: str) -> list[dict[str, Any]]:
        """Get entries that have no assessment."""
        rows = self.db.execute_sql(
            "SELECT e.id, e.entry_type, e.title FROM entry e "
            "WHERE e.kb_name = :kb_name AND e.entry_type != 'qa_assessment' "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM entry a WHERE a.kb_name = :kb_name2 "
            "  AND a.entry_type = 'qa_assessment' "
            "  AND json_extract(a.metadata, '$.target_entry') = e.id"
            ")",
            {"kb_name": kb_name, "kb_name2": kb_name},
        )
        return [
            {"id": row["id"], "entry_type": row["entry_type"], "title": row["title"]}
            for row in rows
        ]

    def get_coverage(self, kb_name: str) -> dict[str, Any]:
        """Get assessment coverage stats for a KB."""
        # Total non-assessment entries
        total_rows = self.db.execute_sql(
            "SELECT COUNT(*) as cnt FROM entry WHERE kb_name = :kb_name AND entry_type != 'qa_assessment'",
            {"kb_name": kb_name},
        )
        total = total_rows[0]["cnt"] if total_rows else 0

        # Entries with at least one assessment
        assessed_rows = self.db.execute_sql(
            "SELECT COUNT(DISTINCT json_extract(metadata, '$.target_entry')) as cnt "
            "FROM entry WHERE kb_name = :kb_name AND entry_type = 'qa_assessment'",
            {"kb_name": kb_name},
        )
        assessed = assessed_rows[0]["cnt"] if assessed_rows else 0

        unassessed = total - assessed

        # Status counts from latest assessments
        by_status: dict[str, int] = {}
        rows = self.db.execute_sql(
            "SELECT json_extract(metadata, '$.qa_status') as qs, COUNT(*) as cnt "
            "FROM entry WHERE kb_name = :kb_name AND entry_type = 'qa_assessment' "
            "GROUP BY qs",
            {"kb_name": kb_name},
        )
        for row in rows:
            status = row["qs"] or "pass"
            by_status[status] = row["cnt"]

        coverage_pct = round((assessed / total * 100), 1) if total > 0 else 0.0

        return {
            "total": total,
            "assessed": assessed,
            "unassessed": unassessed,
            "coverage_pct": coverage_pct,
            "by_status": by_status,
        }

    # =========================================================================
    # Bulk SQL checks
    # =========================================================================

    def _check_missing_titles(
        self, issues: list[dict[str, Any]], kb_name: str | None = None
    ) -> None:
        sql = "SELECT id, kb_name, entry_type FROM entry WHERE (title IS NULL OR title = '')"
        params: dict[str, Any] = {}
        if kb_name:
            sql += " AND kb_name = :kb_name"
            params["kb_name"] = kb_name

        for row in self.db.execute_sql(sql, params):
            issues.append(
                {
                    "entry_id": row["id"],
                    "kb_name": row["kb_name"],
                    "rule": "missing_title",
                    "severity": "error",
                    "field": "title",
                    "message": f"Entry '{row['id']}' has no title",
                }
            )

    def _check_empty_bodies(self, issues: list[dict[str, Any]], kb_name: str | None = None) -> None:
        sql = (
            "SELECT id, kb_name, entry_type, title FROM entry "
            "WHERE (body IS NULL OR body = '') "
            "AND entry_type NOT IN ('collection', 'relationship')"
        )
        params: dict[str, Any] = {}
        if kb_name:
            sql += " AND kb_name = :kb_name"
            params["kb_name"] = kb_name

        for row in self.db.execute_sql(sql, params):
            issues.append(
                {
                    "entry_id": row["id"],
                    "kb_name": row["kb_name"],
                    "rule": "empty_body",
                    "severity": "warning",
                    "field": "body",
                    "message": f"Entry '{row['id']}' ({row['entry_type']}) has no body",
                }
            )

    def _check_events_missing_dates(
        self, issues: list[dict[str, Any]], kb_name: str | None = None
    ) -> None:
        sql = (
            "SELECT id, kb_name, title FROM entry "
            "WHERE entry_type = 'event' AND (date IS NULL OR date = '')"
        )
        params: dict[str, Any] = {}
        if kb_name:
            sql += " AND kb_name = :kb_name"
            params["kb_name"] = kb_name

        for row in self.db.execute_sql(sql, params):
            issues.append(
                {
                    "entry_id": row["id"],
                    "kb_name": row["kb_name"],
                    "rule": "event_missing_date",
                    "severity": "error",
                    "field": "date",
                    "message": f"Event '{row['id']}' has no date",
                }
            )

    def _check_invalid_dates(
        self, issues: list[dict[str, Any]], kb_name: str | None = None
    ) -> None:
        sql = (
            "SELECT id, kb_name, entry_type, date FROM entry WHERE date IS NOT NULL AND date != ''"
        )
        params: dict[str, Any] = {}
        if kb_name:
            sql += " AND kb_name = :kb_name"
            params["kb_name"] = kb_name

        for row in self.db.execute_sql(sql, params):
            if not validate_date(row["date"]):
                issues.append(
                    {
                        "entry_id": row["id"],
                        "kb_name": row["kb_name"],
                        "rule": "invalid_date",
                        "severity": "error",
                        "field": "date",
                        "message": f"Entry '{row['id']}' has invalid date: '{row['date']}'",
                    }
                )

    def _check_importance_range(
        self, issues: list[dict[str, Any]], kb_name: str | None = None
    ) -> None:
        sql = "SELECT id, kb_name, entry_type, importance FROM entry WHERE importance IS NOT NULL"
        params: dict[str, Any] = {}
        if kb_name:
            sql += " AND kb_name = :kb_name"
            params["kb_name"] = kb_name

        for row in self.db.execute_sql(sql, params):
            if not validate_importance(row["importance"]):
                issues.append(
                    {
                        "entry_id": row["id"],
                        "kb_name": row["kb_name"],
                        "rule": "importance_range",
                        "severity": "warning",
                        "field": "importance",
                        "message": f"Entry '{row['id']}' has importance {row['importance']} (must be 1-10)",
                    }
                )

    def _check_broken_links(
        self,
        issues: list[dict[str, Any]],
        kb_name: str | None = None,
        *,
        readable_kbs: set[str] | None,
    ) -> None:
        # A target the caller cannot read joins to nothing, so it is
        # reported exactly as a missing one (P-R5).
        params: dict[str, Any] = {}
        scope = kb_names_clause("e.kb_name", readable_kbs, params)
        sql = (
            "SELECT l.source_id, l.source_kb, l.target_id, l.target_kb, l.relation "
            "FROM link l LEFT JOIN entry e ON l.target_id = e.id AND l.target_kb = e.kb_name "
            + (f"AND {scope} " if scope else "")
            + "WHERE e.id IS NULL"
        )
        if kb_name:
            sql += " AND l.source_kb = :kb_name"
            params["kb_name"] = kb_name

        for row in self.db.execute_sql(sql, params):
            issues.append(
                {
                    "entry_id": row["source_id"],
                    "kb_name": row["source_kb"],
                    "rule": "broken_link",
                    # Wiki-style "wanted pages" — targets that don't exist
                    # yet are an expected state in a growing KB, not data
                    # corruption. Surface them as warnings so `pyrite qa
                    # validate` (which exits 1 only on errors) doesn't
                    # treat intentional forward references as failures.
                    "severity": "warning",
                    "field": "links",
                    "message": (
                        f"Entry '{row['source_id']}' links to non-existent "
                        f"'{row['target_id']}' in '{row['target_kb']}' ({row['relation']})"
                    ),
                }
            )

    def _check_orphans(
        self,
        issues: list[dict[str, Any]],
        kb_name: str | None = None,
        *,
        readable_kbs: set[str] | None,
    ) -> None:
        orphans = self.db.get_orphans(kb_name=kb_name, readable_kbs=readable_kbs)
        for entry in orphans:
            issues.append(
                {
                    "entry_id": entry["id"],
                    "kb_name": entry["kb_name"],
                    "rule": "orphan_entry",
                    "severity": "info",
                    "field": None,
                    "message": f"Entry '{entry['id']}' has no links in either direction",
                }
            )

    # =========================================================================
    # Per-entry checks
    # =========================================================================

    def _check_entry_fields(self, entry: dict[str, Any], issues: list[dict[str, Any]]) -> None:
        """Check individual entry field validity."""
        entry_id = entry["id"]
        kb_name = entry["kb_name"]
        entry_type = entry.get("entry_type", "")

        # Missing title
        if not entry.get("title"):
            issues.append(
                {
                    "entry_id": entry_id,
                    "kb_name": kb_name,
                    "rule": "missing_title",
                    "severity": "error",
                    "field": "title",
                    "message": f"Entry '{entry_id}' has no title",
                }
            )

        # Empty body (skip collections and relationships)
        if entry_type not in ("collection", "relationship") and not entry.get("body"):
            issues.append(
                {
                    "entry_id": entry_id,
                    "kb_name": kb_name,
                    "rule": "empty_body",
                    "severity": "warning",
                    "field": "body",
                    "message": f"Entry '{entry_id}' ({entry_type}) has no body",
                }
            )

        # Event missing date
        if entry_type == "event" and not entry.get("date"):
            issues.append(
                {
                    "entry_id": entry_id,
                    "kb_name": kb_name,
                    "rule": "event_missing_date",
                    "severity": "error",
                    "field": "date",
                    "message": f"Event '{entry_id}' has no date",
                }
            )

        # Invalid date
        date_val = entry.get("date")
        if date_val and not validate_date(str(date_val)):
            issues.append(
                {
                    "entry_id": entry_id,
                    "kb_name": kb_name,
                    "rule": "invalid_date",
                    "severity": "error",
                    "field": "date",
                    "message": f"Entry '{entry_id}' has invalid date: '{date_val}'",
                }
            )

        # Importance range
        importance = entry.get("importance")
        if importance is not None and not validate_importance(importance):
            issues.append(
                {
                    "entry_id": entry_id,
                    "kb_name": kb_name,
                    "rule": "importance_range",
                    "severity": "warning",
                    "field": "importance",
                    "message": f"Entry '{entry_id}' has importance {importance} (must be 1-10)",
                }
            )

    def _check_entry_links(
        self,
        entry_id: str,
        kb_name: str,
        issues: list[dict[str, Any]],
        *,
        readable_kbs: set[str] | None,
    ) -> None:
        """Check links from a single entry for broken targets.

        An unreadable target comes back with a null title, like a missing
        one, so both are reported the same way (P-R5).
        """
        outlinks = self.db.get_outlinks(entry_id, kb_name, readable_kbs=readable_kbs)
        for link in outlinks:
            if link.get("title") is None:
                # LEFT JOIN returned NULL title = target doesn't exist.
                # Treated as wiki-style wanted page — see _check_broken_links.
                issues.append(
                    {
                        "entry_id": entry_id,
                        "kb_name": kb_name,
                        "rule": "broken_link",
                        "severity": "warning",
                        "field": "links",
                        "message": (
                            f"Entry '{entry_id}' links to non-existent "
                            f"'{link['id']}' in '{link['kb_name']}' ({link.get('relation', 'related_to')})"
                        ),
                    }
                )

    def _check_schema_validation(
        self,
        entry: dict[str, Any],
        issues: list[dict[str, Any]],
        source: dict[str, Any] | None = None,
    ) -> None:
        """Run KBSchema.validate_entry() on a single entry.

        ``source`` is the entry file's own frontmatter (see `_validation_fields`).
        """
        kb_name = entry["kb_name"]
        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            return

        if not kb_config.kb_yaml_path.exists():
            return

        schema = kb_config.kb_schema
        entry_type = entry.get("entry_type", "")

        fields, schema_version = _validation_fields(entry, source)

        result = schema.validate_entry(
            entry_type,
            fields,
            context={
                "kb_type": kb_config.kb_type,
                "_schema_version": schema_version,
                "kb_path": str(kb_config.path),
            },
        )

        for err in result.get("errors", []):
            issues.append(
                {
                    "entry_id": entry["id"],
                    "kb_name": kb_name,
                    "rule": "schema_violation",
                    "severity": "error",
                    "field": err.get("field", ""),
                    "message": (
                        f"Schema error on '{entry['id']}': "
                        f"field '{err.get('field', '')}' — {err.get('rule', '')} "
                        f"(expected {err.get('expected', '')}, got {err.get('got', '')})"
                    ),
                }
            )

        for warn in result.get("warnings", []):
            issues.append(
                {
                    "entry_id": entry["id"],
                    "kb_name": kb_name,
                    "rule": "schema_violation",
                    "severity": "warning",
                    "field": warn.get("field", ""),
                    "message": (
                        f"Schema warning on '{entry['id']}': "
                        f"field '{warn.get('field', '')}' — {warn.get('rule', '')} "
                        f"(expected {warn.get('expected', '')}, got {warn.get('got', '')})"
                    ),
                }
            )

    # =========================================================================
    # Reserved-key pass (reads the files)
    # =========================================================================

    def _check_file_values(
        self, issues: list[dict[str, Any]], kb_name: str, *, entry_id: str | None = None
    ) -> dict[str, dict[str, Any]]:
        """Checks that need the file's own values, not the index's reading.

        Loads each file once (one entry with ``entry_id``) and runs the
        reserved-key check (#557) and the task-priority check (#554) on it.
        Returns each loaded entry's own frontmatter by id, for the schema
        pass to read typed attributes as the file wrote them.
        """
        from pathlib import Path

        from ..storage.repository import KBRepository
        from ..utils.frontmatter import leaked_frontmatter_line

        sources: dict[str, dict[str, Any]] = {}
        kb_config = self.config.get_kb(kb_name)
        if kb_config is None:
            return sources
        repo = KBRepository(kb_config)
        if entry_id is None:
            entries = repo.list_entries()
        else:
            row = self.db.get_entry(entry_id, kb_name)
            path = row.get("file_path") if row else None
            if not path or not Path(path).is_file():
                return sources
            try:
                entries = [(repo.load_entry_from_file(Path(path)), Path(path))]
            except Exception as exc:
                # The file-value checks cannot run on a file that will not
                # load: say so, rather than return as if they found nothing
                # (#795).
                logger.debug("file-value checks: cannot load %s", path, exc_info=True)
                issues.append(
                    {
                        "entry_id": entry_id,
                        "kb_name": kb_name,
                        "rule": "file_unloadable",
                        "severity": "error",
                        "field": "file",
                        "message": (
                            f"Entry '{entry_id}': its file could not be loaded, so the "
                            f"checks that read it did not run: {exc}"
                        ),
                    }
                )
                return sources

        for entry, file_path in entries:
            try:
                shown = file_path.relative_to(kb_config.path)
            except ValueError:
                shown = file_path
            self._check_reserved_key_collisions(issues, kb_name, entry, shown)
            self._check_task_priority(issues, kb_name, entry, shown)
            if entry._source_frontmatter is not None:
                leaked = leaked_frontmatter_line(entry._source_frontmatter, entry.body or "")
                if leaked is not None:
                    key, line = leaked
                    issues.append(
                        {
                            "entry_id": entry.id,
                            "kb_name": kb_name,
                            "rule": "frontmatter_line_in_body",
                            "severity": "warning",
                            "field": key,
                            "message": (
                                f"Entry '{entry.id}' body starts with {line!r}, "
                                "which repeats a frontmatter field; inspect the file "
                                "for a migrated frontmatter leak."
                            ),
                        }
                    )
                sources[entry.id] = entry._source_frontmatter
        if entry_id is None:
            # The single-entry path looks up the file through the index, which
            # never holds a row for a file that cannot load; only the KB-wide
            # pass reads every file and can see it.
            self._check_non_string_titles(issues, kb_name, repo, kb_config)
        return sources

    def _check_schema_retypes_core_fields(self, issues: list[dict[str, Any]], kb_name: str) -> None:
        """Report, once, a kb.yaml that retypes a core type's typed field.

        A task's `priority` is an integer from 1 to 10 in Pyrite's model; a
        kb.yaml declaring it a select of words (#554) contradicts that, and
        every task then gets either a schema finding or a task_priority one.
        Naming the kb.yaml and the field says where to fix it.
        """
        kb_config = self.config.get_kb(kb_name)
        if not kb_config or not kb_config.kb_yaml_path.exists():
            return
        for type_name, field_name, expected in _CORE_TYPED_FIELDS:
            type_schema = kb_config.kb_schema.types.get(type_name)
            field_schema = type_schema.fields.get(field_name) if type_schema else None
            if field_schema is None or _keeps_integer_type(field_schema):
                continue
            declared = field_schema.field_type
            if field_schema.options:
                declared += f" of {', '.join(str(o) for o in field_schema.options)}"
            issues.append(
                {
                    "entry_id": "",
                    "kb_name": kb_name,
                    "rule": "schema_retypes_core_field",
                    "severity": "error",
                    "field": field_name,
                    "message": (
                        f"{kb_config.kb_yaml_path.name}: types.{type_name}.fields."
                        f"{field_name} is declared {declared}, but a {type_name}'s "
                        f"{field_name} is {expected} in Pyrite's model. Remove the "
                        f"field from kb.yaml or declare it a number."
                    ),
                }
            )

    @staticmethod
    def _check_task_priority(
        issues: list[dict[str, Any]], kb_name: str, entry: Any, shown: Any
    ) -> None:
        """Report a task whose file ``priority`` is not an integer from 1 to 10.

        The task model reads a word (`medium`) or any other non-integer as a
        number so that one bad value cannot fail a listing (#554); the index
        then holds that number, so only the file shows what was written.
        """
        from ..models.task import task_priority_problem

        if entry.entry_type != "task":
            return
        source = entry._source_frontmatter or {}
        if "priority" not in source:
            return
        value = source["priority"]
        problem = task_priority_problem(value)
        if problem is None:
            return
        issues.append(
            {
                "entry_id": entry.id,
                "kb_name": kb_name,
                "rule": "task_priority",
                "severity": "error",
                "field": "priority",
                "message": (
                    f"{shown}: task priority {value!r} {problem}; a task's priority "
                    f"is an integer from 1 to 10 (higher is more urgent). Pyrite "
                    f"reads it as {getattr(entry, 'priority', '')}."
                ),
            }
        )

    @staticmethod
    def _check_non_string_titles(
        issues: list[dict[str, Any]], kb_name: str, repo: Any, kb_config: Any
    ) -> None:
        """Report a file whose ``title`` is a list, a mapping or NaN (#711).

        The loader refuses such a title before an id can be derived, so the
        entry pass above never sees these files: this reads each file's own
        frontmatter directly. A scalar title (``title: 2024``) is stringified
        at load (#704) and is not reported here. The path in the message is the
        handle to use: no index row exists for such a file.
        """
        from ..exceptions import FrontmatterError
        from ..utils.frontmatter import Frontmatter, split_frontmatter
        from ..utils.yaml import load_yaml

        for file_path in repo.list_files():
            try:
                text = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            split = split_frontmatter(text)
            if not isinstance(split, Frontmatter):
                continue
            try:
                fm = load_yaml(split.text)
            except FrontmatterError:
                # Frontmatter that will not parse (`title: [a, b`) has no title
                # to judge, and the malformed-file rule already reports it. Left
                # unhandled it aborts the whole KB-wide pass over one bad file,
                # so the rest of the KB goes unreported.
                logger.debug("non-string title check: skipping %s", file_path, exc_info=True)
                continue
            if not isinstance(fm, dict) or "title" not in fm:
                continue
            title = fm["title"]
            if isinstance(title, (list, tuple, dict)):
                kind = "a mapping" if isinstance(title, dict) else "a list"
            elif isinstance(title, float) and title != title:
                kind = "NaN"
            else:
                continue
            try:
                shown = file_path.relative_to(kb_config.path)
            except ValueError:
                shown = file_path
            issues.append(
                {
                    "entry_id": "",
                    "kb_name": kb_name,
                    "rule": "non_string_title",
                    "severity": "error",
                    "field": "title",
                    "message": (
                        f"{shown}: title is {kind}, not a string; pyrite "
                        f"cannot index it. Quote it as a single line (#711)."
                    ),
                }
            )

    @staticmethod
    def _check_reserved_key_collisions(
        issues: list[dict[str, Any]], kb_name: str, entry: Any, shown: Any
    ) -> None:
        """Report a file that uses a Pyrite-reserved key for a field of its own.

        `provenance`, `sources`, `importance`... are read into Pyrite's own
        model. A file whose value under one of those names does not fit that
        model (`provenance: {source: ..}`, `importance: high`) is kept as it
        is on every update (#557), but Pyrite reads it wrongly: the index,
        search and the API see Pyrite's reading, not the file's. The rule
        names the file and the key so the KB can rename it. A value Pyrite
        only normalizes, losing nothing (`links: [a, b]`, `tags: Foo`), is
        not reported.

        Reads files, not the index: the index keeps only Pyrite's reading.
        """
        from ..schema.reserved import RESERVED_FIELD_NAMES

        kept = entry.unrepresented_frontmatter()
        if not kept:
            return
        emitted = entry.to_frontmatter()
        for key, value in kept.items():
            if key not in RESERVED_FIELD_NAMES:
                continue
            # A key the model leaves out at its default (`lifecycle:
            # active`) is read, not lost: fall back to the attribute.
            read_as = emitted[key] if key in emitted else getattr(entry, key, None)
            lost = _leaves(value) - _leaves(read_as)
            if not lost:
                continue
            issues.append(
                {
                    "entry_id": entry.id,
                    "kb_name": kb_name,
                    "rule": "reserved_key_collision",
                    "severity": "warning",
                    "field": key,
                    "message": (
                        f"{shown}: '{key}' is a key Pyrite reserves, and this "
                        f"file's value does not fit it (Pyrite cannot read "
                        f"{', '.join(sorted(lost)[:3])}). Updates keep the "
                        f"file's value, but search and the API see Pyrite's "
                        f"reading. Rename the key (for example "
                        f"'{entry.entry_type}_{key.lstrip('_')}')."
                    ),
                }
            )

    # =========================================================================
    # Schema pass (all entries)
    # =========================================================================

    def _check_schema_all(
        self,
        issues: list[dict[str, Any]],
        kb_name: str,
        sources: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Run schema validation on all entries in a KB (only if kb.yaml exists).

        ``sources`` maps an entry id to its file's own frontmatter (see
        `_validation_fields`).
        """
        sources = sources or {}
        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            return

        if not kb_config.kb_yaml_path.exists():
            return

        schema = kb_config.kb_schema

        rows = self.db.execute_sql(
            "SELECT id, kb_name, entry_type, title, date, importance, status, metadata "
            "FROM entry WHERE kb_name = :kb_name",
            {"kb_name": kb_name},
        )

        for row in rows:
            entry = dict(row)
            entry_type = entry.get("entry_type", "")
            fields, schema_version = _validation_fields(entry, sources.get(entry["id"]))

            result = schema.validate_entry(
                entry_type,
                fields,
                context={"kb_type": kb_config.kb_type, "_schema_version": schema_version},
            )

            for err in result.get("errors", []):
                issues.append(
                    {
                        "entry_id": entry["id"],
                        "kb_name": kb_name,
                        "rule": "schema_violation",
                        "severity": "error",
                        "field": err.get("field", ""),
                        "message": (
                            f"Schema error on '{entry['id']}': "
                            f"field '{err.get('field', '')}' — {err.get('rule', '')} "
                            f"(expected {err.get('expected', '')}, got {err.get('got', '')})"
                        ),
                    }
                )

            for warn in result.get("warnings", []):
                issues.append(
                    {
                        "entry_id": entry["id"],
                        "kb_name": kb_name,
                        "rule": "schema_violation",
                        "severity": "warning",
                        "field": warn.get("field", ""),
                        "message": (
                            f"Schema warning on '{entry['id']}': "
                            f"field '{warn.get('field', '')}' — {warn.get('rule', '')} "
                            f"(expected {warn.get('expected', '')}, got {warn.get('got', '')})"
                        ),
                    }
                )

    # =========================================================================
    # Rubric evaluation
    # =========================================================================

    def _get_rubric_items(self, entry_type: str, kb_name: str) -> list[str | dict[str, Any]]:
        """Collect rubric items applicable to an entry type.

        Merges system-level rubric, type-level rubric (from CORE_TYPE_METADATA
        or KB overrides), and KB-level rubric from kb.yaml.

        Items can be plain strings (judgment-only or legacy regex-matched) or
        dicts with {text, checker, params} or {text, covered_by}.
        Deduplication keys on text content; dict items override string items.
        """
        items: list[str | dict[str, Any]] = []
        seen: dict[str, int] = {}  # text -> index in items list

        def _text_key(item: str | dict[str, Any]) -> str:
            if isinstance(item, dict):
                return item.get("text", "")
            return item

        def _add_item(item: str | dict[str, Any]) -> None:
            key = _text_key(item)
            if not key:
                return
            if key in seen:
                # Dict items override string items with same text
                if isinstance(item, dict) and isinstance(items[seen[key]], str):
                    items[seen[key]] = item
                return
            seen[key] = len(items)
            items.append(item)

        # System-level rubric (always applies)
        for item in SYSTEM_INTENT.get("evaluation_rubric", []):
            _add_item(item)

        # Type-level rubric (resolved through 4-layer precedence)
        kb_config = self.config.get_kb(kb_name)
        kb_schema = kb_config.kb_schema if kb_config else None
        type_meta = resolve_type_metadata(entry_type, kb_schema)
        for item in type_meta.get("evaluation_rubric", []):
            _add_item(item)

        # KB-level rubric from kb.yaml
        if kb_schema:
            for item in kb_schema.evaluation_rubric:
                _add_item(item)

        return items

    def _check_rubric_evaluation(self, entry: dict[str, Any], issues: list[dict[str, Any]]) -> None:
        """Run rubric checks on a single entry."""
        entry_id = entry["id"]
        kb_name = entry["kb_name"]
        entry_type = entry.get("entry_type", "")

        rubric_items = self._get_rubric_items(entry_type, kb_name)
        if not rubric_items:
            return

        # Enrich entry with tag count and outlink count for checkers
        enriched = dict(entry)
        tag_rows = self.db.execute_sql(
            "SELECT COUNT(*) as cnt FROM entry_tag WHERE entry_id = :eid AND kb_name = :kb",
            {"eid": entry_id, "kb": kb_name},
        )
        enriched["_tag_count"] = tag_rows[0]["cnt"] if tag_rows else 0

        link_rows = self.db.execute_sql(
            "SELECT COUNT(*) as cnt FROM link WHERE source_id = :eid AND source_kb = :kb",
            {"eid": entry_id, "kb": kb_name},
        )
        enriched["_outlink_count"] = link_rows[0]["cnt"] if link_rows else 0

        kb_config = self.config.get_kb(kb_name)
        kb_schema = kb_config.kb_schema if kb_config else None

        # Cache named checkers once for all items
        named_checkers = get_registry().get_all_rubric_checkers()

        for item in rubric_items:
            if isinstance(item, str):
                # Plain strings are judgment-only, handled by LLM evaluation
                continue

            # Dict rubric item
            if item.get("covered_by"):
                continue

            checker_name = item.get("checker")
            if not checker_name:
                # Judgment-only dict (has text but no checker)
                logger.debug("Rubric item has no checker (judgment_only): %s", item.get("text", ""))
                continue

            fn = named_checkers.get(checker_name)
            if fn is None:
                issues.append(
                    {
                        "entry_id": entry_id,
                        "kb_name": kb_name,
                        "rule": "config_error",
                        "severity": "warning",
                        "field": "evaluation_rubric",
                        "message": f"Unknown checker '{checker_name}' in rubric for type '{entry_type}'",
                    }
                )
                continue

            params = dict(item.get("params", {}) or {})
            params["rubric_text"] = item.get("text", "")
            try:
                issue = fn(enriched, kb_schema, params)
                if issue is not None:
                    issues.append(issue)
            except Exception as exc:
                # The item was not checked: report it, like an unknown checker
                # above, rather than let the entry pass it (#795).
                logger.warning("Named rubric checker '%s' failed", checker_name, exc_info=True)
                issues.append(
                    {
                        "entry_id": entry_id,
                        "kb_name": kb_name,
                        "rule": "checker_failed",
                        "severity": "warning",
                        "field": "evaluation_rubric",
                        "message": (
                            f"Rubric checker '{checker_name}' failed on this entry, so "
                            f"'{item.get('text', '')}' was not checked: "
                            f"{type(exc).__name__}: {exc}"
                        ),
                    }
                )

    def _collect_judgment_items(self, entry_type: str, kb_name: str) -> list[str]:
        """Filter rubric items to judgment-only (no deterministic checker, not schema-covered)."""
        all_items = self._get_rubric_items(entry_type, kb_name)
        result: list[str] = []
        for item in all_items:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                if item.get("covered_by") or item.get("checker"):
                    continue
                text = item.get("text", "")
                if text:
                    result.append(text)
        return result

    def _evaluate_llm_rubric(self, entry_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Run LLM rubric evaluation on a single entry. Returns list of issues.

        ``[]`` only when the model answered every judgment item and passed
        them, or the type has none. No model raises ``AINotConfiguredError``,
        an entry not in the index ``EntryNotFoundError``, and a model that did
        not answer ``EvaluationFailedError`` (#795).
        """
        if not self.llm_available:
            raise AINotConfiguredError(
                "QA tier 2 runs the model-judged rubric and no model is configured"
            )

        # Fetch entry row
        rows = self.db.execute_sql(
            "SELECT id, kb_name, entry_type, title, date, importance, body, status, metadata "
            "FROM entry WHERE id = :entry_id AND kb_name = :kb_name",
            {"entry_id": entry_id, "kb_name": kb_name},
        )
        if not rows:
            raise EntryNotFoundError(
                f"Entry '{entry_id}' is not in the index of KB '{kb_name}'; "
                "tier 2 has nothing to evaluate"
            )
        return self._evaluate_llm_rubric_row(rows[0])

    def _evaluate_llm_rubric_row(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """The model-judged rubric for one index row (see `_evaluate_llm_rubric`)."""
        kb_name = entry["kb_name"]
        entry_type = entry.get("entry_type", "")
        judgment_items = self._collect_judgment_items(entry_type, kb_name)
        if not judgment_items:
            return []  # nothing for the model to judge: a legitimate empty

        # Get guidelines for context
        kb_config = self.config.get_kb(kb_name)
        kb_schema = kb_config.kb_schema if kb_config else None
        type_meta = resolve_type_metadata(entry_type, kb_schema)
        guidelines = type_meta.get("guidelines", "")

        try:
            return self._run_evaluator(entry, judgment_items, guidelines)
        except PyriteError:
            raise
        except Exception as exc:
            # Whatever the evaluator did not expect is still a check that did
            # not run: name it for this entry, never end the caller's run.
            logger.warning("Tier 2 evaluation of %s failed", entry["id"], exc_info=True)
            raise EvaluationFailedError(entry["id"], f"{type(exc).__name__}: {exc}") from exc

    def _run_evaluator(
        self, entry: dict[str, Any], judgment_items: list[str], guidelines: str
    ) -> list[dict[str, Any]]:
        """Run the async evaluator from sync code, in or out of an event loop."""
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(
                        asyncio.run,
                        self.llm_evaluator.evaluate(dict(entry), judgment_items, guidelines),
                    ).result()
                return result
            else:
                return loop.run_until_complete(
                    self.llm_evaluator.evaluate(dict(entry), judgment_items, guidelines)
                )
        except RuntimeError:
            return asyncio.run(self.llm_evaluator.evaluate(dict(entry), judgment_items, guidelines))

    def _check_llm_rubric_all(self, issues: list[dict[str, Any]], kb_name: str) -> None:
        """Tier 2 of `validate_kb`: the model-judged rubric over every entry.

        Skips ``qa_assessment`` entries, as `assess_kb` does. A model finding
        is an issue as `assess_entry` records it; an entry the model did not
        answer for is an ``evaluation_failed`` error naming the reason, so
        `ci` can never pass a check that did not run (#795).
        """
        rows = self.db.execute_sql(
            "SELECT id, kb_name, entry_type, title, date, importance, body, status, metadata "
            "FROM entry WHERE kb_name = :kb_name AND entry_type != 'qa_assessment' ORDER BY id",
            {"kb_name": kb_name},
        )
        for row in rows:
            try:
                issues.extend(self._evaluate_llm_rubric_row(row))
            # Second layer: `_evaluate_llm_rubric_row` already turns any error
            # into EvaluationFailedError; this catch keeps the run going if a
            # future path does not.
            except Exception as exc:  # one entry's failure never ends the run
                issues.append(
                    {
                        "entry_id": row["id"],
                        "kb_name": kb_name,
                        "rule": "evaluation_failed",
                        "severity": "error",
                        "field": "evaluation_rubric",
                        "message": f"Tier 2 did not run for '{row['id']}': "
                        + getattr(exc, "reason", str(exc)),
                    }
                )

    def _check_rubric_all(self, issues: list[dict[str, Any]], kb_name: str) -> None:
        """Bulk SQL rubric checks across all entries in a KB."""
        # 1. Missing tags
        no_tag_rows = self.db.execute_sql(
            "SELECT e.id, e.kb_name, e.entry_type, e.title FROM entry e "
            "LEFT JOIN entry_tag et ON e.id = et.entry_id AND e.kb_name = et.kb_name "
            "WHERE e.kb_name = :kb_name AND et.entry_id IS NULL",
            {"kb_name": kb_name},
        )
        for row in no_tag_rows:
            issues.append(
                {
                    "entry_id": row["id"],
                    "kb_name": row["kb_name"],
                    "rule": "rubric_violation",
                    "severity": "warning",
                    "field": "tags",
                    "message": f"Entry '{row['id']}' has no tags",
                    "rubric_item": "Entry has at least one tag",
                }
            )

        # 2. Missing outlinks (excluding stubs)
        no_link_rows = self.db.execute_sql(
            "SELECT e.id, e.kb_name, e.entry_type, e.title, e.body FROM entry e "
            "WHERE e.kb_name = :kb_name "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM link l WHERE l.source_id = e.id AND l.source_kb = e.kb_name"
            ")",
            {"kb_name": kb_name},
        )
        for row in no_link_rows:
            body = row.get("body", "") or ""
            if "stub" not in body.lower():
                issues.append(
                    {
                        "entry_id": row["id"],
                        "kb_name": row["kb_name"],
                        "rule": "rubric_violation",
                        "severity": "warning",
                        "field": "links",
                        "message": f"Entry '{row['id']}' has no outgoing links",
                        "rubric_item": "Entry links to at least one related entry (unless a stub)",
                    }
                )

        # 3. Generic titles
        rows = self.db.execute_sql(
            "SELECT id, kb_name, title FROM entry WHERE kb_name = :kb_name "
            "AND title IS NOT NULL AND title != ''",
            {"kb_name": kb_name},
        )
        from .rubric_checkers import GENERIC_TITLES

        for row in rows:
            title = (row.get("title") or "").strip().lower()
            if title in GENERIC_TITLES:
                issues.append(
                    {
                        "entry_id": row["id"],
                        "kb_name": row["kb_name"],
                        "rule": "rubric_violation",
                        "severity": "warning",
                        "field": "title",
                        "message": f"Entry '{row['id']}' has a generic title: '{row['title']}'",
                        "rubric_item": "Entry has a descriptive title",
                    }
                )

        # 4. Type-specific metadata checks (person/role, document/url|author, document/document_type)
        self._check_rubric_type_metadata(issues, kb_name)

    def _check_rubric_type_metadata(self, issues: list[dict[str, Any]], kb_name: str) -> None:
        """Bulk check type-specific metadata fields from rubric."""
        # Person: role
        person_rows = self.db.execute_sql(
            "SELECT id, kb_name, metadata FROM entry "
            "WHERE kb_name = :kb_name AND entry_type = 'person' "
            "AND (metadata IS NULL OR json_extract(metadata, '$.role') IS NULL "
            "OR json_extract(metadata, '$.role') = '')",
            {"kb_name": kb_name},
        )
        for row in person_rows:
            issues.append(
                {
                    "entry_id": row["id"],
                    "kb_name": row["kb_name"],
                    "rule": "rubric_violation",
                    "severity": "warning",
                    "field": "metadata.role",
                    "message": f"Entry '{row['id']}' is missing 'role' in metadata",
                    "rubric_item": "Person has a role or position described",
                }
            )

        # Document: url or author
        doc_rows = self.db.execute_sql(
            "SELECT id, kb_name, metadata FROM entry "
            "WHERE kb_name = :kb_name AND entry_type = 'document' "
            "AND (metadata IS NULL OR ("
            "  (json_extract(metadata, '$.url') IS NULL OR json_extract(metadata, '$.url') = '') "
            "  AND (json_extract(metadata, '$.author') IS NULL OR json_extract(metadata, '$.author') = '')"
            "))",
            {"kb_name": kb_name},
        )
        for row in doc_rows:
            issues.append(
                {
                    "entry_id": row["id"],
                    "kb_name": row["kb_name"],
                    "rule": "rubric_violation",
                    "severity": "warning",
                    "field": "metadata",
                    "message": f"Entry '{row['id']}' is missing url or author in metadata",
                    "rubric_item": "Document has a source URL or author",
                }
            )

        # Document: document_type
        doc_type_rows = self.db.execute_sql(
            "SELECT id, kb_name, metadata FROM entry "
            "WHERE kb_name = :kb_name AND entry_type = 'document' "
            "AND (metadata IS NULL OR json_extract(metadata, '$.document_type') IS NULL "
            "OR json_extract(metadata, '$.document_type') = '')",
            {"kb_name": kb_name},
        )
        for row in doc_type_rows:
            issues.append(
                {
                    "entry_id": row["id"],
                    "kb_name": row["kb_name"],
                    "rule": "rubric_violation",
                    "severity": "warning",
                    "field": "metadata.document_type",
                    "message": f"Entry '{row['id']}' is missing 'document_type' in metadata",
                    "rubric_item": "Document has a document_type classification",
                }
            )

    # =========================================================================
    # Delegated: Auto-fix API (QAFixService)
    # =========================================================================

    # Expose FIXABLE_RULES for backward compatibility
    @property
    def FIXABLE_RULES(self):
        from .qa_fix_service import QAFixService

        return QAFixService.FIXABLE_RULES

    def fix_kb(
        self,
        kb_name: str,
        *,
        dry_run: bool = True,
        fix_rules: list[str] | None = None,
        readable_kbs: set[str] | None,
    ) -> dict[str, Any]:
        """Auto-fix safe structural issues in a KB. Delegates to QAFixService.

        The validation that finds the issues runs within ``readable_kbs``,
        as `validate_kb` does.
        """
        return self._fix_svc.fix_kb(
            kb_name,
            dry_run=dry_run,
            fix_rules=fix_rules,
            validate_kb_fn=lambda kb: self.validate_kb(kb, readable_kbs=readable_kbs),
        )

    def _get_all_entry_ids(self, kb_name: str) -> list[str]:
        """Delegate to QAFixService."""
        return self._fix_svc._get_all_entry_ids(kb_name)

    @staticmethod
    def _normalise_date(raw: str) -> str | None:
        """Delegate to QAFixService."""
        from .qa_fix_service import QAFixService

        return QAFixService._normalise_date(raw)

    def _fix_invalid_date(self, issue: dict[str, Any]) -> dict[str, Any] | None:
        """Delegate to QAFixService."""
        return self._fix_svc._fix_invalid_date(issue)

    @staticmethod
    def _fix_missing_field(issue: dict[str, Any]) -> dict[str, Any] | None:
        """Delegate to QAFixService."""
        from .qa_fix_service import QAFixService

        return QAFixService._fix_missing_field(issue)

    @staticmethod
    def _edit_distance(a: str, b: str) -> int:
        """Delegate to QAFixService."""
        from .qa_fix_service import QAFixService

        return QAFixService._edit_distance(a, b)

    def _fix_broken_link(
        self,
        issue: dict[str, Any],
        all_entry_ids: list[str],
    ) -> dict[str, Any] | None:
        """Delegate to QAFixService."""
        return self._fix_svc._fix_broken_link(issue, all_entry_ids)

    def _apply_link_fix(self, entry_id, kb_name, old_target, new_target, kb_svc):
        """Delegate to QAFixService."""
        return self._fix_svc._apply_link_fix(entry_id, kb_name, old_target, new_target, kb_svc)

    def _find_tag_normalisation_fixes(self, kb_name: str) -> list[dict[str, Any]]:
        """Delegate to QAFixService."""
        return self._fix_svc._find_tag_normalisation_fixes(kb_name)

    def _apply_tag_normalisation(self, entry_id, kb_name, old_tag, new_tag, kb_svc):
        """Delegate to QAFixService."""
        return self._fix_svc._apply_tag_normalisation(entry_id, kb_name, old_tag, new_tag, kb_svc)

    # =========================================================================
    # Delegated: Analytics API (QAAnalyticsService)
    # =========================================================================

    # Expose _STALENESS_EXEMPT_TYPES for backward compatibility
    @property
    def _STALENESS_EXEMPT_TYPES(self):
        return self._analytics_svc._STALENESS_EXEMPT_TYPES

    def _check_staleness(
        self, issues: list[dict[str, Any]], kb_name: str, max_age_days: int = 90
    ) -> None:
        """Delegate to QAAnalyticsService."""
        return self._analytics_svc._check_staleness(issues, kb_name, max_age_days)

    def find_stale(self, kb_name: str, max_age_days: int = 90) -> list[dict[str, Any]]:
        """Delegate to QAAnalyticsService."""
        return self._analytics_svc.find_stale(kb_name, max_age_days)

    def find_archival_candidates(
        self, kb_name: str, min_age_days: int = 90
    ) -> list[dict[str, Any]]:
        """Delegate to QAAnalyticsService."""
        return self._analytics_svc.find_archival_candidates(kb_name, min_age_days)

    def analyze_gaps(
        self,
        kb_name: str,
        threshold: int = 3,
    ) -> dict[str, Any]:
        """Delegate to QAAnalyticsService."""
        return self._analytics_svc.analyze_gaps(kb_name, threshold)

    @staticmethod
    def _extract_tags_from_schema(kb_schema) -> set[str]:
        """Delegate to QAAnalyticsService."""
        from .qa_analytics_service import QAAnalyticsService

        return QAAnalyticsService._extract_tags_from_schema(kb_schema)

    @staticmethod
    def _days_since(iso_str: str, now: datetime) -> int:
        """Delegate to QAAnalyticsService."""
        from .qa_analytics_service import QAAnalyticsService

        return QAAnalyticsService._days_since(iso_str, now)


#: Fields a core entry keeps as typed attributes outside `metadata` (a task's
#: `priority`, `assignee`, `due_date`...), in index columns of their own. The
#: schema passes read them from the entry's file, not the column (which holds
#: Pyrite's reading), so a kb.yaml rule on one of them sees the file's value:
#: reading neither, `required: [priority]` reported every task as "got None",
#: `priority: 5` included (#554).
_TYPED_COLUMNS = (
    "location",
    "assignee",
    "assigned_at",
    "priority",
    "due_date",
    "start_date",
    "end_date",
    "coordinates",
)

#: Core-type fields whose type Pyrite's model fixes: (type, field, what it is).
_CORE_TYPED_FIELDS = (("task", "priority", "an integer from 1 to 10"),)


def _keeps_integer_type(field_schema: Any) -> bool:
    """Whether a kb.yaml field declaration still holds an integer."""
    if field_schema.field_type == "number":
        return True
    if field_schema.field_type == "select" and field_schema.options:
        return all(isinstance(o, int) for o in field_schema.options)
    return False


def _validation_fields(
    entry: dict[str, Any], source: dict[str, Any] | None = None
) -> tuple[dict[str, Any], int]:
    """The fields a schema validates for an index row, and its schema version.

    The row's own columns, then the custom fields kept in the metadata JSON
    (writing_type, source_type...), which the validator needs to check
    required fields. The typed columns (`_TYPED_COLUMNS`) are not selected:
    a kb.yaml type with `protocols:` keeps the file's value in metadata (the
    column holds a lossy copy, `[Paris, Lyon]` as a str), and a core type
    that keeps the field as an attribute (a task's `priority`) puts Pyrite's
    reading in the column -- the model's default (5) for a file with no
    priority, its coercion (5) for `medium`. Such a field is taken from
    ``source``, the file's own frontmatter, as the file wrote it, and is
    absent when the file lacks it (round-2 review of #566). Without
    ``source`` (the file did not load) it is left out, never validated as
    Pyrite's reading.
    """
    meta_dict = parse_metadata(entry.get("metadata"))
    fields = {k: v for k, v in entry.items() if v is not None and k not in ("id", "kb_name")}
    for k in _TYPED_COLUMNS:
        if source is not None and source.get(k) is not None:
            fields[k] = source[k]
    schema_version = int(meta_dict.get("_schema_version", 0))
    for k, v in meta_dict.items():
        if k not in fields and k != "_schema_version" and v is not None:
            fields[k] = v
    return fields, schema_version


def _leaves(value: Any) -> set[str]:
    """Every mapping key and non-empty scalar in ``value``, as strings.

    Used to ask whether Pyrite's reading of a reserved key kept everything
    the file said: a leaf of the file's value missing from the reading's
    leaves is information Pyrite does not represent.
    """
    if isinstance(value, dict) or hasattr(value, "items"):
        out: set[str] = set()
        for k, v in value.items():
            out.add(str(k))
            out |= _leaves(v)
        return out
    if isinstance(value, (list, tuple)):
        out = set()
        for v in value:
            out |= _leaves(v)
        return out
    if value is None or value == "":
        return set()
    return {str(value)}
