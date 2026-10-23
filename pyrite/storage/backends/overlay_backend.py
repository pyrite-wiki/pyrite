"""Overlay search backend for per-user diff indexes.

Wraps a main (shared) backend and a diff (per-user) backend.
Read operations merge results with diff-wins-on-ID-collision semantics.
Write operations go only to the diff backend.

Used by the worktree collaboration system (ADR-0024) so users see their
own pending edits overlaid on the shared main index.
"""

from __future__ import annotations

import re
from typing import Any

from .protocol import DEFAULT_MIN_SIMILARITY


class OverlaySearchBackend:
    """Overlays a user's diff index on top of the shared main index.

    Read operations: query both, diff wins on entry ID collision.
    Write operations: only hit the diff backend.
    """

    def __init__(self, main: Any, diff: Any):
        self._main = main
        self._diff = diff

    @property
    def capabilities(self) -> set[Any]:
        """Whatever both halves can do.

        The overlay is only as capable as its weaker half for anything it has
        to combine, so the intersection is the honest answer. ``FILTERED_
        SEMANTIC`` (#56) survives it because ``search_semantic`` delegates
        straight to main with every filter passed through — main's guarantee
        is the overlay's guarantee, and both in-tree backends declare it.
        """
        main = getattr(self._main, "capabilities", set()) or set()
        diff = getattr(self._diff, "capabilities", set()) or set()
        return set(main) & set(diff)

    def close(self) -> None:
        # Don't close main — it's shared. Only close diff.
        self._diff.close()

    # ── write operations → diff only ────────────────────────────────

    def upsert_entry(self, entry_data: dict[str, Any]) -> None:
        self._diff.upsert_entry(entry_data)

    def delete_entry(self, entry_id: str, kb_name: str) -> bool:
        # Delete from diff if present there
        result = self._diff.delete_entry(entry_id, kb_name)
        # If the entry exists in main, we'd need a tombstone for full correctness.
        # For V1, deleting from diff is sufficient — the entry reappears from main
        # on read, which is acceptable (admin merge handles actual deletion).
        return result

    # ── entry CRUD reads → merge ────────────────────────────────────

    def get_entry(self, entry_id: str, kb_name: str) -> dict[str, Any] | None:
        # Diff wins if present
        diff_entry = self._diff.get_entry(entry_id, kb_name)
        if diff_entry is not None:
            return diff_entry
        return self._main.get_entry(entry_id, kb_name)

    def get_entries(self, ids: list[tuple[str, str]]) -> list[dict[str, Any]]:
        main_entries = self._main.get_entries(ids)
        diff_entries = self._diff.get_entries(ids)
        # Build lookup by (id, kb_name) — diff wins
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for e in main_entries:
            merged[(e["id"], e["kb_name"])] = e
        for e in diff_entries:
            merged[(e["id"], e["kb_name"])] = e
        return list(merged.values())

    def list_entries(
        self,
        kb_name: str | None = None,
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
        # Get all from main (without limit — we need to merge before paginating)
        main_results = self._main.list_entries(
            kb_name=kb_name,
            entry_type=entry_type,
            tag=tag,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=10000,  # fetch all for merge
            offset=0,
            include_archived=include_archived,
            status=status,
            min_importance=min_importance,
        )
        diff_results = self._diff.list_entries(
            kb_name=kb_name,
            entry_type=entry_type,
            tag=tag,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=10000,
            offset=0,
            include_archived=include_archived,
            status=status,
            min_importance=min_importance,
        )
        merged = self._merge_entry_lists(main_results, diff_results, sort_by, sort_order)
        return merged[offset : offset + limit] if limit else merged

    def count_entries(
        self,
        kb_name: str | None = None,
        entry_type: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        min_importance: int | None = None,
    ) -> int:
        main_count = self._main.count_entries(
            kb_name=kb_name,
            entry_type=entry_type,
            tag=tag,
            status=status,
            min_importance=min_importance,
        )
        diff_entries = self._diff.list_entries(
            kb_name=kb_name,
            entry_type=entry_type,
            tag=tag,
            limit=10000,
            status=status,
            min_importance=min_importance,
        )
        # New entries in diff (not in main) add to count
        # Modified entries in diff don't change count
        main_ids = set()
        if diff_entries:
            main_all = self._main.list_entries(
                kb_name=kb_name, entry_type=entry_type, tag=tag, limit=10000
            )
            main_ids = {(e["id"], e["kb_name"]) for e in main_all}
        new_in_diff = sum(1 for e in diff_entries if (e["id"], e["kb_name"]) not in main_ids)
        return main_count + new_in_diff

    def get_distinct_types(
        self,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[str]:
        main_types = set(self._main.get_distinct_types(kb_name, kb_names))
        diff_types = set(self._diff.get_distinct_types(kb_name, kb_names))
        return sorted(main_types | diff_types)

    def get_entries_for_indexing(self, kb_name: str) -> list[dict[str, Any]]:
        return self._main.get_entries_for_indexing(kb_name)

    # ── search → merge ──────────────────────────────────────────────

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
        non_empty: bool = False,
    ) -> list[dict[str, Any]]:
        main_results = self._main.search(
            query,
            kb_name=kb_name,
            entry_type=entry_type,
            tags=tags,
            date_from=date_from,
            date_to=date_to,
            limit=10000,  # fetch all for merge
            offset=0,
            include_archived=include_archived,
            lifecycle=lifecycle,
            fips=fips,
            state=state,
            status=status,
            non_empty=non_empty,
        )
        diff_results = self._diff.search(
            query,
            kb_name=kb_name,
            entry_type=entry_type,
            tags=tags,
            date_from=date_from,
            date_to=date_to,
            limit=10000,
            offset=0,
            include_archived=include_archived,
            lifecycle=lifecycle,
            fips=fips,
            state=state,
            status=status,
            non_empty=non_empty,
        )
        merged = self._merge_entry_lists(main_results, diff_results)
        return merged[offset : offset + limit] if limit else merged

    def search_by_tag(
        self, tag: str, kb_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        main = self._main.search_by_tag(tag, kb_name, limit=10000)
        diff = self._diff.search_by_tag(tag, kb_name, limit=10000)
        return self._merge_entry_lists(main, diff)[:limit]

    def search_by_date_range(
        self,
        date_from: str,
        date_to: str,
        kb_name: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        main = self._main.search_by_date_range(date_from, date_to, kb_name, limit=10000)
        diff = self._diff.search_by_date_range(date_from, date_to, kb_name, limit=10000)
        return self._merge_entry_lists(main, diff)[:limit]

    def search_by_tag_prefix(
        self, prefix: str, kb_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        main = self._main.search_by_tag_prefix(prefix, kb_name, limit=10000)
        diff = self._diff.search_by_tag_prefix(prefix, kb_name, limit=10000)
        return self._merge_entry_lists(main, diff)[:limit]

    # ── edge endpoints → delegate to main ────────────────────────────

    def get_edge_endpoints(self, entry_id: str, kb_name: str) -> list[dict[str, Any]]:
        return self._main.get_edge_endpoints(entry_id, kb_name)

    def get_edges_by_endpoint(self, endpoint_id: str, kb_name: str) -> list[dict[str, Any]]:
        return self._main.get_edges_by_endpoint(endpoint_id, kb_name)

    def get_edges_between(self, id_a: str, id_b: str, kb_name: str) -> list[dict[str, Any]]:
        return self._main.get_edges_between(id_a, id_b, kb_name)

    # ── graph → merge ───────────────────────────────────────────────

    def get_backlinks(
        self,
        entry_id: str,
        kb_name: str,
        limit: int = 0,
        offset: int = 0,
        *,
        readable_kbs: set[str] | None,
    ) -> list[dict[str, Any]]:
        main = self._main.get_backlinks(entry_id, kb_name, limit=10000, readable_kbs=readable_kbs)
        diff = self._diff.get_backlinks(entry_id, kb_name, limit=10000, readable_kbs=readable_kbs)
        # The inverse relation may be shared by distinct custom relations, and
        # one source may write the same target twice through two prefixes:
        # rows are per written link (#1009), so the prefix is part of the
        # identity this merge may collapse on (#1062).
        merged = self._merge_entry_lists(
            main, diff, key_fields=("id", "kb_name", "forward_relation", "prefix")
        )
        if limit:
            return merged[offset : offset + limit]
        return merged

    def get_outlinks(
        self, entry_id: str, kb_name: str, *, readable_kbs: set[str] | None
    ) -> list[dict[str, Any]]:
        main = self._main.get_outlinks(entry_id, kb_name, readable_kbs=readable_kbs)
        diff = self._diff.get_outlinks(entry_id, kb_name, readable_kbs=readable_kbs)
        return self._merge_entry_lists(
            main, diff, key_fields=("id", "kb_name", "relation", "prefix")
        )

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
        # For V1, graph comes from main only — diff entries are few
        # and merging graph BFS is complex. User's new entries won't
        # appear in graph until merged.
        return self._main.get_graph_data(
            center=center,
            center_kb=center_kb,
            kb_name=kb_name,
            entry_type=entry_type,
            depth=depth,
            limit=limit,
            readable_kbs=readable_kbs,
        )

    def get_most_linked(self, kb_name: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return self._main.get_most_linked(kb_name, limit)

    def get_orphans(
        self, kb_name: str | None = None, *, readable_kbs: set[str] | None
    ) -> list[dict[str, Any]]:
        return self._main.get_orphans(kb_name, readable_kbs=readable_kbs)

    # ── tags → merge ────────────────────────────────────────────────

    def get_all_tags(
        self,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[tuple[str, int]]:
        main_tags = dict(self._main.get_all_tags(kb_name, kb_names=kb_names))
        diff_tags = dict(self._diff.get_all_tags(kb_name, kb_names=kb_names))
        merged = dict(main_tags)
        for tag, count in diff_tags.items():
            merged[tag] = merged.get(tag, 0) + count
        return sorted(merged.items(), key=lambda x: -x[1])

    def get_tags_as_dicts(
        self,
        kb_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
        prefix: str | None = None,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        # Delegate to main for V1 — tag counts from diff are minimal
        return self._main.get_tags_as_dicts(
            kb_name=kb_name, limit=limit, offset=offset, prefix=prefix, kb_names=kb_names
        )

    # ── timeline → delegate to main ─────────────────────────────────

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
        return self._main.get_timeline(
            date_from=date_from,
            date_to=date_to,
            min_importance=min_importance,
            kb_name=kb_name,
            limit=limit,
            offset=offset,
            sort_order=sort_order,
            kb_names=kb_names,
        )

    # ── embeddings → delegate to main ───────────────────────────────

    def upsert_embedding(self, entry_id: str, kb_name: str, embedding: list[float]) -> bool:
        return self._diff.upsert_embedding(entry_id, kb_name, embedding)

    def search_semantic(
        self,
        embedding: list[float],
        kb_name: str | None = None,
        limit: int = 20,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
        entry_type: str | None = None,
        tags: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        fips: str | None = None,
        state: str | None = None,
        status: str | None = None,
        include_archived: bool = False,
        kb_names: set[str] | list[str] | None = None,
        non_empty: bool = False,
    ) -> list[dict[str, Any]]:
        # For V1, semantic search from main only. Filters pass straight through
        # so the overlay honours them exactly as main does (#56).
        return self._main.search_semantic(
            embedding,
            kb_name,
            limit,
            min_similarity,
            kb_names=kb_names,
            entry_type=entry_type,
            tags=tags,
            date_from=date_from,
            date_to=date_to,
            fips=fips,
            state=state,
            status=status,
            include_archived=include_archived,
            non_empty=non_empty,
        )

    def has_embeddings(
        self, kb_name: str | None = None, kb_names: set[str] | list[str] | None = None
    ) -> bool:
        return self._main.has_embeddings(kb_name, kb_names)

    def embedding_stats(self) -> dict[str, Any]:
        return self._main.embedding_stats()

    def get_embedded_rowids(self) -> set[int]:
        return self._main.get_embedded_rowids()

    def get_entries_for_embedding(self, kb_name: str | None = None) -> list[dict[str, Any]]:
        return self._main.get_entries_for_embedding(kb_name)

    def delete_embedding(self, entry_id: str, kb_name: str) -> None:
        self._diff.delete_embedding(entry_id, kb_name)

    # ── object refs → delegate to main ──────────────────────────────

    def get_refs_from(self, entry_id: str, kb_name: str) -> list[dict[str, Any]]:
        return self._main.get_refs_from(entry_id, kb_name)

    def get_refs_to(self, entry_id: str, kb_name: str) -> list[dict[str, Any]]:
        return self._main.get_refs_to(entry_id, kb_name)

    # ── folder queries → delegate to main ───────────────────────────

    def list_entries_in_folder(
        self,
        kb_name: str,
        folder_path: str,
        sort_by: str = "title",
        sort_order: str = "asc",
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self._main.list_entries_in_folder(
            kb_name, folder_path, sort_by, sort_order, limit, offset
        )

    def count_entries_in_folder(self, kb_name: str, folder_path: str) -> int:
        return self._main.count_entries_in_folder(kb_name, folder_path)

    # ── global counts → delegate to main ────────────────────────────

    def get_global_counts(self, kb_names: set[str] | list[str] | None = None) -> dict[str, int]:
        return self._main.get_global_counts(kb_names=kb_names)

    # ── internal merge helper ───────────────────────────────────────

    @staticmethod
    def _merge_entry_lists(
        main: list[dict[str, Any]],
        diff: list[dict[str, Any]],
        sort_by: str | None = None,
        sort_order: str = "desc",
        *,
        key_fields: tuple[str, ...] = ("id", "kb_name"),
    ) -> list[dict[str, Any]]:
        """Merge rows with diff winning on identity collisions.

        Preserves order from main, replacing entries where diff has a
        newer version, and appending new-in-diff entries at the end.
        Entry/search callers use entry identity; link callers include the
        stored relation. A missing diff link does not hide a main link.
        """
        if not diff:
            return main

        diff_by_id: dict[tuple[str, ...], dict[str, Any]] = {}
        for e in diff:
            diff_by_id[tuple(e.get(field, "") for field in key_fields)] = e

        # Replace main entries where diff has an override
        merged = []
        seen_ids: set[tuple[str, ...]] = set()
        for e in main:
            key = tuple(e.get(field, "") for field in key_fields)
            seen_ids.add(key)
            if key in diff_by_id:
                merged.append(diff_by_id[key])
            else:
                merged.append(e)

        # Append entries only in diff (new entries)
        for key, e in diff_by_id.items():
            if key not in seen_ids:
                merged.append(e)

        # Re-sort if requested
        if sort_by:
            reverse = sort_order == "desc"
            merged.sort(key=lambda e: e.get(sort_by, ""), reverse=reverse)

        return merged


class WorktreeDB:
    """Proxy that routes search operations through an OverlaySearchBackend
    while forwarding app-state operations to the main DB.

    Behaves like a PyriteDB for KBService and other consumers.
    """

    #: Writes through this handle land in a user's change set, not on main:
    #: code that writes the shared index directly (a claim's compare-and-swap)
    #: checks this instead of writing main (ADR-0044 §1).
    is_change_set = True

    def __init__(self, main_db: Any, diff_db: Any):
        self._main = main_db
        self._diff = diff_db
        self._overlay = OverlaySearchBackend(main_db._backend, diff_db._backend)

    @property
    def _backend(self):
        return self._overlay

    @property
    def backend(self):
        return self._overlay

    @property
    def vec_available(self):
        return self._main.vec_available

    @property
    def session(self):
        return self._main.session

    @property
    def _raw_conn(self):
        return self._main._raw_conn

    # Search/entry methods go through overlay
    def get_entry(self, entry_id: str, kb_name: str) -> dict[str, Any] | None:
        return self._overlay.get_entry(entry_id, kb_name)

    def get_entries(self, ids: list[tuple[str, str]]) -> list[dict[str, Any]]:
        return self._overlay.get_entries(ids)

    def search(self, query: str, **kwargs) -> list[dict[str, Any]]:
        return self._overlay.search(query, **kwargs)

    def list_entries(self, **kwargs) -> list[dict[str, Any]]:
        return self._overlay.list_entries(**kwargs)

    def count_entries(self, **kwargs) -> int:
        return self._overlay.count_entries(**kwargs)

    def get_backlinks(
        self,
        entry_id: str,
        kb_name: str,
        limit: int = 0,
        offset: int = 0,
        *,
        readable_kbs: set[str] | None,
    ) -> list[dict[str, Any]]:
        return self._overlay.get_backlinks(
            entry_id, kb_name, limit=limit, offset=offset, readable_kbs=readable_kbs
        )

    def get_outlinks(
        self, entry_id: str, kb_name: str, *, readable_kbs: set[str] | None
    ) -> list[dict[str, Any]]:
        return self._overlay.get_outlinks(entry_id, kb_name, readable_kbs=readable_kbs)

    def get_all_tags(self, kb_name: str | None = None) -> list[tuple[str, int]]:
        return self._overlay.get_all_tags(kb_name)

    def upsert_entry(self, entry_data: dict[str, Any]) -> None:
        return self._overlay.upsert_entry(entry_data)

    def delete_entry(self, entry_id: str, kb_name: str) -> bool:
        return self._overlay.delete_entry(entry_id, kb_name)

    def close(self):
        # Only close diff — main is shared
        self._diff.close()

    # Everything else: classified, never forwarded by default (ADR-0044 §1).
    #
    # A write through this handle lands in the change set. Before 2026-10-20
    # every method this class does not define went to main, so a change-set
    # write that touched another table (``register_kb`` from every create and
    # update) changed main for every reader. Now a name is forwarded only when
    # it is listed: reads go to main, KB-scoped writes go to the diff index,
    # instance-level writes are refused, and an unlisted name is an error, so a
    # new PyriteDB method is classified before it can be used through here
    # (tests/test_landing_rule.py walks PyriteDB and checks every name is).
    def __getattr__(self, name: str) -> Any:
        if name in _READS_FROM_MAIN or name in _RAW_HANDLES:
            return getattr(self._main, name)
        if name in _READ_SQL_ONLY:
            return self._read_only_sql(getattr(self._main, name), name)
        if name in _WRITES_TO_DIFF:
            return getattr(self._diff, name)
        if name in _RECORDS_REFUSED:
            return _refuse(name, _RECORDS_REFUSED[name])
        if name in _WRITES_REFUSED:
            return _refuse(name, "an instance-level write, which has no place in a change set")
        raise AttributeError(
            f"WorktreeDB does not know whether {name!r} writes: classify it in "
            "overlay_backend.py before a change-set write uses it"
        )

    @staticmethod
    def _read_only_sql(method: Any, name: str) -> Any:
        """`execute_sql` runs whatever it is given, on main: here it runs a
        read and refuses anything else."""

        def guarded(sql: str, *args: Any, **kwargs: Any) -> Any:
            if not _READ_SQL.match(sql):
                return _refuse(name, "a SQL write, which a change set cannot carry")()
            return method(sql, *args, **kwargs)

        return guarded


_READ_SQL = re.compile(r"^\s*(SELECT|WITH|PRAGMA|EXPLAIN)\b", re.IGNORECASE)


def _refuse(name: str, why: str):
    def refused(*args: Any, **kwargs: Any) -> Any:
        from pyrite.exceptions import ChangeSetRecordRefusedError

        raise ChangeSetRecordRefusedError(
            f"{name} is {why}. It is available to the operator, or after the change set merges."
        )

    return refused


#: Read-only: answered by the main index (the overlay methods above answer
#: the entry reads that must see the change set). Each is scanned for writes
#: by tests/test_landing_rule.py.
_READS_FROM_MAIN = frozenset(
    {
        "_SORT_COLUMNS", "_VALID_FK_REF", "_VALID_SQL_DEFAULT", "_VALID_SQL_IDENTIFIER",
        "_VALID_SQL_TYPE", "_repo_to_dict", "_review_to_dict", "_user_to_dict",
        "_validate_identifier", "_version_to_dict", "count_entries_in_folder",
        "entry_version_exists", "find_by_assignee", "find_by_location",
        "find_by_status", "find_overdue", "get_all_backlinks_for_kb", "get_all_outlinks_for_kb",
        "get_all_settings", "get_all_sources_for_kb", "get_contributors", "get_distinct_types",
        "get_edge_endpoints", "get_edges_between", "get_edges_by_endpoint",
        "get_entries_for_indexing", "get_entry_version_file_path", "get_entry_versions",
        "get_global_counts", "get_graph_data", "get_kb_stats", "get_kbs_for_repo",
        "get_latest_review", "get_migration_status", "get_most_linked",
        "get_orphans", "get_refs_from", "get_refs_to", "get_related", "get_repo", "get_review",
        "get_reviews", "get_schema_version", "get_setting", "get_tag_tree", "get_tags_as_dicts",
        "get_timeline", "get_type_counts", "get_user", "get_workspace_repos",
        "list_entries_in_folder", "list_entry_version_rows", "list_repos", "search_by_date_range",
        "search_by_tag", "search_by_tag_prefix",
    }
)  # fmt: skip

#: Runs the SQL it is given: forwarded to main for a read, refused otherwise.
_READ_SQL_ONLY = frozenset({"execute_sql"})

#: Handles onto main's connection and session: attributes, not writes. Reads
#: go through them, and so do two known writers: a claim's compare-and-swap
#: (which checks `is_change_set` and stays off main) and plugin side tables
#: (social votes, encyclopedia reviews, software-kb gates), which still reach
#: main -- a tracked follow-up, not covered by this classification.
_RAW_HANDLES = frozenset(
    {"db_path", "engine", "vec_load_error", "_fallback_lock", "_fallback_sessions", "_local",
     "_raw_lock", "_sa_conn", "_session_factory", "conn", "_raw_cursor", "request_handle",
     "session_scope", "_load_extensions", "_version_at_open"}
)  # fmt: skip

#: Writes a change-set write makes whose rows are rebuilt from files and git,
#: so nothing is lost when the diff index is discarded at merge:
#: - `register_kb`, `update_kb_indexed`: the diff index's own row for the KB
#:   (path = the working copy); main's row is not the change set's to change.
#: - `upsert_entry_version`, `delete_entry_version`: entry history indexed
#:   from git commits, rebuilt from the merged history.
_WRITES_TO_DIFF = frozenset(
    {"register_kb", "update_kb_indexed", "upsert_entry_version", "delete_entry_version"}
)  # fmt: skip

#: Database-only records: no file behind them, so the merge, which discards the
#: diff index, would lose them, and main is not the change set's to write.
#: Refused from a change-set target (decided 2026-10-20); carrying them
#: through a merge is a follow-up.
_RECORDS_REFUSED = {
    "create_review": "a database-only review record, which a change set cannot carry",
    "delete_review": "a database-only review record, which a change set cannot carry",
    "execute_write_sql": "a database-only SQL write, which a change set cannot carry",
}

#: Instance-level writes (settings, users, repositories, the KB registry's
#: shape, schema and policy): no change-set write has a reason to make one.
_WRITES_REFUSED = frozenset(
    {"set_setting", "delete_setting", "upsert_user", "get_local_user", "insert_new_kb",
     "unregister_kb", "update_kb_default_role", "merge_registered_kbs", "register_repo",
     "add_workspace_repo", "delete_repo", "remove_workspace_repo", "link_kb_to_repo",
     "set_repo_upstream", "update_repo_synced", "update_workspace_role", "transaction",
     "_init_connection", "_run_migrations", "_create_plugin_tables",
     "_create_table_from_def", "_record_private_policy", "_prepare_schema",
     "_repair_schema", "_dispose_after_failed_open"}
)  # fmt: skip
