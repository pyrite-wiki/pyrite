"""
Search, graph, analytics, and timeline queries.

Mixin class for read-only query operations.
Delegates knowledge-index queries to ``self._backend``.
Settings methods use ORM directly (app-state, not in SearchBackend).
"""

from datetime import UTC, datetime
from typing import Any

from .backends.base_backend import kb_names_clause
from .models import Setting


class QueryMixin:
    """Search, graph traversal, analytics, and timeline queries — delegates to backend."""

    # =========================================================================
    # Full-text and filtered search
    # =========================================================================

    def search(
        self,
        query: str,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
        entry_type: str | None = None,
        tags: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
        include_archived: bool = False,
        lifecycle: str | None = None,
        fips: str | None = None,
        state: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Full-text search across entries using FTS5."""
        return self._backend.search(
            query=query,
            kb_names=kb_names,
            kb_name=kb_name,
            entry_type=entry_type,
            tags=tags,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
            include_archived=include_archived,
            lifecycle=lifecycle,
            fips=fips,
            state=state,
            status=status,
        )

    def search_by_tag(
        self, tag: str, kb_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Search entries by tag."""
        return self._backend.search_by_tag(tag=tag, kb_name=kb_name, limit=limit)

    def search_by_date_range(
        self,
        date_from: str,
        date_to: str,
        kb_name: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Search events within a date range."""
        return self._backend.search_by_date_range(
            date_from=date_from, date_to=date_to, kb_name=kb_name, limit=limit
        )

    # =========================================================================
    # Graph queries (links)
    # =========================================================================

    def get_backlinks(
        self,
        entry_id: str,
        kb_name: str,
        limit: int = 0,
        offset: int = 0,
        *,
        readable_kbs: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get entries that link TO this entry, from KBs in ``readable_kbs``."""
        return self._backend.get_backlinks(
            entry_id=entry_id,
            kb_name=kb_name,
            limit=limit,
            offset=offset,
            readable_kbs=readable_kbs,
        )

    def get_outlinks(
        self, entry_id: str, kb_name: str, *, readable_kbs: set[str] | None = None
    ) -> list[dict[str, Any]]:
        """Get entries this entry links TO; an unreadable target reads as missing."""
        return self._backend.get_outlinks(
            entry_id=entry_id, kb_name=kb_name, readable_kbs=readable_kbs
        )

    def get_all_backlinks_for_kb(self, kb_name: str) -> dict[str, list[dict[str, Any]]]:
        """Get ALL backlinks targeting entries in a KB (1 query, not N)."""
        return self._backend.get_all_backlinks_for_kb(kb_name)

    def get_all_outlinks_for_kb(self, kb_name: str) -> dict[str, list[dict[str, Any]]]:
        """Get ALL outlinks from entries in a KB (1 query, not N)."""
        return self._backend.get_all_outlinks_for_kb(kb_name)

    def get_all_sources_for_kb(self, kb_name: str) -> dict[str, list[dict[str, Any]]]:
        """Get ALL sources for entries in a KB (1 query, not N)."""
        return self._backend.get_all_sources_for_kb(kb_name)

    def get_related(self, entry_id: str, kb_name: str, depth: int = 1) -> list[dict[str, Any]]:
        """Get related entries (both directions) up to N hops."""
        backlinks = self._backend.get_backlinks(entry_id, kb_name)
        outlinks = self._backend.get_outlinks(entry_id, kb_name)

        related = []
        seen = set()
        for link in backlinks + outlinks:
            key = (link.get("id"), link.get("kb_name"))
            if key not in seen and key != (entry_id, kb_name):
                seen.add(key)
                related.append(link)
        return related

    def get_graph_data(
        self,
        center: str | None = None,
        center_kb: str | None = None,
        kb_name: str | None = None,
        entry_type: str | None = None,
        depth: int = 2,
        limit: int = 500,
        *,
        readable_kbs: set[str] | None = None,
    ) -> dict[str, Any]:
        """Multi-hop BFS graph traversal returning nodes and edges, within ``readable_kbs``."""
        return self._backend.get_graph_data(
            center=center,
            center_kb=center_kb,
            kb_name=kb_name,
            entry_type=entry_type,
            depth=depth,
            limit=limit,
            readable_kbs=readable_kbs,
        )

    # =========================================================================
    # Edge endpoint queries
    # =========================================================================

    def get_edge_endpoints(self, entry_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Get edge endpoints for an edge-type entry."""
        return self._backend.get_edge_endpoints(entry_id, kb_name)

    def get_edges_by_endpoint(self, endpoint_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Get edge entries where this entity is an endpoint."""
        return self._backend.get_edges_by_endpoint(endpoint_id, kb_name)

    def get_edges_between(self, id_a: str, id_b: str, kb_name: str) -> list[dict[str, Any]]:
        """Get edge entries connecting two entities."""
        return self._backend.get_edges_between(id_a, id_b, kb_name)

    # =========================================================================
    # Analytics
    # =========================================================================

    def get_all_tags(
        self,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[tuple[str, int]]:
        """Get all tags with counts."""
        return self._backend.get_all_tags(kb_name=kb_name, kb_names=kb_names)

    def get_most_linked(self, kb_name: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Get entries with most incoming links (most referenced)."""
        return self._backend.get_most_linked(kb_name=kb_name, limit=limit)

    def get_orphans(self, kb_name: str | None = None) -> list[dict[str, Any]]:
        """Get entries with no links (neither incoming nor outgoing)."""
        return self._backend.get_orphans(kb_name=kb_name)

    def get_timeline(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        min_importance: int = 1,
        kb_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
        sort_order: str = "asc",
        kb_names: set[str] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get timeline events ordered by date."""
        return self._backend.get_timeline(
            date_from=date_from,
            date_to=date_to,
            min_importance=min_importance,
            kb_name=kb_name,
            limit=limit,
            offset=offset,
            sort_order=sort_order,
            kb_names=kb_names,
        )

    def get_global_counts(self, kb_names: set[str] | list[str] | None = None) -> dict[str, int]:
        """Get tag and link counts, restricted to ``kb_names`` when given."""
        return self._backend.get_global_counts(kb_names=kb_names)

    def get_tags_as_dicts(
        self,
        kb_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
        prefix: str | None = None,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get tags with counts as dicts, optionally filtered by KB and prefix."""
        return self._backend.get_tags_as_dicts(
            kb_name=kb_name, limit=limit, offset=offset, prefix=prefix, kb_names=kb_names
        )

    # =========================================================================
    # Object Refs
    # =========================================================================

    def get_refs_from(self, entry_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Get entries this entry references via object-ref fields."""
        return self._backend.get_refs_from(entry_id=entry_id, kb_name=kb_name)

    def get_refs_to(self, entry_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Get entries that reference this entry via object-ref fields."""
        return self._backend.get_refs_to(entry_id=entry_id, kb_name=kb_name)

    # =========================================================================
    # Settings (app-state — stays in ORM, not in SearchBackend)
    # =========================================================================

    def get_setting(self, key: str) -> str | None:
        """Get a setting value by key."""
        setting = self.session.query(Setting).filter_by(key=key).first()
        return setting.value if setting else None

    def set_setting(self, key: str, value: str) -> None:
        """Set a setting value (upsert)."""
        now = datetime.now(UTC).isoformat()
        existing = self.session.query(Setting).filter_by(key=key).first()
        if existing:
            existing.value = value
            existing.updated_at = now
        else:
            setting = Setting(key=key, value=value, updated_at=now)
            self.session.add(setting)
        self.session.commit()

    def get_all_settings(self) -> dict[str, str]:
        """Get all settings as a dict."""
        settings = self.session.query(Setting).all()
        return {s.key: s.value for s in settings}

    def delete_setting(self, key: str) -> bool:
        """Delete a setting. Returns True if deleted."""
        count = self.session.query(Setting).filter_by(key=key).delete()
        self.session.commit()
        return count > 0

    # =========================================================================
    # Tag hierarchy (computed from backend data)
    # =========================================================================

    def get_tag_tree(
        self,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Build hierarchical tag tree from /-separated tags."""
        flat_tags = self._backend.get_all_tags(kb_name, kb_names=kb_names)

        root_children: list[dict[str, Any]] = []
        node_map: dict[str, dict[str, Any]] = {}

        for tag_name, count in sorted(flat_tags, key=lambda t: t[0]):
            parts = tag_name.split("/")
            for i, part in enumerate(parts):
                full_path = "/".join(parts[: i + 1])
                if full_path not in node_map:
                    node: dict[str, Any] = {
                        "name": part,
                        "full_path": full_path,
                        "count": 0,
                        "children": [],
                    }
                    node_map[full_path] = node
                    if i == 0:
                        root_children.append(node)
                    else:
                        parent_path = "/".join(parts[:i])
                        node_map[parent_path]["children"].append(node)
            if tag_name in node_map:
                node_map[tag_name]["count"] = count

        return root_children

    # =========================================================================
    # Folder queries (for collections)
    # =========================================================================

    def list_entries_in_folder(
        self,
        kb_name: str,
        folder_path: str,
        sort_by: str = "title",
        sort_order: str = "asc",
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List entries whose file_path is within the given folder."""
        return self._backend.list_entries_in_folder(
            kb_name=kb_name,
            folder_path=folder_path,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )

    def count_entries_in_folder(self, kb_name: str, folder_path: str) -> int:
        """Count entries whose file_path is within the given folder."""
        return self._backend.count_entries_in_folder(kb_name=kb_name, folder_path=folder_path)

    def search_by_tag_prefix(
        self, prefix: str, kb_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Search entries by tag prefix (parent tag includes children)."""
        return self._backend.search_by_tag_prefix(prefix=prefix, kb_name=kb_name, limit=limit)

    # =========================================================================
    # Protocol-aware cross-type queries (ADR-0017)
    # =========================================================================

    def find_by_assignee(
        self,
        assignee: str,
        kb_name: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Find entries assigned to a specific agent/user, across all entry types.

        `kb_names` narrows in SQL (#223): filtering after ``LIMIT`` would
        drop unreadable rows and hand a scoped caller a short page.
        """
        from sqlalchemy import text

        sql = "SELECT * FROM entry WHERE assignee = :assignee"
        params: dict[str, Any] = {"assignee": assignee}
        if kb_name:
            sql += " AND kb_name = :kb_name"
            params["kb_name"] = kb_name
        scope = kb_names_clause("kb_name", kb_names, params)
        if scope:
            sql += f" AND {scope}"
        if status:
            sql += " AND status = :status"
            params["status"] = status
        sql += " ORDER BY updated_at DESC LIMIT :limit OFFSET :offset"
        params["limit"] = limit
        params["offset"] = offset

        result = self.session.execute(text(sql), params)
        cols = result.keys()
        return [dict(zip(cols, row, strict=False)) for row in result.fetchall()]

    def find_overdue(
        self,
        as_of: str | None = None,
        kb_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Find entries with due_date before a given date (default: now).

        Only returns entries that are not yet done or failed.

        `kb_names` narrows in SQL (#223): filtering after ``LIMIT`` would
        drop unreadable rows and hand a scoped caller a short page.
        """
        from sqlalchemy import text

        if not as_of:
            as_of = datetime.now(UTC).strftime("%Y-%m-%d")

        sql = (
            "SELECT * FROM entry WHERE due_date IS NOT NULL AND due_date != '' "
            "AND due_date < :as_of "
            "AND (status IS NULL OR status NOT IN ('done', 'failed'))"
        )
        params: dict[str, Any] = {"as_of": as_of}
        if kb_name:
            sql += " AND kb_name = :kb_name"
            params["kb_name"] = kb_name
        scope = kb_names_clause("kb_name", kb_names, params)
        if scope:
            sql += f" AND {scope}"
        sql += " ORDER BY due_date ASC LIMIT :limit OFFSET :offset"
        params["limit"] = limit
        params["offset"] = offset

        result = self.session.execute(text(sql), params)
        cols = result.keys()
        return [dict(zip(cols, row, strict=False)) for row in result.fetchall()]

    def find_by_status(
        self,
        status: str,
        kb_name: str | None = None,
        entry_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Find entries by status, across all entry types.

        `kb_names` narrows in SQL (#223): filtering after ``LIMIT`` would
        drop unreadable rows and hand a scoped caller a short page.
        """
        from sqlalchemy import text

        sql = "SELECT * FROM entry WHERE status = :status"
        params: dict[str, Any] = {"status": status}
        if kb_name:
            sql += " AND kb_name = :kb_name"
            params["kb_name"] = kb_name
        scope = kb_names_clause("kb_name", kb_names, params)
        if scope:
            sql += f" AND {scope}"
        if entry_type:
            sql += " AND entry_type = :entry_type"
            params["entry_type"] = entry_type
        sql += " ORDER BY updated_at DESC LIMIT :limit OFFSET :offset"
        params["limit"] = limit
        params["offset"] = offset

        result = self.session.execute(text(sql), params)
        cols = result.keys()
        return [dict(zip(cols, row, strict=False)) for row in result.fetchall()]

    def find_by_location(
        self,
        location: str,
        kb_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Find entries by location (substring match), across all entry types.

        `kb_names` narrows in SQL (#223): filtering after ``LIMIT`` would
        drop unreadable rows and hand a scoped caller a short page.
        """
        from sqlalchemy import text

        sql = "SELECT * FROM entry WHERE location LIKE :location"
        params: dict[str, Any] = {"location": f"%{location}%"}
        if kb_name:
            sql += " AND kb_name = :kb_name"
            params["kb_name"] = kb_name
        scope = kb_names_clause("kb_name", kb_names, params)
        if scope:
            sql += f" AND {scope}"
        sql += " ORDER BY updated_at DESC LIMIT :limit OFFSET :offset"
        params["limit"] = limit
        params["offset"] = offset

        result = self.session.execute(text(sql), params)
        cols = result.keys()
        return [dict(zip(cols, row, strict=False)) for row in result.fetchall()]
