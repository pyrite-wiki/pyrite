"""
SearchBackend protocol — the contract any index backend must satisfy.

Knowledge-index tables (entry, entry_fts, vec_entry, tag, entry_tag,
link, entry_ref, source, block) are managed exclusively through this
protocol.  App-state tables (kb, user, repo, etc.) stay in PyriteDB/ORM.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SearchBackend(Protocol):
    """Protocol for pluggable search/index backends."""

    # ── lifecycle ────────────────────────────────────────────────────

    def close(self) -> None:
        """Release backend resources."""
        ...

    # ── entry CRUD ───────────────────────────────────────────────────

    def upsert_entry(self, entry_data: dict[str, Any]) -> None:
        """Insert or update an entry with all related data (tags, links, etc.)."""
        ...

    def delete_entry(self, entry_id: str, kb_name: str) -> bool:
        """Delete an entry and all related data. Returns True if deleted."""
        ...

    def get_entry(self, entry_id: str, kb_name: str) -> dict[str, Any] | None:
        """Get a single entry with tags, sources, and links."""
        ...

    def get_entries(self, ids: list[tuple[str, str]]) -> list[dict[str, Any]]:
        """Batch-get multiple entries by (entry_id, kb_name) pairs."""
        ...

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
        """List entries with pagination and optional filters."""
        ...

    def count_entries(
        self,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
        entry_type: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        min_importance: int | None = None,
    ) -> int:
        """Count entries matching filters."""
        ...

    def get_distinct_types(
        self,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[str]:
        """Get distinct entry types."""
        ...

    def get_entries_for_indexing(self, kb_name: str) -> list[dict[str, Any]]:
        """Get entry id/file_path/indexed_at for incremental sync."""
        ...

    # ── full-text search ─────────────────────────────────────────────

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
    ) -> list[dict[str, Any]]:
        """Full-text search across entries."""
        ...

    def search_by_tag(
        self, tag: str, kb_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Search entries by exact tag."""
        ...

    def search_by_date_range(
        self,
        date_from: str,
        date_to: str,
        kb_name: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Search entries within a date range."""
        ...

    def search_by_tag_prefix(
        self, prefix: str, kb_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Search entries by tag prefix (parent includes children)."""
        ...

    # ── semantic search (embeddings) ─────────────────────────────────

    def upsert_embedding(self, entry_id: str, kb_name: str, embedding: list[float]) -> bool:
        """Store/update a vector embedding for an entry. Returns True on success."""
        ...

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
    ) -> list[dict[str, Any]]:
        """KNN search over stored embeddings, honouring the same filters as
        :meth:`search`.

        The filter set here is deliberately the keyword leg's filter set. A
        hybrid search fuses the two legs, so a filter applied on only one of
        them produces a result set that silently violates the caller's filter
        (#56). An implementation that applies every filter it is given declares
        :attr:`~.capabilities.BackendCapability.FILTERED_SEMANTIC`; one that
        does not must leave it undeclared, and ``SearchService`` drops the
        vector leg — naming the filters in ``warnings`` — rather than call it
        with a filter it would ignore. Returning unfiltered rows is never
        acceptable.
        """
        ...

    def has_embeddings(self) -> bool:
        """Check if any embeddings exist."""
        ...

    def embedding_stats(self) -> dict[str, Any]:
        """Get embedding coverage statistics."""
        ...

    def get_embedded_rowids(self) -> set[int]:
        """Get set of rowids that already have embeddings."""
        ...

    def get_entries_for_embedding(self, kb_name: str | None = None) -> list[dict[str, Any]]:
        """Get entries with rowid for batch embedding."""
        ...

    def delete_embedding(self, entry_id: str, kb_name: str) -> None:
        """Delete embedding for an entry."""
        ...

    # ── edge endpoints ──────────────────────────────────────────────

    def get_edge_endpoints(self, entry_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Get edge endpoints for an edge-type entry (what does this edge connect?)."""
        ...

    def get_edges_by_endpoint(self, endpoint_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Get edge entries where this entity is an endpoint."""
        ...

    def get_edges_between(self, id_a: str, id_b: str, kb_name: str) -> list[dict[str, Any]]:
        """Get edge entries that connect two entities."""
        ...

    # ── graph (links) ────────────────────────────────────────────────

    def get_backlinks(
        self,
        entry_id: str,
        kb_name: str,
        limit: int = 0,
        offset: int = 0,
        *,
        readable_kbs: set[str] | None,
    ) -> list[dict[str, Any]]:
        """Get entries that link TO this entry; sources outside ``readable_kbs`` dropped."""
        ...

    def get_outlinks(
        self, entry_id: str, kb_name: str, *, readable_kbs: set[str] | None
    ) -> list[dict[str, Any]]:
        """Get entries this entry links TO; a target outside ``readable_kbs`` reads as missing."""
        ...

    def get_all_backlinks_for_kb(self, kb_name: str) -> dict[str, list[dict[str, Any]]]:
        """Get ALL backlinks targeting entries in a KB, keyed by target entry_id.

        Returns the same per-entry data as get_backlinks() but in a single query.
        """
        ...

    def get_all_outlinks_for_kb(self, kb_name: str) -> dict[str, list[dict[str, Any]]]:
        """Get ALL outlinks from entries in a KB, keyed by source entry_id.

        Returns the same per-entry data as get_outlinks() but in a single query.
        """
        ...

    def get_all_sources_for_kb(self, kb_name: str) -> dict[str, list[dict[str, Any]]]:
        """Get ALL sources for entries in a KB, keyed by entry_id.

        Returns the same per-entry data as _get_entry_sources() but in a single query.
        """
        ...

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
        """Multi-hop BFS graph traversal returning {nodes, edges}, within ``readable_kbs``."""
        ...

    def get_most_linked(self, kb_name: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Get entries with most incoming links."""
        ...

    def get_orphans(
        self, kb_name: str | None = None, *, readable_kbs: set[str] | None
    ) -> list[dict[str, Any]]:
        """Get entries with no links (neither direction); inbound from ``readable_kbs`` only."""
        ...

    # ── tags ─────────────────────────────────────────────────────────

    def get_all_tags(
        self,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[tuple[str, int]]:
        """Get all tags with counts.

        ``kb_names`` restricts the result to a set of KBs -- the caller's
        readable set. ``None`` means unrestricted; an empty set means the
        caller may read nothing and must match no rows.
        """
        ...

    def get_tags_as_dicts(
        self,
        kb_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
        prefix: str | None = None,
        kb_names: set[str] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get tags with counts as dicts."""
        ...

    # ── timeline ─────────────────────────────────────────────────────

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
        ...

    # ── object refs ──────────────────────────────────────────────────

    def get_refs_from(self, entry_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Get entries this entry references via object-ref fields."""
        ...

    def get_refs_to(self, entry_id: str, kb_name: str) -> list[dict[str, Any]]:
        """Get entries that reference this entry."""
        ...

    # ── folder queries ───────────────────────────────────────────────

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
        ...

    def count_entries_in_folder(self, kb_name: str, folder_path: str) -> int:
        """Count entries in a folder."""
        ...

    # ── global counts ────────────────────────────────────────────────

    def get_global_counts(self, kb_names: set[str] | list[str] | None = None) -> dict[str, int]:
        """Get tag and link counts, restricted to ``kb_names`` when given."""
        ...
