"""
Wikilink Service — resolution, autocomplete, and wanted-page queries.

Extracted from KBService to slim it down. All methods are read-only
and use PyriteDB.execute_sql() for queries.
"""

import logging
from typing import Any

from ..config import PyriteConfig
from ..storage.database import PyriteDB

logger = logging.getLogger(__name__)


def _scope_clause(
    column: str, readable_kbs: set[str] | None, tag: str = "scope"
) -> tuple[str, dict[str, Any]]:
    """An ``AND column IN (...)`` narrowing to the readable set, as named
    params ``:<tag>0``, ``:<tag>1``, ... (a distinct ``tag`` per clause when a
    query has two).

    None is the unscoped caller: no narrowing. An empty set matches nothing.
    """
    if readable_kbs is None:
        return "", {}
    if not readable_kbs:
        return " AND 1 = 0", {}
    names = sorted(readable_kbs)
    params = {f"{tag}{i}": name for i, name in enumerate(names)}
    placeholders = ",".join(f":{tag}{i}" for i in range(len(names)))
    return f" AND {column} IN ({placeholders})", params


class WikilinkService:
    """Wikilink resolution, autocomplete titles, and wanted-page queries.

    Every method takes ``readable_kbs``, the caller's readable set (None:
    unscoped), and pushes it into its SQL. A KB named inside a target's
    ``kb:`` or shortname prefix is authorized against the same set: one the
    caller cannot read is treated as an unknown prefix, which is how a KB
    that does not exist is treated (P-R2, P-R5).
    """

    def __init__(self, config: PyriteConfig, db: PyriteDB):
        self.config = config
        self.db = db

    def list_entry_titles(
        self,
        kb_name: str | None = None,
        query: str | None = None,
        limit: int = 500,
        readable_kbs: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Lightweight listing of entry IDs and titles for wikilink autocomplete."""
        sql = "SELECT id, kb_name, entry_type, title, json_extract(metadata, '$.aliases') as aliases FROM entry WHERE 1=1"
        params: dict[str, Any] = {}

        if kb_name:
            sql += " AND kb_name = :kb_name"
            params["kb_name"] = kb_name
        scope_sql, scope_params = _scope_clause("kb_name", readable_kbs)
        sql += scope_sql
        params.update(scope_params)
        if query:
            sql += " AND (title LIKE :q1 OR json_extract(metadata, '$.aliases') LIKE :q2)"
            params["q1"] = f"%{query}%"
            params["q2"] = f"%{query}%"

        sql += " ORDER BY title COLLATE NOCASE LIMIT :limit"
        params["limit"] = limit

        return self.db.execute_sql(sql, params)

    def _prefixed_kb(self, prefix: str, readable_kbs: set[str] | None) -> str | None:
        """The KB a ``prefix:`` names -- a shortname or a KB name -- if the
        caller may read it. An unreadable KB is answered as an unknown
        prefix, which is what a KB that does not exist gets."""
        kb_by_short = self.config.get_kb_by_shortname(prefix)
        name = kb_by_short.name if kb_by_short else (prefix if self.config.get_kb(prefix) else None)
        if name is None or (readable_kbs is not None and name not in readable_kbs):
            return None
        return name

    def resolve_entry(
        self,
        target: str,
        kb_name: str | None = None,
        readable_kbs: set[str] | None = None,
    ) -> dict[str, Any] | None:
        """Resolve a wikilink target to an entry. Supports kb:id format for cross-KB links."""
        # Parse cross-KB format
        actual_target = target
        actual_kb = kb_name
        if ":" in target and not target.startswith("http"):
            prefix, rest = target.split(":", 1)
            prefixed = self._prefixed_kb(prefix, readable_kbs)
            if prefixed:
                actual_target = rest
                actual_kb = prefixed
        scope_sql, scope_params = _scope_clause("kb_name", readable_kbs)

        # First pass: exact ID match
        sql = "SELECT id, kb_name, entry_type, title FROM entry WHERE id = :target"
        params: dict[str, Any] = {"target": actual_target, **scope_params}
        if actual_kb:
            sql += " AND kb_name = :kb_name"
            params["kb_name"] = actual_kb
        sql += scope_sql + " LIMIT 1"

        rows = self.db.execute_sql(sql, params)
        if rows:
            return rows[0]

        # Second pass: title match
        sql = "SELECT id, kb_name, entry_type, title FROM entry WHERE title LIKE :target"
        params = {"target": actual_target, **scope_params}
        if actual_kb:
            sql += " AND kb_name = :kb_name"
            params["kb_name"] = actual_kb
        sql += scope_sql + " LIMIT 1"

        rows = self.db.execute_sql(sql, params)
        if rows:
            return rows[0]

        # Third pass: search aliases
        sql = """
            SELECT id, kb_name, entry_type, title FROM entry
            WHERE json_extract(metadata, '$.aliases') LIKE :alias_pattern
        """
        params = {"alias_pattern": f"%{actual_target}%", **scope_params}
        if actual_kb:
            sql += " AND kb_name = :kb_name"
            params["kb_name"] = actual_kb
        sql += scope_sql + " LIMIT 1"

        rows = self.db.execute_sql(sql, params)
        return rows[0] if rows else None

    def resolve_batch(
        self,
        targets: list[str],
        kb_name: str | None = None,
        readable_kbs: set[str] | None = None,
    ) -> dict[str, bool]:
        """Batch-resolve wikilink targets. Supports kb:id format."""
        if not targets:
            return {}
        result: dict[str, bool] = {}

        # Separate cross-KB targets from same-KB targets
        simple_targets = []
        for t in targets:
            if ":" in t and not t.startswith("http"):
                # Resolve cross-KB targets individually
                resolved = self.resolve_entry(t, kb_name, readable_kbs=readable_kbs)
                result[t] = resolved is not None
            else:
                simple_targets.append(t)

        if simple_targets:
            placeholders = ",".join([f":t{i}" for i in range(len(simple_targets))])
            sql = f"SELECT id FROM entry WHERE id IN ({placeholders})"
            params: dict[str, Any] = {f"t{i}": t for i, t in enumerate(simple_targets)}
            if kb_name:
                sql += " AND kb_name = :kb_name"
                params["kb_name"] = kb_name
            scope_sql, scope_params = _scope_clause("kb_name", readable_kbs)
            sql += scope_sql
            params.update(scope_params)
            rows = self.db.execute_sql(sql, params)
            existing_ids = {r["id"] for r in rows}
            for t in simple_targets:
                result[t] = t in existing_ids

        return result

    def get_wanted_pages(
        self,
        kb_name: str | None = None,
        limit: int = 100,
        readable_kbs: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get link targets that don't exist as entries (wanted pages).

        Scoped: only links from readable sources count, and an entry the
        caller cannot read does not count as existing -- a link into an
        unreadable KB is wanted exactly as a link into a missing KB is. The
        target's KB and id are the readable source's own text.
        """
        source_scope, source_params = _scope_clause("l.source_kb", readable_kbs, "src")
        target_scope, target_params = _scope_clause("e.kb_name", readable_kbs, "tgt")
        sql = f"""
            SELECT l.target_id, l.target_kb, COUNT(*) as ref_count,
                   GROUP_CONCAT(DISTINCT l.source_id) as referenced_by
            FROM link l
            LEFT JOIN entry e ON l.target_id = e.id AND l.target_kb = e.kb_name{target_scope}
            WHERE e.id IS NULL{source_scope}
        """
        params: dict[str, Any] = {**source_params, **target_params}
        if kb_name:
            sql += " AND l.target_kb = :kb_name"
            params["kb_name"] = kb_name
        sql += " GROUP BY l.target_id, l.target_kb ORDER BY ref_count DESC LIMIT :limit"
        params["limit"] = limit
        return self.db.execute_sql(sql, params)

    def check_links(
        self,
        kb_name: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Check for broken links, grouped by missing target.

        Returns missing targets sorted by reference count (most-referenced
        first). Each result includes the list of entries that reference it.

        Args:
            kb_name: Filter to links originating from this KB.
            limit: Max missing targets to return.

        Returns list of dicts:
            target_id, target_kb, ref_count, references (list of
            {source_id, source_kb, relation})
        """
        # Get per-link rows, then group in Python for nested structure
        sql = """
            SELECT l.source_id, l.source_kb, l.target_id, l.target_kb, l.relation
            FROM link l
            LEFT JOIN entry e ON l.target_id = e.id AND l.target_kb = e.kb_name
            WHERE e.id IS NULL
        """
        params: dict[str, Any] = {}
        if kb_name:
            sql += " AND l.source_kb = :kb_name"
            params["kb_name"] = kb_name
        sql += " ORDER BY l.target_kb, l.target_id"
        rows = self.db.execute_sql(sql, params)

        # Group by target
        targets: dict[tuple[str, str], list[dict[str, str]]] = {}
        for row in rows:
            key = (row["target_id"], row["target_kb"])
            targets.setdefault(key, []).append(
                {
                    "source_id": row["source_id"],
                    "source_kb": row["source_kb"],
                    "relation": row["relation"],
                }
            )

        # Build result sorted by ref_count desc, apply limit
        result = []
        for (target_id, target_kb), refs in sorted(targets.items(), key=lambda x: -len(x[1])):
            result.append(
                {
                    "target_id": target_id,
                    "target_kb": target_kb,
                    "ref_count": len(refs),
                    "references": refs,
                }
            )
        return result[:limit]
