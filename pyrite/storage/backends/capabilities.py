"""Backend capability declarations (Tier A r1400; locked design commit 3777cb5).

Note: an earlier draft of this docstring cited "ADR-0028", but that number was
later assigned to the backend-agnostic query DSL. The r1400 capability design
was never written up as a numbered ADR; the locked decision lives in commit
3777cb5.

Backend classes declare which backend-protocol subsystems they support
via a ``capabilities: ClassVar[set[BackendCapability]]`` class
attribute. A dispatch helper (``backend_declares``) consults this set
before calling each method, skipping methods whose capability the
backend did not claim.

Locked design (commit 3777cb5, Option B). ENTITY, SEARCH and EMBEDDING
mirror the 3-subsystem split documented in the r1400 ticket — an
eventual move to Option A (splitting into ``EntityStore``,
``SearchEngine``, ``EmbeddingStore`` protocols) is mechanical: each
Capability becomes its own Protocol with the same name.

``FILTERED_SEMANTIC`` (#56) is the first member that is not a subsystem
but a refinement of one: it says *how well* a backend implements
``search_semantic``, not whether it implements it. Callers that need a
guarantee rather than a method check it directly rather than going
through ``backend_declares``, which answers only the dispatch question
("may I call this method at all"). Under a future Option A split it
would be a flag on ``EmbeddingStore``, not a protocol of its own.

Decision #2 from the locked design: this module handles ONLY the
class-attribute question ("can in principle do X"). The runtime
``is_available()`` check ("is the dependency installed right now")
stays where it already lives in each backend — they're two separate
gates.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class BackendCapability(StrEnum):
    """The backend-protocol subsystems a backend can opt into.

    ENTITY     — entity-table CRUD, listing, counting, edges, graph,
                 tags, timeline, object refs, folder queries, global
                 counts. Anything that's "entity-table reads/writes/
                 topology" without crossing into search or vectors.
    SEARCH     — full-text search (keyword + tag + date + tag-prefix).
                 Backend-specific FTS implementations (FTS5 vs tsvector)
                 vary at runtime but all live behind this capability.
    EMBEDDING  — vector storage and semantic KNN search. SQLite via
                 sqlite-vec (runtime-gated), Postgres via pgvector.

    FILTERED_SEMANTIC — ``search_semantic`` honours the same filter set
                 as ``search`` (entry_type, tags, date range, fips,
                 state, status, include_archived) *inside* the KNN query
                 rather than after it. A refinement of EMBEDDING, not a
                 fourth subsystem: a backend declaring it must declare
                 EMBEDDING too.

                 It exists because hybrid mode fuses the keyword and
                 vector legs, so a filter honoured on only one of them
                 yields a result set that silently violates the caller's
                 filter (#56). ``SearchService`` checks this up front and
                 drops the vector leg — with a named warning — when a
                 backend does not declare it. The alternative, probing by
                 calling and catching ``TypeError``, cannot tell a
                 backend that lacks the feature from a genuine bug
                 anywhere inside the vector leg, and relabelled the
                 second as the first.
    """

    ENTITY = "entity"
    SEARCH = "search"
    EMBEDDING = "embedding"
    FILTERED_SEMANTIC = "filtered_semantic"


# =============================================================================
# Method-to-capability map (locked design decision #3: module-level constant)
# =============================================================================
# Maps each dispatched method on the SearchBackend Protocol to the
# Capability that authorizes it. The ``close`` lifecycle method is NOT
# in this map — methods missing from this dict are treated as
# always-allowed (see ``backend_declares``) so infrastructure calls
# don't silently disappear.
#
# When adding a new method to the Backend Protocol, add an entry here
# at the same time. ``test_every_value_is_a_capability`` keeps the
# headcount honest; a future PR that adds a method without mapping it
# will silently skip that method's dispatch — the test guards against
# the silent-skip variant.
# =============================================================================

_METHOD_CAPABILITIES: dict[str, BackendCapability] = {
    # ── ENTITY — CRUD ────────────────────────────────────────────────
    "upsert_entry": BackendCapability.ENTITY,
    "delete_entry": BackendCapability.ENTITY,
    "get_entry": BackendCapability.ENTITY,
    "get_entries": BackendCapability.ENTITY,
    "list_entries": BackendCapability.ENTITY,
    "count_entries": BackendCapability.ENTITY,
    "get_distinct_types": BackendCapability.ENTITY,
    "get_entries_for_indexing": BackendCapability.ENTITY,
    # ── ENTITY — edge endpoints ─────────────────────────────────────
    "get_edge_endpoints": BackendCapability.ENTITY,
    "get_edges_by_endpoint": BackendCapability.ENTITY,
    "get_edges_between": BackendCapability.ENTITY,
    # ── ENTITY — graph (links) ──────────────────────────────────────
    "get_backlinks": BackendCapability.ENTITY,
    "get_outlinks": BackendCapability.ENTITY,
    "get_kbs_holding": BackendCapability.ENTITY,
    "get_all_backlinks_for_kb": BackendCapability.ENTITY,
    "get_all_outlinks_for_kb": BackendCapability.ENTITY,
    "get_all_sources_for_kb": BackendCapability.ENTITY,
    "get_graph_data": BackendCapability.ENTITY,
    "get_most_linked": BackendCapability.ENTITY,
    "get_orphans": BackendCapability.ENTITY,
    # ── ENTITY — tags / timeline / object refs / folder / counts ────
    "get_all_tags": BackendCapability.ENTITY,
    "get_tags_as_dicts": BackendCapability.ENTITY,
    "get_timeline": BackendCapability.ENTITY,
    "get_refs_from": BackendCapability.ENTITY,
    "get_refs_to": BackendCapability.ENTITY,
    "list_entries_in_folder": BackendCapability.ENTITY,
    "count_entries_in_folder": BackendCapability.ENTITY,
    "get_global_counts": BackendCapability.ENTITY,
    # ── SEARCH — full-text + filtered list flavors ──────────────────
    "search": BackendCapability.SEARCH,
    "search_by_tag": BackendCapability.SEARCH,
    "search_by_date_range": BackendCapability.SEARCH,
    "search_by_tag_prefix": BackendCapability.SEARCH,
    # ── EMBEDDING — vector storage + semantic KNN ───────────────────
    "upsert_embedding": BackendCapability.EMBEDDING,
    "search_semantic": BackendCapability.EMBEDDING,
    "has_embeddings": BackendCapability.EMBEDDING,
    "embedding_stats": BackendCapability.EMBEDDING,
    "get_embedded_rowids": BackendCapability.EMBEDDING,
    "get_entries_for_embedding": BackendCapability.EMBEDDING,
    "delete_embedding": BackendCapability.EMBEDDING,
}


def backend_declares(backend: Any, method_name: str) -> bool:
    """Return True if ``backend``'s declared capability set covers
    ``method_name``'s required capability.

    Methods not in ``_METHOD_CAPABILITIES`` (e.g. ``close`` lifecycle,
    or any future ungated helper) are treated as always-allowed —
    they're not part of the dispatch-skip surface this optimization
    targets, and silently skipping infrastructure calls would be far
    worse than a no-op.

    A backend instance without a ``capabilities`` attribute defaults
    to the empty set — every capability-gated method is skipped. Safe
    failure mode (Tier A r1400 / Option B): a backend that forgets to
    declare gets ignored entirely rather than silently half-loaded.
    Mitigated by the same-commit migration of in-tree backends
    (SQLiteBackend + PostgresBackend declare all three) in r1400 fires
    3 and 4.
    """
    cap = _METHOD_CAPABILITIES.get(method_name)
    if cap is None:
        # Method isn't dispatch-gated; allow.
        return True
    declared = getattr(backend, "capabilities", set()) or set()
    return cap in declared
