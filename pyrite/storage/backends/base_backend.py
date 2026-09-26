"""
BaseBackend — shared ORM and SQL logic for SQLite and Postgres search backends.

Concrete subclasses must implement:
- ``close()``
- ``_exec(sql, params)`` / ``_exec_one(sql, params)`` / ``_exec_scalar(sql, params)``
- ``_sync_links(entry_id, kb_name, links)``
- ``search(...)``
- ``search_by_tag(...)``, ``search_by_date_range(...)``, ``search_by_tag_prefix(...)``
- All embedding methods (``upsert_embedding``, ``search_semantic``, etc.)
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ...utils.json_utils import SafeEncoder as _SafeEncoder
from ..models import Block, EdgeEndpoint, Entry, EntryRef, EntryTag, Link, Source, Tag


def kb_names_clause(
    column: str, kb_names: set[str] | list[str] | None, params: dict[str, Any]
) -> str | None:
    """SQL predicate restricting ``column`` to ``kb_names``, binding params.

    ``None`` means "not scoped" -- no predicate. An *empty* set means
    "this caller may read nothing", which must match no rows, not every
    row: ``IN ()`` is not valid SQLite, so it becomes ``1 = 0``.

    Module-level so the facade's four protocol finders
    (``pyrite/storage/queries.py``) apply the same rule as the backend's own
    SQL -- one copy of it, not two (#223).
    """
    if kb_names is None:
        return None
    names = list(kb_names)
    if not names:
        return "1 = 0"
    keys = []
    for i, name in enumerate(names):
        key = f"kbn_{i}"
        params[key] = name
        keys.append(f":{key}")
    return f"{column} IN ({', '.join(keys)})"


class BaseBackend(ABC):
    """Shared ORM and raw-SQL logic for search backends."""

    _session: Session

    _SORT_COLUMNS = {"title", "updated_at", "created_at", "entry_type"}

    # =====================================================================
    # Raw SQL helpers — subclasses must provide these
    # =====================================================================

    @abstractmethod
    def _exec(self, sql: str, params: dict | None = None) -> list[dict[str, Any]]:
        """Execute raw SQL and return all rows as list of dicts."""
        ...

    @abstractmethod
    def _exec_one(self, sql: str, params: dict | None = None) -> dict | None:
        """Execute raw SQL and return first row as dict, or None."""
        ...

    @abstractmethod
    def _exec_scalar(self, sql: str, params: dict | None = None):
        """Execute raw SQL and return scalar value."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Release backend resources."""
        ...

    # =====================================================================
    # Entry CRUD (ORM-based — shared)
    # =====================================================================

    def upsert_entry(self, entry_data: dict[str, Any]) -> None:
        """Insert or update an entry with tags, sources, links, refs, blocks."""
        entry_id = entry_data.get("id")
        kb_name = entry_data.get("kb_name")
        try:
            self._upsert_entry_main(entry_id, kb_name, entry_data)
            self._sync_tags(entry_id, kb_name, entry_data.get("tags", []))
            self._sync_sources(entry_id, kb_name, entry_data.get("sources", []))
            self._sync_links(entry_id, kb_name, entry_data.get("links", []))
            self._sync_entry_refs(entry_id, kb_name, entry_data)
            self._sync_blocks(entry_id, kb_name, entry_data)
            self._sync_edge_endpoints(entry_id, kb_name, entry_data)
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def _upsert_entry_main(self, entry_id: str, kb_name: str, entry_data: dict[str, Any]) -> None:
        metadata = entry_data.get("metadata", {})
        metadata_json = json.dumps(metadata, cls=_SafeEncoder) if metadata is not None else "{}"

        existing = self._session.get(Entry, (entry_id, kb_name))
        if existing:
            existing.entry_type = entry_data.get("entry_type")
            existing.title = entry_data.get("title")
            existing.body = entry_data.get("body")
            existing.summary = entry_data.get("summary")
            existing.file_path = entry_data.get("file_path")
            existing.content_hash = entry_data.get("content_hash")
            existing.date = entry_data.get("date")
            existing.importance = entry_data.get("importance")
            existing.status = entry_data.get("status")
            existing.location = entry_data.get("location")
            # Protocol columns (ADR-0017)
            existing.assignee = entry_data.get("assignee")
            existing.assigned_at = entry_data.get("assigned_at")
            existing.priority = entry_data.get("priority")
            existing.due_date = entry_data.get("due_date")
            existing.start_date = entry_data.get("start_date")
            existing.end_date = entry_data.get("end_date")
            existing.coordinates = entry_data.get("coordinates")
            existing.fips = entry_data.get("fips")
            existing.state = entry_data.get("state")
            existing.lifecycle = entry_data.get("lifecycle", "active")
            existing.extra_data = metadata_json
            existing.updated_at = entry_data.get("updated_at")
            # Microsecond precision + explicit UTC offset — second-level
            # truncation caused `_is_stale` to fire immediately after
            # indexing when file mtimes have sub-second precision.
            existing.indexed_at = datetime.now(UTC).isoformat(timespec="microseconds")
            if entry_data.get("created_by") and not existing.created_by:
                existing.created_by = entry_data.get("created_by")
            if entry_data.get("modified_by"):
                existing.modified_by = entry_data.get("modified_by")
        else:
            entry = Entry(
                id=entry_id,
                kb_name=kb_name,
                entry_type=entry_data.get("entry_type"),
                title=entry_data.get("title"),
                body=entry_data.get("body"),
                summary=entry_data.get("summary"),
                file_path=entry_data.get("file_path"),
                content_hash=entry_data.get("content_hash"),
                date=entry_data.get("date"),
                importance=entry_data.get("importance"),
                status=entry_data.get("status"),
                location=entry_data.get("location"),
                # Protocol columns (ADR-0017)
                assignee=entry_data.get("assignee"),
                assigned_at=entry_data.get("assigned_at"),
                priority=entry_data.get("priority"),
                due_date=entry_data.get("due_date"),
                start_date=entry_data.get("start_date"),
                end_date=entry_data.get("end_date"),
                coordinates=entry_data.get("coordinates"),
                fips=entry_data.get("fips"),
                state=entry_data.get("state"),
                lifecycle=entry_data.get("lifecycle", "active"),
                extra_data=metadata_json,
                created_at=entry_data.get("created_at"),
                updated_at=entry_data.get("updated_at"),
                # Always set indexed_at explicitly — the Column's
                # server_default="CURRENT_TIMESTAMP" stores the literal
                # string under some SQLAlchemy/SQLite combinations, which
                # breaks `_is_stale` on every subsequent sync.
                # Microsecond precision + explicit UTC offset so
                # `_is_stale` comparisons against sub-second file mtimes
                # don't produce false positives immediately after index.
                indexed_at=datetime.now(UTC).isoformat(timespec="microseconds"),
                created_by=entry_data.get("created_by"),
                modified_by=entry_data.get("modified_by"),
            )
            self._session.add(entry)
        self._session.flush()

    def _sync_tags(self, entry_id: str, kb_name: str, tags: list[str]) -> None:
        self._session.query(EntryTag).filter_by(entry_id=entry_id, kb_name=kb_name).delete()
        self._session.flush()
        seen: set[str] = set()
        for tag_name in tags:
            if not tag_name or tag_name in seen:
                continue
            seen.add(tag_name)
            tag = self._session.query(Tag).filter_by(name=tag_name).first()
            if not tag:
                tag = Tag(name=tag_name)
                self._session.add(tag)
                self._session.flush()
            self._session.add(EntryTag(entry_id=entry_id, kb_name=kb_name, tag_id=tag.id))

    def _sync_sources(self, entry_id: str, kb_name: str, sources: list[dict[str, Any]]) -> None:
        self._session.query(Source).filter_by(entry_id=entry_id, kb_name=kb_name).delete()
        for src in sources:
            self._session.add(
                Source(
                    entry_id=entry_id,
                    kb_name=kb_name,
                    title=src.get("title", ""),
                    url=src.get("url", ""),
                    outlet=src.get("outlet", ""),
                    date=src.get("date", ""),
                    verified=1 if src.get("verified") else 0,
                )
            )

    @abstractmethod
    def _sync_links(self, entry_id: str, kb_name: str, links: list[dict[str, Any]]) -> None:
        """Sync links — implementation differs between SQLite and Postgres."""
        ...

    def _sync_entry_refs(self, entry_id: str, kb_name: str, entry_data: dict) -> None:
        self._session.query(EntryRef).filter_by(source_id=entry_id, source_kb=kb_name).delete()
        for ref in entry_data.get("_refs", []):
            self._session.add(
                EntryRef(
                    source_id=entry_id,
                    source_kb=kb_name,
                    target_id=ref["target_id"],
                    target_kb=ref.get("target_kb", kb_name),
                    field_name=ref["field_name"],
                    target_type=ref.get("target_type"),
                )
            )

    def _sync_blocks(self, entry_id: str, kb_name: str, entry_data: dict) -> None:
        self._session.query(Block).filter_by(entry_id=entry_id, kb_name=kb_name).delete()
        for blk in entry_data.get("_blocks", []):
            self._session.add(
                Block(
                    entry_id=entry_id,
                    kb_name=kb_name,
                    block_id=blk["block_id"],
                    heading=blk.get("heading"),
                    content=blk["content"],
                    position=blk["position"],
                    block_type=blk["block_type"],
                )
            )

    def _sync_edge_endpoints(self, entry_id: str, kb_name: str, entry_data: dict) -> None:
        self._session.query(EdgeEndpoint).filter_by(
            edge_entry_id=entry_id, edge_entry_kb=kb_name
        ).delete()
        for ep in entry_data.get("_edge_endpoints", []):
            self._session.add(
                EdgeEndpoint(
                    edge_entry_id=entry_id,
                    edge_entry_kb=kb_name,
                    role=ep["role"],
                    field_name=ep["field_name"],
                    endpoint_id=ep["endpoint_id"],
                    endpoint_kb=ep.get("endpoint_kb", kb_name),
                    edge_type=ep["edge_type"],
                )
            )

    def delete_entry(self, entry_id: str, kb_name: str) -> bool:
        count = self._session.query(Entry).filter_by(id=entry_id, kb_name=kb_name).delete()
        self._session.commit()
        return count > 0

    def get_entry(self, entry_id: str, kb_name: str) -> dict[str, Any] | None:
        entry = self._session.get(Entry, (entry_id, kb_name))
        if not entry:
            return None
        result = self._entry_to_dict(entry)
        result["tags"] = self._get_entry_tags(entry_id, kb_name)
        result["sources"] = self._get_entry_sources(entry_id, kb_name)
        result["links"] = self._get_entry_links(entry_id, kb_name)
        return result

    def get_entries(self, ids: list[tuple[str, str]]) -> list[dict[str, Any]]:
        """Batch-get multiple entries by (entry_id, kb_name) pairs."""
        if not ids:
            return []
        from sqlalchemy import tuple_

        entries = self._session.query(Entry).filter(tuple_(Entry.id, Entry.kb_name).in_(ids)).all()
        tag_map = self._get_tags_for_entries(ids)
        results = []
        for entry in entries:
            d = self._entry_to_dict(entry)
            d["tags"] = tag_map.get((entry.id, entry.kb_name), [])
            d["sources"] = self._get_entry_sources(entry.id, entry.kb_name)
            d["links"] = self._get_entry_links(entry.id, entry.kb_name)
            results.append(d)
        return results

    @staticmethod
    def _parse_metadata(raw: Any) -> dict:
        """Parse extra_data into a dict, handling JSON strings and edge cases."""
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    def _entry_to_dict(self, entry: Entry) -> dict[str, Any]:
        metadata = self._parse_metadata(entry.extra_data)
        result = {
            "id": entry.id,
            "kb_name": entry.kb_name,
            "entry_type": entry.entry_type,
            "title": entry.title,
            "body": entry.body,
            "summary": entry.summary,
            "file_path": entry.file_path,
            "date": entry.date,
            "importance": entry.importance,
            "status": entry.status,
            "location": entry.location,
            "lifecycle": entry.lifecycle or "active",
            # Protocol fields (ADR-0017)
            "assignee": entry.assignee,
            "assigned_at": entry.assigned_at,
            "priority": entry.priority,
            "due_date": entry.due_date,
            "start_date": entry.start_date,
            "end_date": entry.end_date,
            "coordinates": entry.coordinates,
            "metadata": metadata,
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
            "indexed_at": entry.indexed_at,
            "created_by": entry.created_by,
            "modified_by": entry.modified_by,
        }
        # Lift well-known top-level fields that live in metadata for legacy
        # reasons (the software-kb plugin stores rank/effort/kind in the
        # `metadata` JSON bag rather than as Prioritizable protocol fields).
        # Consumers expect them at the top level — `pyrite sw backlog` already
        # does this lift in its own projection. Surface them here so
        # `pyrite get`, REST `/entries/{id}`, and the MCP `kb_get` tool all
        # see the same shape. Regression for Tier A 1200
        # (bug-pyrite-get-omits-rank-field-in-json-output).
        for key in ("rank", "effort", "kind"):
            if key in metadata and key not in result:
                result[key] = metadata[key]
        # status_reason is the per-transition audit field for the
        # relaxed-mode state machine (Tier A r1175). Plugins and the
        # task migration script read it directly off the entry dict;
        # lift here so consumers don't have to special-case
        # entry["metadata"]["status_reason"].
        if "status_reason" in metadata and "status_reason" not in result:
            result["status_reason"] = metadata["status_reason"]
        # parked_awaiting is the conductor/dispatch-classifier convention for
        # marking a task legitimately waiting rather than stalled (not a
        # schema field on TaskEntry — it now round-trips into metadata via
        # TaskEntry's unknown-frontmatter-key preservation). Lift here so
        # TaskService.list_tasks()'s hydration path (which reads
        # entry.get("parked_awaiting") directly) sees it.
        # See: issue-pyrite-task-update-strips-non-schema-frontmatter-fields-silently-unparks-monitors
        if "parked_awaiting" in metadata and "parked_awaiting" not in result:
            result["parked_awaiting"] = metadata["parked_awaiting"]
        return result

    def _get_entry_tags(self, entry_id: str, kb_name: str) -> list[str]:
        results = (
            self._session.query(Tag.name)
            .join(EntryTag, Tag.id == EntryTag.tag_id)
            .filter(EntryTag.entry_id == entry_id, EntryTag.kb_name == kb_name)
            .all()
        )
        return [r[0] for r in results]

    def _get_tags_for_entries(
        self, entry_ids: list[tuple[str, str]]
    ) -> dict[tuple[str, str], list[str]]:
        """Batch-fetch tags for multiple entries. Returns {(entry_id, kb_name): [tag_names]}."""
        if not entry_ids:
            return {}
        from sqlalchemy import tuple_

        result: dict[tuple[str, str], list[str]] = {k: [] for k in entry_ids}
        rows = (
            self._session.query(EntryTag.entry_id, EntryTag.kb_name, Tag.name)
            .join(Tag, Tag.id == EntryTag.tag_id)
            .filter(tuple_(EntryTag.entry_id, EntryTag.kb_name).in_(entry_ids))
            .all()
        )
        for entry_id, kb_name, tag_name in rows:
            result[(entry_id, kb_name)].append(tag_name)
        return result

    def _get_entry_sources(self, entry_id: str, kb_name: str) -> list[dict[str, Any]]:
        sources = self._session.query(Source).filter_by(entry_id=entry_id, kb_name=kb_name).all()
        return [
            {
                "id": s.id,
                "entry_id": s.entry_id,
                "kb_name": s.kb_name,
                "title": s.title,
                "url": s.url,
                "outlet": s.outlet,
                "date": s.date,
                "verified": s.verified,
            }
            for s in sources
        ]

    def _get_entry_links(self, entry_id: str, kb_name: str) -> list[dict[str, Any]]:
        links = (
            self._session.query(Link.target_id, Link.target_kb, Link.relation, Link.note)
            .filter_by(source_id=entry_id, source_kb=kb_name)
            .all()
        )
        return [
            {"target_id": l[0], "target_kb": l[1], "relation": l[2], "note": l[3]} for l in links
        ]

    # =====================================================================
    # List / count / types (ORM-based — shared)
    # =====================================================================

    def list_entries(
        self,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
        entry_type: str | None = None,
        tag: str | None = None,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
        limit: int = 50,
        offset: int = 0,
        include_archived: bool = False,
        status: str | None = None,
        min_importance: int | None = None,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import func as sa_func

        query = self._session.query(Entry)
        if not include_archived:
            query = query.filter(sa_func.coalesce(Entry.lifecycle, "active") != "archived")
        if tag:
            query = (
                query.join(
                    EntryTag, (Entry.id == EntryTag.entry_id) & (Entry.kb_name == EntryTag.kb_name)
                )
                .join(Tag, EntryTag.tag_id == Tag.id)
                .filter(Tag.name == tag)
            )
        if kb_names is not None:
            query = query.filter(Entry.kb_name.in_(list(kb_names)))
        if kb_name:
            query = query.filter(Entry.kb_name == kb_name)
        if entry_type:
            query = query.filter(Entry.entry_type == entry_type)
        if status:
            query = query.filter(Entry.status == status)
        if min_importance is not None:
            query = query.filter(Entry.importance >= min_importance)
        query = query.distinct()

        col = sort_by if sort_by in self._SORT_COLUMNS else "updated_at"
        sort_col = getattr(Entry, col)
        if sort_order.lower() == "asc":
            query = query.order_by(sort_col.asc())
        else:
            query = query.order_by(sort_col.desc())
        if col != "updated_at":
            query = query.order_by(Entry.updated_at.desc())

        query = query.limit(limit).offset(offset)
        rows = query.all()
        entries = []
        for entry in rows:
            e = self._entry_to_dict(entry)
            e["tags"] = self._get_entry_tags(entry.id, entry.kb_name)
            entries.append(e)
        return entries

    def count_entries(
        self,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
        entry_type: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        min_importance: int | None = None,
    ) -> int:
        from sqlalchemy import func

        query = self._session.query(func.count(func.distinct(Entry.id)))
        if tag:
            query = (
                query.join(
                    EntryTag, (Entry.id == EntryTag.entry_id) & (Entry.kb_name == EntryTag.kb_name)
                )
                .join(Tag, EntryTag.tag_id == Tag.id)
                .filter(Tag.name == tag)
            )
        if kb_names is not None:
            query = query.filter(Entry.kb_name.in_(list(kb_names)))
        if kb_name:
            query = query.filter(Entry.kb_name == kb_name)
        if entry_type:
            query = query.filter(Entry.entry_type == entry_type)
        if status:
            query = query.filter(Entry.status == status)
        if min_importance is not None:
            query = query.filter(Entry.importance >= min_importance)
        return query.scalar() or 0

    def get_distinct_types(
        self,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[str]:
        query = (
            self._session.query(Entry.entry_type).filter(Entry.entry_type.isnot(None)).distinct()
        )
        if kb_names is not None:
            query = query.filter(Entry.kb_name.in_(list(kb_names)))
        if kb_name:
            query = query.filter(Entry.kb_name == kb_name)
        query = query.order_by(Entry.entry_type)
        return [r[0] for r in query.all()]

    def get_entries_for_indexing(self, kb_name: str) -> list[dict[str, Any]]:
        rows = (
            self._session.query(Entry.id, Entry.file_path, Entry.indexed_at, Entry.content_hash)
            .filter_by(kb_name=kb_name)
            .all()
        )
        return [
            {"id": r[0], "file_path": r[1], "indexed_at": r[2], "content_hash": r[3]} for r in rows
        ]

    # =====================================================================
    # Full-text search — subclasses must override
    # =====================================================================

    @abstractmethod
    def search(
        self,
        query: str,
        kb_name: str | None = None,
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
        """Full-text search — FTS5 (SQLite) or tsvector (Postgres)."""
        ...

    @abstractmethod
    def search_by_tag(
        self, tag: str, kb_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def search_by_date_range(
        self,
        date_from: str,
        date_to: str,
        kb_name: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def search_by_tag_prefix(
        self, prefix: str, kb_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]: ...

    # =====================================================================
    # Semantic search — subclasses must override
    # =====================================================================

    @abstractmethod
    def upsert_embedding(self, entry_id: str, kb_name: str, embedding: list[float]) -> bool: ...

    @abstractmethod
    def search_semantic(
        self,
        embedding: list[float],
        kb_name: str | None = None,
        limit: int = 20,
        max_distance: float = 1.3,
        entry_type: str | None = None,
        tags: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        fips: str | None = None,
        state: str | None = None,
        status: str | None = None,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def has_embeddings(self) -> bool: ...

    @abstractmethod
    def embedding_stats(self) -> dict[str, Any]: ...

    @abstractmethod
    def get_embedded_rowids(self) -> set[int]: ...

    @abstractmethod
    def get_entries_for_embedding(self, kb_name: str | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def delete_embedding(self, entry_id: str, kb_name: str) -> None: ...

    # =====================================================================
    # Graph queries (links) — shared via _exec helpers
    # =====================================================================

    def get_backlinks(
        self,
        entry_id: str,
        kb_name: str,
        limit: int = 0,
        offset: int = 0,
        *,
        readable_kbs: set[str] | None,
    ) -> list[dict[str, Any]]:
        """Entries linking TO this one.

        ``readable_kbs`` (the caller's ``ReadScope`` set; ``None`` unscoped)
        drops a source the caller cannot read -- the same answer as a source
        that does not exist, which never has a row here (P-R4, P-R5).
        """
        sql = """
            SELECT e.id, e.kb_name, e.title, e.entry_type,
                   l.inverse_relation as relation, l.note
            FROM link l
            JOIN entry e ON l.source_id = e.id AND l.source_kb = e.kb_name
            WHERE l.target_id = :entry_id AND l.target_kb = :kb_name
        """
        params: dict[str, Any] = {"entry_id": entry_id, "kb_name": kb_name}
        scope = kb_names_clause("e.kb_name", readable_kbs, params)
        if scope:
            sql += f" AND {scope}"
        if limit > 0:
            sql += " LIMIT :limit OFFSET :offset"
            params["limit"] = limit
            params["offset"] = offset
        return self._exec(sql, params)

    def get_outlinks(
        self, entry_id: str, kb_name: str, *, readable_kbs: set[str] | None
    ) -> list[dict[str, Any]]:
        """Entries this one links TO.

        The row itself comes from the source's own body, so it is kept; with
        ``readable_kbs`` set the join to the target only matches a readable
        KB, and a target the caller cannot read comes back exactly as a
        missing one does -- ``title`` and ``entry_type`` null (P-R5).
        """
        params: dict[str, Any] = {"entry_id": entry_id, "kb_name": kb_name}
        scope = kb_names_clause("e.kb_name", readable_kbs, params)
        return self._exec(
            f"""
            SELECT l.target_id as id, l.target_kb as kb_name,
                   e.title, e.entry_type, l.relation, l.note
            FROM link l
            LEFT JOIN entry e ON l.target_id = e.id AND l.target_kb = e.kb_name
                {f"AND {scope}" if scope else ""}
            WHERE l.source_id = :entry_id AND l.source_kb = :kb_name
            """,
            params,
        )

    def get_all_backlinks_for_kb(self, kb_name: str) -> dict[str, list[dict[str, Any]]]:
        """Get ALL backlinks targeting entries in a KB in one query."""
        rows = self._exec(
            """
            SELECT l.target_id, e.id, e.kb_name, e.title, e.entry_type,
                   l.inverse_relation as relation, l.note
            FROM link l
            JOIN entry e ON l.source_id = e.id AND l.source_kb = e.kb_name
            WHERE l.target_kb = :kb_name
            """,
            {"kb_name": kb_name},
        )
        result: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            d = dict(row)
            target_id = d.pop("target_id")
            result[target_id].append(d)
        return result

    def get_all_outlinks_for_kb(self, kb_name: str) -> dict[str, list[dict[str, Any]]]:
        """Get ALL outlinks from entries in a KB in one query."""
        rows = self._exec(
            """
            SELECT l.source_id,
                   l.target_id as id, l.target_kb as kb_name,
                   e.title, e.entry_type, l.relation, l.note
            FROM link l
            LEFT JOIN entry e ON l.target_id = e.id AND l.target_kb = e.kb_name
            WHERE l.source_kb = :kb_name
            """,
            {"kb_name": kb_name},
        )
        result: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            d = dict(row)
            source_id = d.pop("source_id")
            result[source_id].append(d)
        return result

    def get_all_sources_for_kb(self, kb_name: str) -> dict[str, list[dict[str, Any]]]:
        """Get ALL sources for entries in a KB in one query."""
        rows = self._exec(
            """
            SELECT s.entry_id, s.id, s.title, s.url, s.outlet, s.date, s.verified
            FROM source s
            WHERE s.kb_name = :kb_name
            """,
            {"kb_name": kb_name},
        )
        result: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            d = dict(row)
            entry_id = d.pop("entry_id")
            d["entry_id"] = entry_id
            d["kb_name"] = kb_name
            result[entry_id].append(d)
        return result

    def get_graph_data(
        self,
        center: str | None = None,
        center_kb: str | None = None,
        kb_name: str | None = None,
        entry_type: str | None = None,
        depth: int = 2,
        limit: int = 500,
        *,
        readable_kbs: set[str] | None,
    ) -> dict[str, Any]:
        """Nodes and edges around ``center``, or across the index without one.

        ``readable_kbs`` (the caller's ``ReadScope`` set; ``None`` unscoped)
        bounds the walk (P-R4, P-R5): an unreadable centre answers like a
        missing one, a link from an unreadable source is not followed, and a
        link to an unreadable target reads as a link to a missing one (a
        bare node, title = id, type "unknown"). ``link_count`` is taken over
        the edges returned, so it counts nothing the caller cannot read.
        """
        depth = max(1, min(3, depth))
        # One binding of the set, shared by the three predicates below.
        scope: dict[str, Any] = {}
        center_clause = kb_names_clause("kb_name", readable_kbs, scope)
        entry_clause = kb_names_clause("e.kb_name", readable_kbs, scope)
        source_clause = kb_names_clause("l.source_kb", readable_kbs, scope)
        nodes: dict[tuple[str, str], dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        edge_set: set[tuple[str, str, str, str]] = set()

        if center and center_kb:
            row = self._exec_one(
                "SELECT id, kb_name, title, entry_type FROM entry "
                "WHERE id = :center AND kb_name = :center_kb"
                + (f" AND {center_clause}" if center_clause else ""),
                {"center": center, "center_kb": center_kb, **scope},
            )
            if not row:
                return {"nodes": [], "edges": []}
            nodes[(row["id"], row["kb_name"])] = row

            frontier = [(center, center_kb)]
            for _hop in range(depth):
                if not frontier or len(nodes) >= limit:
                    break
                next_frontier: list[tuple[str, str]] = []
                for eid, ekb in frontier:
                    if len(nodes) >= limit:
                        break
                    out_rows = self._exec(
                        f"""SELECT l.target_id, l.target_kb, l.relation,
                                  e.title, e.entry_type
                           FROM link l
                           LEFT JOIN entry e ON l.target_id = e.id AND l.target_kb = e.kb_name
                               {f"AND {entry_clause}" if entry_clause else ""}
                           WHERE l.source_id = :eid AND l.source_kb = :ekb
                               {f"AND {source_clause}" if source_clause else ""}""",
                        {"eid": eid, "ekb": ekb, **scope},
                    )
                    for r in out_rows:
                        if len(nodes) >= limit:
                            break
                        tid, tkb = r["target_id"], r["target_kb"]
                        if kb_name and tkb != kb_name:
                            continue
                        if entry_type and r["entry_type"] and r["entry_type"] != entry_type:
                            continue
                        edge_key = (eid, ekb, tid, tkb)
                        if edge_key not in edge_set:
                            edge_set.add(edge_key)
                            edges.append(
                                {
                                    "source_id": eid,
                                    "source_kb": ekb,
                                    "target_id": tid,
                                    "target_kb": tkb,
                                    "relation": r["relation"],
                                }
                            )
                        if (tid, tkb) not in nodes:
                            nodes[(tid, tkb)] = {
                                "id": tid,
                                "kb_name": tkb,
                                "title": r["title"] or tid,
                                "entry_type": r["entry_type"] or "unknown",
                            }
                            next_frontier.append((tid, tkb))

                    in_rows = self._exec(
                        f"""SELECT l.source_id, l.source_kb, l.relation,
                                  e.title, e.entry_type
                           FROM link l
                           JOIN entry e ON l.source_id = e.id AND l.source_kb = e.kb_name
                           WHERE l.target_id = :eid AND l.target_kb = :ekb
                               {f"AND {entry_clause}" if entry_clause else ""}""",
                        {"eid": eid, "ekb": ekb, **scope},
                    )
                    for r in in_rows:
                        if len(nodes) >= limit:
                            break
                        sid, skb = r["source_id"], r["source_kb"]
                        if kb_name and skb != kb_name:
                            continue
                        if entry_type and r["entry_type"] != entry_type:
                            continue
                        edge_key = (sid, skb, eid, ekb)
                        if edge_key not in edge_set:
                            edge_set.add(edge_key)
                            edges.append(
                                {
                                    "source_id": sid,
                                    "source_kb": skb,
                                    "target_id": eid,
                                    "target_kb": ekb,
                                    "relation": r["relation"],
                                }
                            )
                        if (sid, skb) not in nodes:
                            nodes[(sid, skb)] = {
                                "id": sid,
                                "kb_name": skb,
                                "title": r["title"] or sid,
                                "entry_type": r["entry_type"] or "unknown",
                            }
                            next_frontier.append((sid, skb))

                frontier = next_frontier
        else:
            params: dict[str, Any] = {}
            if readable_kbs is None:
                sql = """
                    SELECT e.id, e.kb_name, e.title, e.entry_type
                    FROM entry e
                    WHERE (e.id IN (SELECT source_id FROM link)
                       OR e.id IN (SELECT target_id FROM link))
                """
            else:
                # A node earns its place by a link the caller can see: one it
                # makes itself, or one made to it from a readable source.
                # Otherwise a readable entry linked only from a private KB
                # would appear, and take a slot of `limit`, because of it.
                inbound = kb_names_clause("l.source_kb", readable_kbs, params)
                entry_scope = kb_names_clause("e.kb_name", readable_kbs, params)
                sql = f"""
                    SELECT e.id, e.kb_name, e.title, e.entry_type
                    FROM entry e
                    WHERE (EXISTS (SELECT 1 FROM link l
                                   WHERE l.source_id = e.id AND l.source_kb = e.kb_name)
                       OR EXISTS (SELECT 1 FROM link l
                                  WHERE l.target_id = e.id AND l.target_kb = e.kb_name
                                    AND {inbound}))
                      AND {entry_scope}
                """
            if kb_name:
                sql += " AND e.kb_name = :kb_name"
                params["kb_name"] = kb_name
            if entry_type:
                sql += " AND e.entry_type = :entry_type"
                params["entry_type"] = entry_type
            sql += " LIMIT :limit"
            params["limit"] = limit
            for r in self._exec(sql, params):
                nodes[(r["id"], r["kb_name"])] = r
            if nodes:
                link_rows = self._exec(
                    "SELECT source_id, source_kb, target_id, target_kb, relation FROM link"
                )
                for r in link_rows:
                    src = (r["source_id"], r["source_kb"])
                    tgt = (r["target_id"], r["target_kb"])
                    if src in nodes and tgt in nodes:
                        edges.append(r)

        node_list = []
        for node in nodes.values():
            count = 0
            for e in edges:
                if (e["source_id"] == node["id"] and e["source_kb"] == node["kb_name"]) or (
                    e["target_id"] == node["id"] and e["target_kb"] == node["kb_name"]
                ):
                    count += 1
            node["link_count"] = count
            node_list.append(node)
        return {"nodes": node_list, "edges": edges}

    def get_most_linked(self, kb_name: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        sql = """
            SELECT e.id, e.kb_name, e.title, e.entry_type,
                   COUNT(l.id) as link_count
            FROM entry e
            LEFT JOIN link l ON e.id = l.target_id AND e.kb_name = l.target_kb
        """
        params: dict[str, Any] = {}
        if kb_name:
            sql += " WHERE e.kb_name = :kb_name"
            params["kb_name"] = kb_name
        sql += (
            " GROUP BY e.id, e.kb_name, e.title, e.entry_type ORDER BY link_count DESC LIMIT :limit"
        )
        params["limit"] = limit
        return self._exec(sql, params)

    def get_orphans(
        self, kb_name: str | None = None, *, readable_kbs: set[str] | None
    ) -> list[dict[str, Any]]:
        """Entries with no links in either direction.

        With ``readable_kbs`` (the caller's ``ReadScope`` set) an inbound
        link counts only from a readable source: whether a private entry
        links here is not the caller's to learn (P-R4).
        """
        params: dict[str, Any] = {}
        inbound = kb_names_clause("source_kb", readable_kbs, params)
        sql = f"""
            SELECT e.id, e.kb_name, e.title, e.entry_type
            FROM entry e
            WHERE e.id NOT IN (
                SELECT source_id FROM link WHERE source_kb = e.kb_name
            )
            AND e.id NOT IN (
                SELECT target_id FROM link WHERE target_kb = e.kb_name
                {f"AND {inbound}" if inbound else ""}
            )
        """
        if kb_name:
            sql += " AND e.kb_name = :kb_name"
            params["kb_name"] = kb_name
        return self._exec(sql, params)

    # =====================================================================
    # Tags (raw SQL — shared via _exec)
    # =====================================================================

    def get_all_tags(
        self,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[tuple[str, int]]:
        conditions: list[str] = []
        params: dict[str, Any] = {}
        if kb_name:
            conditions.append("et.kb_name = :kb_name")
            params["kb_name"] = kb_name
        scope = kb_names_clause("et.kb_name", kb_names, params)
        if scope:
            conditions.append(scope)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = self._exec(
            f"""SELECT t.name, COUNT(*) as count
                FROM tag t JOIN entry_tag et ON t.id = et.tag_id
                {where}
                GROUP BY t.name ORDER BY count DESC""",
            params,
        )
        return [(r["name"], r["count"]) for r in rows]

    def get_tags_as_dicts(
        self,
        kb_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
        prefix: str | None = None,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if kb_name:
            conditions.append("et.kb_name = :kb_name")
            params["kb_name"] = kb_name
        scope = kb_names_clause("et.kb_name", kb_names, params)
        if scope:
            conditions.append(scope)
        if prefix:
            conditions.append("t.name LIKE :prefix")
            params["prefix"] = f"{prefix}%"
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"""
            SELECT t.name, COUNT(*) as count
            FROM tag t JOIN entry_tag et ON t.id = et.tag_id
            {where}
            GROUP BY t.name ORDER BY count DESC
            LIMIT :limit OFFSET :offset
        """
        rows = self._exec(sql, params)
        return [{"name": r["name"], "count": r["count"]} for r in rows]

    # =====================================================================
    # Timeline (raw SQL — shared via _exec)
    # =====================================================================

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
        sql = """
            SELECT id, kb_name, title, date, importance, location, summary
            FROM entry
            WHERE date IS NOT NULL AND importance >= :min_importance
        """
        params: dict[str, Any] = {"min_importance": min_importance}
        if kb_name:
            sql += " AND kb_name = :kb_name"
            params["kb_name"] = kb_name
        scope = kb_names_clause("kb_name", kb_names, params)
        if scope:
            sql += f" AND {scope}"
        if date_from:
            sql += " AND date >= :date_from"
            params["date_from"] = date_from
        if date_to:
            sql += " AND date <= :date_to"
            params["date_to"] = date_to
        order = "DESC" if sort_order.lower() == "desc" else "ASC"
        sql += f" ORDER BY date {order} LIMIT :limit OFFSET :offset"
        params["limit"] = limit
        params["offset"] = offset
        return self._exec(sql, params)

    # =====================================================================
    # Object refs (raw SQL — shared via _exec)
    # =====================================================================

    def get_refs_from(self, entry_id: str, kb_name: str) -> list[dict[str, Any]]:
        return self._exec(
            """SELECT r.target_id as id, r.target_kb as kb_name, r.field_name, r.target_type,
                      e.title, e.entry_type
               FROM entry_ref r
               LEFT JOIN entry e ON r.target_id = e.id AND r.target_kb = e.kb_name
               WHERE r.source_id = :entry_id AND r.source_kb = :kb_name""",
            {"entry_id": entry_id, "kb_name": kb_name},
        )

    def get_refs_to(self, entry_id: str, kb_name: str) -> list[dict[str, Any]]:
        return self._exec(
            """SELECT r.source_id as id, r.source_kb as kb_name, r.field_name, r.target_type,
                      e.title, e.entry_type
               FROM entry_ref r
               JOIN entry e ON r.source_id = e.id AND r.source_kb = e.kb_name
               WHERE r.target_id = :entry_id AND r.target_kb = :kb_name""",
            {"entry_id": entry_id, "kb_name": kb_name},
        )

    # =====================================================================
    # Folder queries (raw SQL — shared via _exec)
    # =====================================================================

    def list_entries_in_folder(
        self,
        kb_name: str,
        folder_path: str,
        sort_by: str = "title",
        sort_order: str = "asc",
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        allowed_sorts = {"title", "entry_type", "updated_at", "created_at", "date"}
        if sort_by not in allowed_sorts:
            sort_by = "title"
        if sort_order not in ("asc", "desc"):
            sort_order = "asc"
        folder_prefix = folder_path.rstrip("/") + "/"
        sql = f"""
            SELECT * FROM entry
            WHERE kb_name = :kb_name AND file_path LIKE :folder_prefix
              AND entry_type != 'collection'
            ORDER BY {sort_by} {sort_order}
            LIMIT :limit OFFSET :offset
        """
        return self._exec(
            sql,
            {
                "kb_name": kb_name,
                "folder_prefix": folder_prefix + "%",
                "limit": limit,
                "offset": offset,
            },
        )

    def count_entries_in_folder(self, kb_name: str, folder_path: str) -> int:
        folder_prefix = folder_path.rstrip("/") + "/"
        return (
            self._exec_scalar(
                """SELECT COUNT(*) FROM entry
               WHERE kb_name = :kb_name AND file_path LIKE :folder_prefix
                 AND entry_type != 'collection'""",
                {"kb_name": kb_name, "folder_prefix": folder_prefix + "%"},
            )
            or 0
        )

    # =====================================================================
    # Global counts (raw SQL — shared via _exec)
    # =====================================================================

    def get_global_counts(self, kb_names: set[str] | list[str] | None = None) -> dict[str, int]:
        if kb_names is None:
            tag_count = self._exec_scalar("SELECT COUNT(*) FROM tag") or 0
            link_count = self._exec_scalar("SELECT COUNT(*) FROM link") or 0
        else:
            # Scoped: a tag counts when an entry in a readable KB carries it,
            # and a link counts when both of its ends are in readable KBs --
            # the same rule /api/graph applies to edges.
            params: dict[str, Any] = {}
            tag_scope = kb_names_clause("et.kb_name", kb_names, params)
            tag_count = (
                self._exec_scalar(
                    f"SELECT COUNT(DISTINCT et.tag_id) FROM entry_tag et WHERE {tag_scope}",
                    params,
                )
                or 0
            )
            params = {}
            source_scope = kb_names_clause("source_kb", kb_names, params)
            target_scope = kb_names_clause("target_kb", kb_names, params)
            link_count = (
                self._exec_scalar(
                    f"SELECT COUNT(*) FROM link WHERE {source_scope} AND {target_scope}",
                    params,
                )
                or 0
            )
        return {
            "total_tags": tag_count,
            "total_links": link_count,
        }

    # =====================================================================
    # Edge endpoint queries (raw SQL — shared via _exec)
    # =====================================================================

    def get_edge_endpoints(self, entry_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Get edge endpoints for an edge-type entry (what does this edge connect?)."""
        return self._exec(
            """
            SELECT ep.role, ep.field_name, ep.endpoint_id, ep.endpoint_kb,
                   ep.edge_type, e.title, e.entry_type
            FROM edge_endpoint ep
            LEFT JOIN entry e ON ep.endpoint_id = e.id AND ep.endpoint_kb = e.kb_name
            WHERE ep.edge_entry_id = :entry_id AND ep.edge_entry_kb = :kb_name
            """,
            {"entry_id": entry_id, "kb_name": kb_name},
        )

    def get_edges_by_endpoint(self, endpoint_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Get edge entries where this entity is an endpoint (what edges connect TO this entity?)."""
        return self._exec(
            """
            SELECT ep.edge_entry_id as id, ep.edge_entry_kb as kb_name,
                   ep.role, ep.field_name, ep.edge_type,
                   e.title, e.entry_type
            FROM edge_endpoint ep
            JOIN entry e ON ep.edge_entry_id = e.id AND ep.edge_entry_kb = e.kb_name
            WHERE ep.endpoint_id = :endpoint_id AND ep.endpoint_kb = :kb_name
            """,
            {"endpoint_id": endpoint_id, "kb_name": kb_name},
        )

    def get_edges_between(self, id_a: str, id_b: str, kb_name: str) -> list[dict[str, Any]]:
        """Get edge entries that connect two entities (both are endpoints of the same edge)."""
        return self._exec(
            """
            SELECT DISTINCT e.id, e.kb_name, e.title, e.entry_type,
                   ep1.edge_type, ep1.role as role_a, ep2.role as role_b
            FROM edge_endpoint ep1
            JOIN edge_endpoint ep2 ON ep1.edge_entry_id = ep2.edge_entry_id
                                   AND ep1.edge_entry_kb = ep2.edge_entry_kb
            JOIN entry e ON ep1.edge_entry_id = e.id AND ep1.edge_entry_kb = e.kb_name
            WHERE ep1.endpoint_id = :id_a AND ep2.endpoint_id = :id_b
                  AND ep1.edge_entry_kb = :kb_name
            """,
            {"id_a": id_a, "id_b": id_b, "kb_name": kb_name},
        )
