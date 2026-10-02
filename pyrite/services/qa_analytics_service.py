"""
QA Analytics Service — gap analysis, staleness checks, and archival candidates.

Extracted from QAService. Handles structural coverage gap analysis,
stale entry detection, and archival candidate identification.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from ..config import PyriteConfig
from ..storage.database import PyriteDB

logger = logging.getLogger(__name__)


class QAAnalyticsService:
    """Gap analysis, staleness, and archival for knowledge bases."""

    # Types that are historical by design — never flag as stale.
    _STALENESS_EXEMPT_TYPES = frozenset(
        {
            "adr",
            "event",
            "timeline",
            "qa_assessment",
            "relationship",
        }
    )

    def __init__(self, config: PyriteConfig, db: PyriteDB):
        self.config = config
        self.db = db

    def _check_staleness(
        self, issues: list[dict[str, Any]], kb_name: str, max_age_days: int = 90
    ) -> None:
        """Add staleness info issues for entries not updated within *max_age_days*."""
        for entry in self.find_stale(kb_name, max_age_days):
            issues.append(
                {
                    "entry_id": entry["entry_id"],
                    "kb_name": kb_name,
                    "rule": "stale_entry",
                    "severity": "info",
                    "field": "updated_at",
                    "message": (
                        f"Entry '{entry['entry_id']}' ({entry['entry_type']}) "
                        f"has not been updated in {entry['days_stale']} days"
                    ),
                }
            )

    def find_stale(self, kb_name: str, max_age_days: int = 90) -> list[dict[str, Any]]:
        """Find active entries not updated within *max_age_days*.

        Type-aware: historical types (adr, event, timeline, qa_assessment,
        relationship) are exempt from staleness checks.

        Returns list of dicts with entry_id, entry_type, title, days_stale.
        """
        cutoff = (datetime.now(UTC) - timedelta(days=max_age_days)).isoformat()

        rows = self.db.execute_sql(
            "SELECT id, entry_type, title, updated_at FROM entry "
            "WHERE kb_name = :kb_name AND lifecycle = 'active' "
            "AND updated_at < :cutoff "
            "ORDER BY updated_at ASC",
            {"kb_name": kb_name, "cutoff": cutoff},
        )

        now = datetime.now(UTC)
        results = []
        for row in rows:
            entry_type = row.get("entry_type", "")
            if entry_type in self._STALENESS_EXEMPT_TYPES:
                continue

            updated_str = row.get("updated_at", "")
            try:
                updated = datetime.fromisoformat(updated_str)
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=UTC)
                days_stale = (now - updated).days
            except (ValueError, TypeError):
                days_stale = max_age_days  # Treat unparseable as stale

            results.append(
                {
                    "entry_id": row["id"],
                    "entry_type": entry_type,
                    "title": row.get("title", ""),
                    "days_stale": days_stale,
                    "updated_at": updated_str,
                }
            )

        return results

    def find_archival_candidates(
        self, kb_name: str, min_age_days: int = 90
    ) -> list[dict[str, Any]]:
        """Find entries that are candidates for archival.

        Candidates:
        1. Completed backlog items older than *min_age_days*
        2. Old orphan entries (no links) with low importance (<=3) older than *min_age_days*

        Returns list of dicts with entry_id, entry_type, title, reason, days_old.
        """
        cutoff = (datetime.now(UTC) - timedelta(days=min_age_days)).isoformat()
        now = datetime.now(UTC)
        results: list[dict[str, Any]] = []

        # 1. Completed backlog items
        done_rows = self.db.execute_sql(
            "SELECT id, entry_type, title, updated_at FROM entry "
            "WHERE kb_name = :kb_name AND lifecycle = 'active' "
            "AND entry_type = 'backlog_item' AND status = 'completed' "
            "AND updated_at < :cutoff",
            {"kb_name": kb_name, "cutoff": cutoff},
        )
        for row in done_rows:
            days_old = self._days_since(row.get("updated_at", ""), now)
            results.append(
                {
                    "entry_id": row["id"],
                    "entry_type": row.get("entry_type", ""),
                    "title": row.get("title", ""),
                    "reason": "completed_backlog_item",
                    "days_old": days_old,
                }
            )

        # 2. Old orphan entries with low importance
        orphan_rows = self.db.execute_sql(
            "SELECT e.id, e.entry_type, e.title, e.updated_at FROM entry e "
            "WHERE e.kb_name = :kb_name AND e.lifecycle = 'active' "
            "AND e.updated_at < :cutoff "
            "AND (e.importance IS NULL OR e.importance <= 3) "
            "AND e.entry_type NOT IN ('backlog_item', 'qa_assessment', 'collection') "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM link l WHERE l.target_id = e.id AND l.target_kb = :kb_name"
            ") "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM link l WHERE l.source_id = e.id AND l.source_kb = :kb_name"
            ")",
            {"kb_name": kb_name, "cutoff": cutoff},
        )
        existing_ids = {r["entry_id"] for r in results}
        for row in orphan_rows:
            if row["id"] not in existing_ids:
                days_old = self._days_since(row.get("updated_at", ""), now)
                results.append(
                    {
                        "entry_id": row["id"],
                        "entry_type": row.get("entry_type", ""),
                        "title": row.get("title", ""),
                        "reason": "old_orphan",
                        "days_old": days_old,
                    }
                )

        return results

    def analyze_gaps(
        self,
        kb_name: str,
        threshold: int = 3,
    ) -> dict[str, Any]:
        """Analyze structural coverage gaps in a KB.

        Reports:
        - Entry types defined in kb.yaml with 0 entries
        - Entry types with fewer than *threshold* entries
        - Tags referenced in kb.yaml guidelines/goals with 0 entries
        - Entries with no outbound links (no outlinks)
        - Entries with no inbound links (unreferenced)
        - Distribution: entries per type, per tag (top 20), per importance band

        Returns a dict suitable for JSON serialisation or rich display.
        """
        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            return {"error": f"KB '{kb_name}' not found"}

        kb_schema = kb_config.kb_schema

        # -- 1. Collect the types this KB actually declares -----------------
        declared_types = set(kb_schema.declared_types()) if kb_schema else set()
        if not declared_types:
            # An untyped KB accepts every core type; a typed KB accepts only
            # the vocabulary returned by declared_types().
            from ..schema.core_types import CORE_TYPES

            declared_types.update(CORE_TYPES)

        # -- 2. Actual entry counts by type ----------------------------------
        type_count_rows = self.db.execute_sql(
            "SELECT entry_type, COUNT(*) as cnt FROM entry "
            "WHERE kb_name = :kb_name GROUP BY entry_type",
            {"kb_name": kb_name},
        )
        type_counts: dict[str, int] = {row["entry_type"]: row["cnt"] for row in type_count_rows}

        # Types with 0 entries
        empty_types = sorted(declared_types - set(type_counts.keys()))

        # Types below threshold
        sparse_types = sorted(
            [
                {"type": t, "count": c}
                for t, c in type_counts.items()
                if c < threshold and t in declared_types
            ],
            key=lambda x: x["count"],
        )

        # -- 3. Tags referenced in kb.yaml guidelines/goals but with 0 entries
        referenced_tags = self._extract_tags_from_schema(kb_schema)
        actual_tags = dict(self.db.get_all_tags(kb_name=kb_name))
        unused_tags = sorted(t for t in referenced_tags if t not in actual_tags)

        # -- 4. No-outlink entries -------------------------------------------
        no_outlink_rows = self.db.execute_sql(
            "SELECT e.id, e.entry_type, e.title FROM entry e "
            "WHERE e.kb_name = :kb_name "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM link l "
            "  WHERE l.source_id = e.id AND l.source_kb = e.kb_name"
            ") ORDER BY e.entry_type, e.id",
            {"kb_name": kb_name},
        )
        no_outlinks = [
            {"id": r["id"], "type": r["entry_type"], "title": r["title"]} for r in no_outlink_rows
        ]

        # -- 5. No-inlink entries (unreferenced) -----------------------------
        no_inlink_rows = self.db.execute_sql(
            "SELECT e.id, e.entry_type, e.title FROM entry e "
            "WHERE e.kb_name = :kb_name "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM link l "
            "  WHERE l.target_id = e.id AND l.target_kb = e.kb_name"
            ") ORDER BY e.entry_type, e.id",
            {"kb_name": kb_name},
        )
        no_inlinks = [
            {"id": r["id"], "type": r["entry_type"], "title": r["title"]} for r in no_inlink_rows
        ]

        # -- 6. Distribution stats -------------------------------------------
        # Entries per tag (top 20)
        all_tags = self.db.get_all_tags(kb_name=kb_name)
        tag_distribution = [
            {"tag": tag, "count": count}
            for tag, count in sorted(all_tags, key=lambda x: -x[1])[:20]
        ]

        # Entries per importance band
        importance_rows = self.db.execute_sql(
            "SELECT importance, COUNT(*) as cnt FROM entry "
            "WHERE kb_name = :kb_name AND importance IS NOT NULL "
            "GROUP BY importance ORDER BY importance",
            {"kb_name": kb_name},
        )
        importance_distribution = {str(row["importance"]): row["cnt"] for row in importance_rows}

        # Count entries with NULL importance
        null_importance_rows = self.db.execute_sql(
            "SELECT COUNT(*) as cnt FROM entry WHERE kb_name = :kb_name AND importance IS NULL",
            {"kb_name": kb_name},
        )
        null_importance = null_importance_rows[0]["cnt"] if null_importance_rows else 0
        if null_importance:
            importance_distribution["unset"] = null_importance

        # Total entry count
        total_rows = self.db.execute_sql(
            "SELECT COUNT(*) as cnt FROM entry WHERE kb_name = :kb_name",
            {"kb_name": kb_name},
        )
        total = total_rows[0]["cnt"] if total_rows else 0

        return {
            "kb_name": kb_name,
            "total_entries": total,
            "threshold": threshold,
            "empty_types": empty_types,
            "sparse_types": sparse_types,
            "unused_tags": unused_tags,
            "no_outlinks": no_outlinks,
            "no_inlinks": no_inlinks,
            "distribution": {
                "entries_per_type": dict(sorted(type_counts.items())),
                "top_tags": tag_distribution,
                "importance": importance_distribution,
            },
        }

    @staticmethod
    def _extract_tags_from_schema(kb_schema) -> set[str]:
        """Extract tag-like tokens referenced in kb.yaml guidelines and goals.

        Looks for words preceded by '#' or explicit tag references in
        guidelines/goals text values.
        """
        tags: set[str] = set()
        if not kb_schema:
            return tags

        # Scan guidelines and goals (dict[str, str])
        text_sources: list[str] = []
        if kb_schema.guidelines:
            text_sources.extend(v for v in kb_schema.guidelines.values() if isinstance(v, str))
        if kb_schema.goals:
            text_sources.extend(v for v in kb_schema.goals.values() if isinstance(v, str))

        # Also scan type-level guidelines/goals
        if kb_schema.types:
            for ts in kb_schema.types.values():
                if ts.guidelines and isinstance(ts.guidelines, str):
                    text_sources.append(ts.guidelines)
                if ts.goals and isinstance(ts.goals, str):
                    text_sources.append(ts.goals)

        # Extract #tag patterns
        for text in text_sources:
            tags.update(re.findall(r"#([a-zA-Z0-9_/-]+)", text))

        return tags

    @staticmethod
    def _days_since(iso_str: str, now: datetime) -> int:
        """Parse ISO timestamp and return days elapsed."""
        try:
            dt = datetime.fromisoformat(iso_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return (now - dt).days
        except (ValueError, TypeError):
            return 0

    # =========================================================================
    # Coverage stats — Tier A r2300
    # =========================================================================

    def coverage_stats(self, kb_name: str, entry_type: str | None = None) -> dict[str, Any]:
        """Aggregate curation-coverage stats for a KB.

        Answers the planning-level questions `pyrite qa validate`
        can't:

          - How many entries do we have? Broken down by type?
          - What status distribution? (stub/working/done/...)
          - What fraction have non-empty body?
          - What fraction have outbound wikilinks? Average per entry?
          - What fraction have structured sources attached?

        Args:
            kb_name: KB to summarize.
            entry_type: Restrict the report to one type when set.

        Returns:
            Dict shaped:
              {
                "kb_name": str,
                "entry_type": str | None,
                "total_entries": int,
                "by_type": {type: count, ...},
                "by_status": {status: count, ...},
                "body_coverage": {"with_body", "without_body", "total", "fraction"},
                "link_coverage": {"with_outlinks", "total_outlinks", "total", "avg_per_entry"},
                "source_coverage": {"with_sources", "total", "fraction"},
              }
        """
        # Build the WHERE clause once so we don't duplicate the filter
        # logic across each aggregation query. SQLite parameter binding
        # handles the values; the type filter is optional.
        params: dict[str, Any] = {"kb": kb_name}
        type_clause = ""
        if entry_type is not None:
            type_clause = " AND entry_type = :etype"
            params["etype"] = entry_type

        # 1. Total entries
        total_row = self.db.execute_sql(
            f"SELECT COUNT(*) AS n FROM entry WHERE kb_name = :kb{type_clause}",
            params,
        )
        total = int(total_row[0]["n"]) if total_row else 0

        if total == 0:
            return {
                "kb_name": kb_name,
                "entry_type": entry_type,
                "total_entries": 0,
                "by_type": {},
                "by_status": {},
                "body_coverage": {
                    "with_body": 0,
                    "without_body": 0,
                    "total": 0,
                    "fraction": 0.0,
                },
                "link_coverage": {
                    "with_outlinks": 0,
                    "total_outlinks": 0,
                    "total": 0,
                    "avg_per_entry": 0.0,
                },
                "source_coverage": {
                    "with_sources": 0,
                    "total": 0,
                    "fraction": 0.0,
                },
            }

        # 2. By type
        by_type_rows = self.db.execute_sql(
            f"SELECT entry_type, COUNT(*) AS n FROM entry "
            f"WHERE kb_name = :kb{type_clause} "
            "GROUP BY entry_type",
            params,
        )
        by_type = {r["entry_type"]: int(r["n"]) for r in by_type_rows}

        # 3. By status
        by_status_rows = self.db.execute_sql(
            f"SELECT status, COUNT(*) AS n FROM entry "
            f"WHERE kb_name = :kb{type_clause} "
            "GROUP BY status",
            params,
        )
        by_status = {(r["status"] or ""): int(r["n"]) for r in by_status_rows}

        # 4. Body coverage — non-empty body field
        body_rows = self.db.execute_sql(
            f"SELECT COUNT(*) AS n FROM entry "
            f"WHERE kb_name = :kb{type_clause} "
            "AND body IS NOT NULL AND TRIM(body) != ''",
            params,
        )
        with_body = int(body_rows[0]["n"]) if body_rows else 0
        body_coverage = {
            "with_body": with_body,
            "without_body": total - with_body,
            "total": total,
            "fraction": (with_body / total) if total else 0.0,
        }

        # 5. Link coverage — outbound wikilinks. Joins through the link
        # table where source_id is the entry id.
        outlink_rows = self.db.execute_sql(
            f"SELECT e.id AS eid, COUNT(l.id) AS n "
            f"FROM entry e LEFT JOIN link l "
            "ON l.source_id = e.id AND l.source_kb = e.kb_name "
            f"WHERE e.kb_name = :kb{(' AND e.entry_type = :etype' if entry_type else '')} "
            "GROUP BY e.id",
            params,
        )
        with_outlinks = sum(1 for r in outlink_rows if int(r["n"]) > 0)
        total_outlinks = sum(int(r["n"]) for r in outlink_rows)
        link_coverage = {
            "with_outlinks": with_outlinks,
            "total_outlinks": total_outlinks,
            "total": total,
            "avg_per_entry": (total_outlinks / total) if total else 0.0,
        }

        # 6. Source coverage — structured `sources:` field, joined
        # through the source table.
        source_rows = self.db.execute_sql(
            f"SELECT COUNT(DISTINCT e.id) AS n FROM entry e "
            "INNER JOIN source s ON s.entry_id = e.id AND s.kb_name = e.kb_name "
            f"WHERE e.kb_name = :kb"
            f"{(' AND e.entry_type = :etype' if entry_type else '')}",
            params,
        )
        with_sources = int(source_rows[0]["n"]) if source_rows else 0
        source_coverage = {
            "with_sources": with_sources,
            "total": total,
            "fraction": (with_sources / total) if total else 0.0,
        }

        return {
            "kb_name": kb_name,
            "entry_type": entry_type,
            "total_entries": total,
            "by_type": by_type,
            "by_status": by_status,
            "body_coverage": body_coverage,
            "link_coverage": link_coverage,
            "source_coverage": source_coverage,
        }
