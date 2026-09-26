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
from ..plugins.registry import get_registry
from ..schema import validate_date, validate_importance
from ..schema.core_types import SYSTEM_INTENT, resolve_type_metadata
from ..storage.backends.base_backend import kb_names_clause
from ..storage.database import PyriteDB
from ..utils.metadata import parse_metadata

logger = logging.getLogger(__name__)


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
        self._check_schema_validation(entry, issues)
        self._check_rubric_evaluation(entry, issues)

        return {"entry_id": entry_id, "kb_name": kb_name, "issues": issues}

    def validate_kb(
        self,
        kb_name: str,
        check_staleness: bool = False,
        staleness_days: int = 90,
        *,
        readable_kbs: set[str] | None,
    ) -> dict[str, Any]:
        """Validate all entries in a KB. Returns {kb_name, total, checked, issues: [...]}.

        ``readable_kbs`` bounds the link checks (P-R4, P-R5): a target the
        caller cannot read is reported as a missing one, and a link from a
        source it cannot read does not stop an entry being an orphan.
        """
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

        # Per-entry schema pass (only if kb.yaml exists)
        self._check_schema_all(issues, kb_name)

        # Rubric evaluation pass
        self._check_rubric_all(issues, kb_name)

        # Optional staleness check (delegated to analytics service)
        if check_staleness:
            self._analytics_svc._check_staleness(issues, kb_name, staleness_days)

        return {
            "kb_name": kb_name,
            "total": total,
            "checked": total,
            "issues": issues,
        }

    def validate_all(
        self,
        kb_names: set[str] | list[str] | None = None,
        *,
        readable_kbs: set[str] | None,
    ) -> dict[str, Any]:
        """Validate all KBs. Returns {kbs: [{kb_name, total, checked, issues}]}.

        ``kb_names`` restricts the sweep to those KBs -- the caller's
        readable set. A KB left out contributes neither issues nor its
        name, so the aggregate totals in ``get_status`` are computed over
        readable rows only. ``readable_kbs`` bounds each KB's link checks
        (see `validate_kb`).
        """
        kbs = []
        for kb in self.config.all_kbs():
            if kb_names is not None and kb.name not in kb_names:
                continue
            result = self.validate_kb(kb.name, readable_kbs=readable_kbs)
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
        Tier 2+: tier 1 + LLM judgment rubric evaluation.
        """
        result = self.validate_entry(entry_id, kb_name, readable_kbs=readable_kbs)
        issues = result["issues"]

        # Tier 2+: LLM rubric evaluation
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

        if create_task_on_fail and qa_status == "fail":
            self._maybe_create_task(entry_id, kb_name, assessment_id, issues)

        return {
            "assessment_id": assessment_id,
            "target_entry": entry_id,
            "kb_name": kb_name,
            "qa_status": qa_status,
            "issues_found": len(issues),
            "issues": issues,
            "llm_available": self.llm_available,
        }

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

        Returns dict with kb_name, assessed count, skipped count, and results list.
        """
        rows = self.db.execute_sql(
            "SELECT id, entry_type FROM entry WHERE kb_name = :kb_name",
            {"kb_name": kb_name},
        )

        results = []
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

            result = self.assess_entry(
                eid,
                kb_name,
                tier=tier,
                create_task_on_fail=create_task_on_fail,
                readable_kbs=readable_kbs,
            )
            results.append(result)

        return {
            "kb_name": kb_name,
            "assessed": len(results),
            "skipped": skipped,
            "results": results,
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
    ) -> None:
        """Create a task for failed assessment."""
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

    def _check_schema_validation(self, entry: dict[str, Any], issues: list[dict[str, Any]]) -> None:
        """Run KBSchema.validate_entry() on a single entry."""
        kb_name = entry["kb_name"]
        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            return

        if not kb_config.kb_yaml_path.exists():
            return

        schema = kb_config.kb_schema
        entry_type = entry.get("entry_type", "")

        # Build fields dict for validation
        fields = {k: v for k, v in entry.items() if v is not None and k not in ("id", "kb_name")}

        # Extract _schema_version and merge custom fields from metadata
        # Custom type-specific fields (e.g. writing_type, source_type, date_range)
        # are stored in the metadata JSON blob, not as top-level entry attributes.
        # The validator needs them in the fields dict to check required fields.
        meta_dict = parse_metadata(entry.get("metadata"))
        schema_version = int(meta_dict.get("_schema_version", 0))
        if meta_dict:
            # Merge metadata fields into fields dict so custom type fields
            # are visible to the schema validator
            for k, v in meta_dict.items():
                if k not in fields and k != "_schema_version" and v is not None:
                    fields[k] = v

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
    # Schema pass (all entries)
    # =========================================================================

    def _check_schema_all(self, issues: list[dict[str, Any]], kb_name: str) -> None:
        """Run schema validation on all entries in a KB (only if kb.yaml exists)."""
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
            fields = {
                k: v for k, v in entry.items() if v is not None and k not in ("id", "kb_name")
            }

            # Extract _schema_version and merge custom fields from metadata
            meta_dict = parse_metadata(entry.get("metadata"))
            schema_version = int(meta_dict.get("_schema_version", 0))
            # Merge metadata fields into fields dict so custom type fields
            # are visible to the schema validator
            for k, v in meta_dict.items():
                if k not in fields and k != "_schema_version" and v is not None:
                    fields[k] = v

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
            except Exception:
                logger.warning("Named rubric checker '%s' failed", checker_name, exc_info=True)

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
        """Run LLM rubric evaluation on a single entry. Returns list of issues."""
        if not self.llm_available:
            logger.info("LLM not configured; skipping LLM rubric evaluation for %s", entry_id)
            return []

        # Fetch entry row
        rows = self.db.execute_sql(
            "SELECT id, kb_name, entry_type, title, date, importance, body, status, metadata "
            "FROM entry WHERE id = :entry_id AND kb_name = :kb_name",
            {"entry_id": entry_id, "kb_name": kb_name},
        )
        if not rows:
            return []

        entry = rows[0]
        entry_type = entry.get("entry_type", "")
        judgment_items = self._collect_judgment_items(entry_type, kb_name)
        if not judgment_items:
            return []

        # Get guidelines for context
        kb_config = self.config.get_kb(kb_name)
        kb_schema = kb_config.kb_schema if kb_config else None
        type_meta = resolve_type_metadata(entry_type, kb_schema)
        guidelines = type_meta.get("guidelines", "")

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
    ) -> dict[str, Any]:
        """Auto-fix safe structural issues in a KB. Delegates to QAFixService."""
        return self._fix_svc.fix_kb(
            kb_name,
            dry_run=dry_run,
            fix_rules=fix_rules,
            validate_kb_fn=self.validate_kb,
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
