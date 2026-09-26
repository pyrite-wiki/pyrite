"""
SearchBackend conformance test suite.

Every test here runs against every registered backend via the parametrized
``backend`` fixture.  A backend passes conformance when all tests pass.
"""

from typing import Any

import pytest
from pyrite.services.access_policy import UNSCOPED


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(entry_id="e1", kb_name="test", **overrides):
    """Build a minimal entry data dict."""
    data = {
        "id": entry_id,
        "kb_name": kb_name,
        "entry_type": "note",
        "title": f"Title {entry_id}",
        "body": f"Body for {entry_id}",
        "summary": f"Summary of {entry_id}",
        "tags": [],
        "sources": [],
        "links": [],
        "metadata": {},
    }
    data.update(overrides)
    return data


# =========================================================================
# Entry lifecycle
# =========================================================================


class TestEntryLifecycle:
    def test_upsert_and_get(self, backend):
        backend.upsert_entry(_make_entry("e1"))
        entry = backend.get_entry("e1", "test")
        assert entry is not None
        assert entry["id"] == "e1"
        assert entry["title"] == "Title e1"
        assert entry["body"] == "Body for e1"

    def test_get_missing_returns_none(self, backend):
        assert backend.get_entry("nonexistent", "test") is None

    def test_upsert_update(self, backend):
        backend.upsert_entry(_make_entry("e1", title="Original"))
        backend.upsert_entry(_make_entry("e1", title="Updated"))
        entry = backend.get_entry("e1", "test")
        assert entry["title"] == "Updated"

    def test_delete_entry(self, backend):
        backend.upsert_entry(_make_entry("e1"))
        assert backend.delete_entry("e1", "test") is True
        assert backend.get_entry("e1", "test") is None

    def test_delete_nonexistent(self, backend):
        assert backend.delete_entry("nope", "test") is False

    def test_list_entries(self, backend):
        for i in range(5):
            backend.upsert_entry(_make_entry(f"e{i}"))
        entries = backend.list_entries(kb_name="test")
        assert len(entries) == 5

    def test_list_entries_with_type_filter(self, backend):
        backend.upsert_entry(_make_entry("e1", entry_type="note"))
        backend.upsert_entry(_make_entry("e2", entry_type="event"))
        entries = backend.list_entries(kb_name="test", entry_type="note")
        assert len(entries) == 1
        assert entries[0]["entry_type"] == "note"

    def test_list_entries_with_tag_filter(self, backend):
        backend.upsert_entry(_make_entry("e1", tags=["alpha", "beta"]))
        backend.upsert_entry(_make_entry("e2", tags=["gamma"]))
        entries = backend.list_entries(kb_name="test", tag="alpha")
        assert len(entries) == 1
        assert entries[0]["id"] == "e1"

    def test_list_entries_pagination(self, backend):
        for i in range(10):
            backend.upsert_entry(_make_entry(f"e{i:02d}"))
        page1 = backend.list_entries(kb_name="test", limit=3, offset=0)
        page2 = backend.list_entries(kb_name="test", limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3
        ids1 = {e["id"] for e in page1}
        ids2 = {e["id"] for e in page2}
        assert ids1.isdisjoint(ids2)

    def test_count_entries(self, backend):
        for i in range(3):
            backend.upsert_entry(_make_entry(f"e{i}"))
        assert backend.count_entries(kb_name="test") == 3

    def test_count_entries_filtered(self, backend):
        backend.upsert_entry(_make_entry("e1", entry_type="note"))
        backend.upsert_entry(_make_entry("e2", entry_type="event"))
        assert backend.count_entries(kb_name="test", entry_type="note") == 1

    def test_count_entries_by_tag(self, backend):
        backend.upsert_entry(_make_entry("e1", tags=["alpha"]))
        backend.upsert_entry(_make_entry("e2", tags=["beta"]))
        assert backend.count_entries(kb_name="test", tag="alpha") == 1

    def test_get_distinct_types(self, backend):
        backend.upsert_entry(_make_entry("e1", entry_type="note"))
        backend.upsert_entry(_make_entry("e2", entry_type="event"))
        backend.upsert_entry(_make_entry("e3", entry_type="note"))
        types = backend.get_distinct_types(kb_name="test")
        assert sorted(types) == ["event", "note"]

    def test_get_entries_for_indexing(self, backend):
        backend.upsert_entry(_make_entry("e1", file_path="notes/e1.md"))
        result = backend.get_entries_for_indexing("test")
        assert len(result) == 1
        assert result[0]["id"] == "e1"
        assert result[0]["file_path"] == "notes/e1.md"

    def test_entry_metadata(self, backend):
        backend.upsert_entry(_make_entry("e1", metadata={"custom_field": "value"}))
        entry = backend.get_entry("e1", "test")
        import json

        meta = (
            json.loads(entry["metadata"])
            if isinstance(entry["metadata"], str)
            else entry["metadata"]
        )
        assert meta["custom_field"] == "value"

    def test_entry_importance_and_status(self, backend):
        backend.upsert_entry(_make_entry("e1", importance=5, status="draft"))
        entry = backend.get_entry("e1", "test")
        assert entry["importance"] == 5
        assert entry["status"] == "draft"


# =========================================================================
# Tags
# =========================================================================


class TestTags:
    def test_tags_stored_and_retrieved(self, backend):
        backend.upsert_entry(_make_entry("e1", tags=["alpha", "beta"]))
        entry = backend.get_entry("e1", "test")
        assert sorted(entry["tags"]) == ["alpha", "beta"]

    def test_tags_replaced_on_update(self, backend):
        backend.upsert_entry(_make_entry("e1", tags=["old"]))
        backend.upsert_entry(_make_entry("e1", tags=["new1", "new2"]))
        entry = backend.get_entry("e1", "test")
        assert sorted(entry["tags"]) == ["new1", "new2"]

    def test_get_all_tags(self, backend):
        backend.upsert_entry(_make_entry("e1", tags=["alpha", "beta"]))
        backend.upsert_entry(_make_entry("e2", tags=["alpha", "gamma"]))
        tags = backend.get_all_tags(kb_name="test")
        tag_dict = dict(tags)
        assert tag_dict["alpha"] == 2
        assert tag_dict["beta"] == 1
        assert tag_dict["gamma"] == 1

    def test_get_all_tags_no_kb_filter(self, backend_with_db):
        backend, db = backend_with_db
        backend.upsert_entry(_make_entry("e1", kb_name="test", tags=["shared"]))
        backend.upsert_entry(_make_entry("e2", kb_name="other", tags=["shared"]))
        tags = backend.get_all_tags()
        tag_dict = dict(tags)
        assert tag_dict["shared"] == 2

    def test_get_tags_as_dicts(self, backend):
        backend.upsert_entry(_make_entry("e1", tags=["alpha", "beta"]))
        tags = backend.get_tags_as_dicts(kb_name="test")
        assert len(tags) == 2
        names = {t["name"] for t in tags}
        assert "alpha" in names
        assert "beta" in names

    def test_get_tags_as_dicts_with_prefix(self, backend):
        backend.upsert_entry(_make_entry("e1", tags=["topic/ai", "topic/ml", "other"]))
        tags = backend.get_tags_as_dicts(kb_name="test", prefix="topic")
        assert len(tags) == 2

    def test_search_by_tag(self, backend):
        backend.upsert_entry(_make_entry("e1", tags=["alpha"]))
        backend.upsert_entry(_make_entry("e2", tags=["beta"]))
        results = backend.search_by_tag("alpha", kb_name="test")
        assert len(results) == 1

    def test_search_by_tag_prefix(self, backend):
        backend.upsert_entry(_make_entry("e1", tags=["topic/ai"]))
        backend.upsert_entry(_make_entry("e2", tags=["topic/ml"]))
        backend.upsert_entry(_make_entry("e3", tags=["other"]))
        results = backend.search_by_tag_prefix("topic", kb_name="test")
        assert len(results) == 2

    def test_empty_tags_ignored(self, backend):
        backend.upsert_entry(_make_entry("e1", tags=["alpha", "", "beta"]))
        entry = backend.get_entry("e1", "test")
        assert sorted(entry["tags"]) == ["alpha", "beta"]


# =========================================================================
# Full-text search
# =========================================================================


class TestFullTextSearch:
    def test_search_basic(self, backend):
        backend.upsert_entry(_make_entry("e1", title="quantum computing advances"))
        backend.upsert_entry(_make_entry("e2", title="classical music review"))
        results = backend.search("quantum")
        assert len(results) >= 1
        assert any(r["id"] == "e1" for r in results)

    def test_search_with_kb_filter(self, backend_with_db):
        backend, db = backend_with_db
        backend.upsert_entry(_make_entry("e1", kb_name="test", title="quantum computing"))
        backend.upsert_entry(_make_entry("e2", kb_name="other", title="quantum physics"))
        results = backend.search("quantum", kb_name="test")
        assert len(results) == 1
        assert results[0]["kb_name"] == "test"

    def test_search_with_type_filter(self, backend):
        backend.upsert_entry(_make_entry("e1", entry_type="note", title="quantum note"))
        backend.upsert_entry(_make_entry("e2", entry_type="event", title="quantum event"))
        results = backend.search("quantum", entry_type="note")
        assert len(results) == 1
        assert results[0]["entry_type"] == "note"

    def test_search_with_tag_filter(self, backend):
        backend.upsert_entry(_make_entry("e1", title="quantum topic", tags=["science"]))
        backend.upsert_entry(_make_entry("e2", title="quantum other", tags=["music"]))
        results = backend.search("quantum", tags=["science"])
        assert len(results) == 1
        assert results[0]["id"] == "e1"

    def test_search_with_date_filter(self, backend):
        backend.upsert_entry(_make_entry("e1", title="quantum old", date="2020-01-01"))
        backend.upsert_entry(_make_entry("e2", title="quantum new", date="2024-01-01"))
        results = backend.search("quantum", date_from="2023-01-01")
        assert len(results) == 1
        assert results[0]["id"] == "e2"

    def test_search_returns_snippet(self, backend):
        backend.upsert_entry(_make_entry("e1", title="quantum", body="quantum computing body"))
        results = backend.search("quantum")
        assert len(results) >= 1
        # FTS results should include snippet and rank
        assert "snippet" in results[0]
        assert "rank" in results[0]

    def test_search_pagination(self, backend):
        for i in range(10):
            backend.upsert_entry(_make_entry(f"e{i}", title=f"quantum topic {i}"))
        page1 = backend.search("quantum", limit=3, offset=0)
        page2 = backend.search("quantum", limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3

    def test_search_by_date_range(self, backend):
        backend.upsert_entry(_make_entry("e1", date="2020-06-15"))
        backend.upsert_entry(_make_entry("e2", date="2021-03-01"))
        backend.upsert_entry(_make_entry("e3", date="2022-01-01"))
        results = backend.search_by_date_range("2020-01-01", "2021-12-31")
        assert len(results) == 2


# =========================================================================
# Graph (links)
# =========================================================================


class TestGraph:
    def _setup_linked_entries(self, backend):
        backend.upsert_entry(
            _make_entry(
                "a",
                title="Entry A",
                links=[
                    {"target": "b", "relation": "related_to"},
                ],
            )
        )
        backend.upsert_entry(
            _make_entry(
                "b",
                title="Entry B",
                links=[
                    {"target": "c", "relation": "related_to"},
                ],
            )
        )
        backend.upsert_entry(_make_entry("c", title="Entry C"))

    def test_get_backlinks(self, backend):
        self._setup_linked_entries(backend)
        backlinks = backend.get_backlinks("b", "test", readable_kbs=UNSCOPED)
        assert len(backlinks) == 1
        assert backlinks[0]["id"] == "a"

    def test_get_backlinks_empty(self, backend):
        self._setup_linked_entries(backend)
        backlinks = backend.get_backlinks("a", "test", readable_kbs=UNSCOPED)
        assert len(backlinks) == 0

    def test_get_outlinks(self, backend):
        self._setup_linked_entries(backend)
        outlinks = backend.get_outlinks("a", "test", readable_kbs=UNSCOPED)
        assert len(outlinks) == 1
        assert outlinks[0]["id"] == "b"

    def test_get_outlinks_empty(self, backend):
        self._setup_linked_entries(backend)
        outlinks = backend.get_outlinks("c", "test", readable_kbs=UNSCOPED)
        assert len(outlinks) == 0

    def test_get_backlinks_with_limit(self, backend):
        # Create many inbound links to c
        for i in range(5):
            backend.upsert_entry(
                _make_entry(
                    f"src{i}",
                    title=f"Source {i}",
                    links=[
                        {"target": "target", "relation": "related_to"},
                    ],
                )
            )
        backend.upsert_entry(_make_entry("target", title="Target"))
        backlinks = backend.get_backlinks("target", "test", limit=2, readable_kbs=UNSCOPED)
        assert len(backlinks) == 2

    def test_get_graph_data_centered(self, backend):
        self._setup_linked_entries(backend)
        graph = backend.get_graph_data(center="b", center_kb="test", depth=1, readable_kbs=UNSCOPED)
        assert "nodes" in graph
        assert "edges" in graph
        node_ids = {n["id"] for n in graph["nodes"]}
        assert "b" in node_ids
        # At depth 1, should have a and c
        assert "a" in node_ids
        assert "c" in node_ids

    def test_get_graph_data_no_center(self, backend):
        self._setup_linked_entries(backend)
        graph = backend.get_graph_data(readable_kbs=UNSCOPED)
        assert len(graph["nodes"]) >= 2
        assert len(graph["edges"]) >= 1

    def test_get_graph_data_filters_by_kb(self, backend):
        """Graph with kb_name filter should only return nodes from that KB."""
        # Setup: entries in default 'test' KB
        self._setup_linked_entries(backend)  # a->b->c all in 'test'

        # Filter to 'test' — should get a, b, c (all linked)
        graph = backend.get_graph_data(kb_name="test", readable_kbs=UNSCOPED)
        node_kbs = {n["kb_name"] for n in graph["nodes"]}
        assert node_kbs == {"test"}, f"Expected only test nodes, got {node_kbs}"

        # Filter to nonexistent KB — should get nothing
        graph2 = backend.get_graph_data(kb_name="nonexistent", readable_kbs=UNSCOPED)
        assert len(graph2["nodes"]) == 0

        # Without filter — should get all linked entries
        graph3 = backend.get_graph_data(readable_kbs=UNSCOPED)
        assert len(graph3["nodes"]) >= 2

    def test_get_most_linked(self, backend):
        self._setup_linked_entries(backend)
        most = backend.get_most_linked(kb_name="test", limit=5)
        # b has 1 incoming link, c has 1 incoming link
        assert len(most) >= 2

    def test_get_orphans(self, backend):
        backend.upsert_entry(_make_entry("orphan", title="Lonely"))
        backend.upsert_entry(
            _make_entry(
                "linked",
                links=[
                    {"target": "other", "relation": "related_to"},
                ],
            )
        )
        backend.upsert_entry(_make_entry("other"))
        orphans = backend.get_orphans(kb_name="test", readable_kbs=UNSCOPED)
        orphan_ids = {o["id"] for o in orphans}
        assert "orphan" in orphan_ids
        assert "linked" not in orphan_ids


# =========================================================================
# Sources
# =========================================================================


class TestSources:
    def test_sources_stored(self, backend):
        backend.upsert_entry(
            _make_entry(
                "e1",
                sources=[
                    {"title": "Source 1", "url": "https://example.com", "outlet": "Blog"},
                ],
            )
        )
        entry = backend.get_entry("e1", "test")
        assert len(entry["sources"]) == 1
        assert entry["sources"][0]["title"] == "Source 1"

    def test_sources_replaced_on_update(self, backend):
        backend.upsert_entry(_make_entry("e1", sources=[{"title": "Old"}]))
        backend.upsert_entry(_make_entry("e1", sources=[{"title": "New"}]))
        entry = backend.get_entry("e1", "test")
        assert len(entry["sources"]) == 1
        assert entry["sources"][0]["title"] == "New"


# =========================================================================
# Object refs
# =========================================================================


class TestObjectRefs:
    def test_refs_from(self, backend):
        backend.upsert_entry(
            _make_entry(
                "e1",
                _refs=[
                    {"target_id": "e2", "field_name": "author", "target_type": "person"},
                ],
            )
        )
        backend.upsert_entry(_make_entry("e2", entry_type="person", title="Person"))
        refs = backend.get_refs_from("e1", "test")
        assert len(refs) == 1
        assert refs[0]["id"] == "e2"
        assert refs[0]["field_name"] == "author"

    def test_refs_to(self, backend):
        backend.upsert_entry(
            _make_entry(
                "e1",
                _refs=[
                    {"target_id": "e2", "field_name": "author", "target_type": "person"},
                ],
            )
        )
        backend.upsert_entry(_make_entry("e2"))
        refs = backend.get_refs_to("e2", "test")
        assert len(refs) == 1
        assert refs[0]["id"] == "e1"

    def test_refs_replaced_on_update(self, backend):
        backend.upsert_entry(
            _make_entry(
                "e1",
                _refs=[
                    {"target_id": "old", "field_name": "ref"},
                ],
            )
        )
        backend.upsert_entry(
            _make_entry(
                "e1",
                _refs=[
                    {"target_id": "new", "field_name": "ref"},
                ],
            )
        )
        refs = backend.get_refs_from("e1", "test")
        assert len(refs) == 1
        assert refs[0]["id"] == "new"


# =========================================================================
# Blocks
# =========================================================================


class TestBlocks:
    def test_blocks_stored(self, backend):
        backend.upsert_entry(
            _make_entry(
                "e1",
                _blocks=[
                    {
                        "block_id": "b1",
                        "heading": "Section 1",
                        "content": "Content 1",
                        "position": 0,
                        "block_type": "heading",
                    },
                    {
                        "block_id": "b2",
                        "heading": "Section 2",
                        "content": "Content 2",
                        "position": 1,
                        "block_type": "heading",
                    },
                ],
            )
        )
        # Blocks don't have a direct get, but upsert shouldn't fail
        entry = backend.get_entry("e1", "test")
        assert entry is not None

    def test_blocks_replaced_on_update(self, backend):
        backend.upsert_entry(
            _make_entry(
                "e1",
                _blocks=[
                    {
                        "block_id": "b1",
                        "heading": "Old",
                        "content": "Old",
                        "position": 0,
                        "block_type": "heading",
                    },
                ],
            )
        )
        backend.upsert_entry(
            _make_entry(
                "e1",
                _blocks=[
                    {
                        "block_id": "b2",
                        "heading": "New",
                        "content": "New",
                        "position": 0,
                        "block_type": "heading",
                    },
                ],
            )
        )
        entry = backend.get_entry("e1", "test")
        assert entry is not None


# =========================================================================
# Timeline
# =========================================================================


class TestTimeline:
    def test_timeline_basic(self, backend):
        backend.upsert_entry(_make_entry("e1", date="2024-01-15", importance=3, title="Event 1"))
        backend.upsert_entry(_make_entry("e2", date="2024-02-15", importance=5, title="Event 2"))
        backend.upsert_entry(_make_entry("e3", importance=2))  # no date
        timeline = backend.get_timeline()
        assert len(timeline) == 2

    def test_timeline_date_filter(self, backend):
        backend.upsert_entry(_make_entry("e1", date="2024-01-15", importance=3))
        backend.upsert_entry(_make_entry("e2", date="2024-06-15", importance=3))
        timeline = backend.get_timeline(date_from="2024-03-01")
        assert len(timeline) == 1
        assert timeline[0]["id"] == "e2"

    def test_timeline_importance_filter(self, backend):
        backend.upsert_entry(_make_entry("e1", date="2024-01-15", importance=1))
        backend.upsert_entry(_make_entry("e2", date="2024-02-15", importance=5))
        timeline = backend.get_timeline(min_importance=3)
        assert len(timeline) == 1
        assert timeline[0]["id"] == "e2"

    def test_timeline_ordered_by_date(self, backend):
        backend.upsert_entry(_make_entry("e2", date="2024-06-01", importance=1))
        backend.upsert_entry(_make_entry("e1", date="2024-01-01", importance=1))
        timeline = backend.get_timeline()
        assert timeline[0]["id"] == "e1"
        assert timeline[1]["id"] == "e2"


# =========================================================================
# Folder queries
# =========================================================================


class TestFolderQueries:
    def test_list_entries_in_folder(self, backend):
        backend.upsert_entry(_make_entry("e1", file_path="notes/sub/e1.md"))
        backend.upsert_entry(_make_entry("e2", file_path="notes/sub/e2.md"))
        backend.upsert_entry(_make_entry("e3", file_path="other/e3.md"))
        results = backend.list_entries_in_folder("test", "notes/sub")
        assert len(results) == 2

    def test_count_entries_in_folder(self, backend):
        backend.upsert_entry(_make_entry("e1", file_path="notes/e1.md"))
        backend.upsert_entry(_make_entry("e2", file_path="notes/e2.md"))
        backend.upsert_entry(_make_entry("e3", file_path="other/e3.md"))
        assert backend.count_entries_in_folder("test", "notes") == 2

    def test_folder_excludes_collections(self, backend):
        backend.upsert_entry(_make_entry("e1", file_path="notes/e1.md", entry_type="note"))
        backend.upsert_entry(_make_entry("c1", file_path="notes/c1.md", entry_type="collection"))
        results = backend.list_entries_in_folder("test", "notes")
        assert len(results) == 1


# =========================================================================
# Global counts
# =========================================================================


class TestGlobalCounts:
    def test_global_counts(self, backend):
        backend.upsert_entry(
            _make_entry(
                "e1",
                tags=["alpha"],
                links=[
                    {"target": "e2", "relation": "related_to"},
                ],
            )
        )
        backend.upsert_entry(_make_entry("e2", tags=["beta"]))
        counts = backend.get_global_counts()
        assert counts["total_tags"] == 2
        assert counts["total_links"] == 1


# =========================================================================
# Multi-KB isolation
# =========================================================================


class TestMultiKB:
    def test_entries_isolated_by_kb(self, backend_with_db):
        backend, db = backend_with_db
        backend.upsert_entry(_make_entry("e1", kb_name="test", title="Test entry"))
        backend.upsert_entry(_make_entry("e1", kb_name="other", title="Other entry"))
        test_entry = backend.get_entry("e1", "test")
        other_entry = backend.get_entry("e1", "other")
        assert test_entry["title"] == "Test entry"
        assert other_entry["title"] == "Other entry"

    def test_list_entries_scoped_to_kb(self, backend_with_db):
        backend, db = backend_with_db
        backend.upsert_entry(_make_entry("e1", kb_name="test"))
        backend.upsert_entry(_make_entry("e2", kb_name="other"))
        entries = backend.list_entries(kb_name="test")
        assert len(entries) == 1
        assert entries[0]["kb_name"] == "test"

    def test_search_scoped_to_kb(self, backend_with_db):
        backend, db = backend_with_db
        backend.upsert_entry(_make_entry("e1", kb_name="test", title="quantum test"))
        backend.upsert_entry(_make_entry("e2", kb_name="other", title="quantum other"))
        results = backend.search("quantum", kb_name="test")
        assert len(results) == 1
        assert results[0]["kb_name"] == "test"

    def test_delete_only_in_correct_kb(self, backend_with_db):
        backend, db = backend_with_db
        backend.upsert_entry(_make_entry("e1", kb_name="test"))
        backend.upsert_entry(_make_entry("e1", kb_name="other"))
        backend.delete_entry("e1", "test")
        assert backend.get_entry("e1", "test") is None
        assert backend.get_entry("e1", "other") is not None


# =========================================================================
# Embeddings (basic — only tests interface, not actual model)
# =========================================================================


class TestEmbeddingInterface:
    """Tests the embedding API surface. Actual vector search requires sqlite-vec."""

    def test_has_embeddings_false_initially(self, backend):
        # May be False if vec not available, or True with no data — both valid
        result = backend.has_embeddings()
        assert isinstance(result, bool)

    def test_embedding_stats_structure(self, backend):
        stats = backend.embedding_stats()
        assert "count" in stats
        assert "total_entries" in stats

    def test_get_embedded_rowids_returns_set(self, backend):
        result = backend.get_embedded_rowids()
        assert isinstance(result, set)

    def test_get_entries_for_embedding(self, backend):
        backend.upsert_entry(_make_entry("e1"))
        result = backend.get_entries_for_embedding(kb_name="test")
        assert len(result) >= 1
        assert "id" in result[0]
        assert "title" in result[0]

    def test_search_semantic_empty(self, backend):
        # With no embeddings, should return empty list
        result = backend.search_semantic([0.0] * 384)
        assert isinstance(result, list)


# =========================================================================
# Semantic filters — the vector leg takes the keyword leg's filter set (#56)
# =========================================================================


def _spy_on_sql(backend, monkeypatch):
    """Record every ``(sql, params)`` the backend sends to raw SQLite.

    ``sqlite3.Connection.execute`` is read-only, so the whole connection is
    wrapped and everything else forwarded untouched. Skips the calling test for
    backends with no raw sqlite3 connection (Postgres), whose semantic leg puts
    the predicates in the same ``WHERE`` as the ordering and has no KNN budget
    to escalate.

    ``cursor()`` is wrapped as well as ``execute()``. The backend runs its raw
    SQL through a private cursor taken under a lock (#131) rather than through
    ``Connection.execute``, and ``__getattr__`` would forward ``cursor()`` to
    the real connection -- leaving this spy recording nothing at all while the
    queries still ran, which reads as "the loop never executed" rather than as
    a broken spy.
    """
    conn = getattr(backend, "_raw_conn", None)
    if conn is None:
        pytest.skip("backend has no raw sqlite3 connection / no KNN budget")

    log: list[tuple[str, Any]] = []

    class _SpyCursor:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, params=()):
            log.append((sql, params))
            self._inner.execute(sql, params)
            return self

        def __iter__(self):
            return iter(self._inner)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    class _SpyConn:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, params=()):
            log.append((sql, params))
            return self._inner.execute(sql, params)

        def cursor(self, *args, **kwargs):
            return _SpyCursor(self._inner.cursor(*args, **kwargs))

        def __getattr__(self, name):
            return getattr(self._inner, name)

    monkeypatch.setattr(backend, "_raw_conn", _SpyConn(conn))
    return log


def _near_vector(nudge: int = 0) -> list[float]:
    """A 384-dim vector. All of them are near-identical, so every embedded row
    is a KNN candidate: filtering, not ranking, is what these tests pin."""
    vec = [0.05] * 384
    vec[nudge % 384] += 0.001
    return vec


@pytest.fixture
def embedded_backend(backend):
    """A backend holding three entries that differ only in filterable columns.

    Skips cleanly when the backend cannot store an embedding (e.g. SQLite built
    without the sqlite-vec extension), so the suite stays green where vectors
    are unavailable.
    """
    specs = [
        {
            "entry_id": "mech",
            "entry_type": "mechanism",
            "tags": ["oversight"],
            "state": "MI",
            "fips": "26163",
            "status": "unprocessed",
            "date": "2026-01-10",
        },
        {
            "entry_id": "theme",
            "entry_type": "theme",
            "tags": ["capture"],
            "state": "LA",
            "fips": "22071",
            "status": "processed",
            "date": "2026-02-20",
        },
        {
            "entry_id": "task",
            "entry_type": "task",
            "tags": ["workflow"],
            "state": None,
            "fips": None,
            "status": "unprocessed",
            "date": "2026-03-30",
        },
    ]
    for i, spec in enumerate(specs):
        entry_id = spec.pop("entry_id")
        backend.upsert_entry(_make_entry(entry_id, **spec))
        if not backend.upsert_embedding(entry_id, "test", _near_vector(i)):
            pytest.skip("backend cannot store embeddings (no vector support)")
    if not backend.has_embeddings():
        pytest.skip("backend cannot store embeddings (no vector support)")
    return backend


class TestSemanticFilterConformance:
    """``search_semantic`` must honour every filter ``search`` honours.

    Hybrid mode fuses the two legs, so a filter applied on only one of them
    silently returns entries the caller excluded (#56). A backend that returns
    unfiltered rows here is not conformant.
    """

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"entry_type": "mechanism"}, {"mech"}),
            ({"entry_type": "zzz-not-a-real-type"}, set()),
            ({"tags": ["capture"]}, {"theme"}),
            ({"tags": ["zzz-no-such-tag"]}, set()),
            ({"state": "MI"}, {"mech"}),
            ({"state": "ZZ"}, set()),
            ({"fips": "26163"}, {"mech"}),
            ({"fips": "99999"}, set()),
            ({"status": "processed"}, {"theme"}),
            ({"status": "zzz-bogus-status"}, set()),
            ({"date_from": "2026-02-01"}, {"theme", "task"}),
            ({"date_to": "2026-01-31"}, {"mech"}),
            ({"date_from": "2026-02-01", "date_to": "2026-02-28"}, {"theme"}),
            ({"entry_type": "mechanism", "state": "MI"}, {"mech"}),
            ({"entry_type": "mechanism", "state": "LA"}, set()),
        ],
    )
    def test_search_semantic_honours_filter(self, embedded_backend, kwargs, expected):
        rows = embedded_backend.search_semantic(_near_vector(), kb_name="test", limit=10, **kwargs)
        assert {r["id"] for r in rows} == expected

    def test_search_semantic_unfiltered_returns_all(self, embedded_backend):
        """Guard against over-filtering: no filter means every entry."""
        rows = embedded_backend.search_semantic(_near_vector(), kb_name="test", limit=10)
        assert {r["id"] for r in rows} == {"mech", "theme", "task"}

    def test_search_semantic_excludes_archived_by_default(self, embedded_backend):
        """``include_archived`` is a filter too, and it was the one left behind.

        Unlike the others it is a default *exclusion* rather than a value the
        caller supplies, which is how it stayed in the keyword branch while
        every other filter was threaded through — archived entries reached
        semantic and hybrid results while the contract promised they could not.
        """
        backend = embedded_backend
        backend.upsert_entry(_make_entry("archived-one", lifecycle="archived"))
        if not backend.upsert_embedding("archived-one", "test", _near_vector(9)):
            pytest.skip("backend cannot store embeddings (no vector support)")

        rows = backend.search_semantic(_near_vector(), kb_name="test", limit=10)
        assert {r["id"] for r in rows} == {"mech", "theme", "task"}

        rows = backend.search_semantic(
            _near_vector(), kb_name="test", limit=10, include_archived=True
        )
        assert {r["id"] for r in rows} == {"mech", "theme", "task", "archived-one"}

    def test_max_distance_does_not_cost_recall(self, embedded_backend):
        """A cutoff that excludes nothing must not reduce the rows returned.

        Postgres applied ``max_distance`` in Python *after* ``LIMIT``, so a
        culled row was one the LIMIT had already spent — it under-returned
        where sqlite escalates its ``k``. Asking for as many rows as match,
        with a cutoff wide enough to exclude none, must give all of them on
        every backend.
        """
        rows = embedded_backend.search_semantic(
            _near_vector(), kb_name="test", limit=3, max_distance=2.0
        )
        assert {r["id"] for r in rows} == {"mech", "theme", "task"}

    def test_search_semantic_fills_limit_despite_selective_filter(self, embedded_backend):
        """A selective filter must not cost recall.

        sqlite-vec applies its ``k`` budget before any join predicate, so an
        implementation that filters the k nearest afterwards silently
        under-returns. Two entries share a status; asking for two must get two.
        """
        rows = embedded_backend.search_semantic(
            _near_vector(), kb_name="test", limit=2, status="unprocessed"
        )
        assert {r["id"] for r in rows} == {"mech", "task"}


# =========================================================================
# KNN budget — escalation must stay inside the backend's own k ceiling (#56)
# =========================================================================


class TestSemanticKnnBudget:
    """Escalating ``k`` to recover recall must not exceed what the engine allows.

    sqlite-vec 0.1.9 rejects ``k > 4096`` outright::

        OperationalError: k value in knn query too large,
        provided 16384 and the limit is 4096

    An escalation loop clamped only against the table's row count therefore
    raises on any index bigger than the cap — which reaches users as an HTTP
    400 ``SEARCH_FAILED``, an unhandled exception out of MCP ``kb_search`` and
    a silent keyword-only fallback in the AI endpoint. Postgres has no such
    ceiling and passes these trivially.
    """

    @pytest.fixture
    def big_embedded_backend(self, backend):
        """Just over sqlite-vec's 4096 ``k`` cap, so escalation must clamp.

        All vectors are near-identical, and exactly one row carries the
        filterable value, so the filtered query is maximally selective: the
        escalation loop runs to its ceiling on every backend.
        """
        backend.upsert_entry(_make_entry("probe"))
        if not backend.upsert_embedding("probe", "test", _near_vector()):
            pytest.skip("backend cannot store embeddings (no vector support)")
        backend.delete_entry("probe", "test")

        for i in range(4097):
            entry_type = "needle" if i == 0 else "note"
            backend.upsert_entry(_make_entry(f"e{i}", entry_type=entry_type))
            backend.upsert_embedding(f"e{i}", "test", _near_vector(i))
        return backend

    def test_filtered_search_above_the_k_cap_does_not_raise(self, big_embedded_backend):
        """A selective filter over >4096 embedded rows must return, not raise."""
        rows = big_embedded_backend.search_semantic(
            _near_vector(), kb_name="test", limit=5, entry_type="needle"
        )
        assert {r["id"] for r in rows} == {"e0"}

    def test_unfiltered_search_above_the_k_cap_does_not_raise(self, big_embedded_backend):
        """``max_distance`` culling drives the same escalation with no filter.

        A cutoff no row can satisfy means ``limit`` is never reached, so the
        loop escalates ``k`` to its ceiling exactly as a selective filter does.
        The honest answer is an empty list, never an exception.
        """
        rows = big_embedded_backend.search_semantic(
            _near_vector(), kb_name="test", limit=5, max_distance=-1.0
        )
        assert rows == []


class TestSemanticKnnEscalation:
    """The escalation loop must actually run more than once, and be observed.

    The filter-conformance tests above use three entries, where the first ``k``
    (``limit * 3``) already covers the table — the loop body runs once and the
    escalation branch is never exercised. These tests put the needle outside
    the first budget so a second iteration is the only way to find it.
    """

    @pytest.fixture
    def haystack_backend(self, backend):
        """200 rows where only the *farthest* one matches the filter.

        The first ``k`` (``limit * 3`` = 3) cannot reach it, so a correct
        implementation escalates; one that filters the k nearest afterwards
        returns nothing.
        """
        backend.upsert_entry(_make_entry("probe"))
        if not backend.upsert_embedding("probe", "test", _near_vector()):
            pytest.skip("backend cannot store embeddings (no vector support)")
        backend.delete_entry("probe", "test")

        for i in range(200):
            # Distance grows with i, so "needle" (i=199) is the farthest row.
            entry_type = "needle" if i == 199 else "note"
            backend.upsert_entry(_make_entry(f"h{i}", entry_type=entry_type))
            # Distances grow with i but stay well inside the default
            # ``max_distance`` cutoff, so ordering — not culling — is what
            # puts the needle out of reach of the first ``k``.
            vec = [0.05] * 384
            vec[0] += 0.0001 * i
            backend.upsert_embedding(f"h{i}", "test", vec)
        return backend

    def test_selective_filter_beyond_the_first_budget_still_finds_it(self, haystack_backend):
        """The needle is farther than ``limit * 3`` rows: escalation finds it."""
        rows = haystack_backend.search_semantic(
            _near_vector(), kb_name="test", limit=1, entry_type="needle"
        )
        assert {r["id"] for r in rows} == {"h199"}

    def test_escalation_loop_runs_a_second_iteration(self, haystack_backend, monkeypatch):
        """Pin the mechanism, not just the outcome: ``k`` must grow.

        Counts the distinct ``k`` values the backend actually asks for. One
        value means the loop never escalated and the assertion above passed by
        accident. Skipped for backends that do not use a KNN budget at all
        (Postgres puts the predicates in the same ``WHERE`` as the ordering).
        """
        sql_log = _spy_on_sql(haystack_backend, monkeypatch)
        rows = haystack_backend.search_semantic(
            _near_vector(), kb_name="test", limit=1, entry_type="needle"
        )
        assert {r["id"] for r in rows} == {"h199"}
        seen_k = [p[1] for s, p in sql_log if "MATCH" in s and "k = ?" in s]
        # The same k can appear twice in one round (the main query plus the
        # filter-independent size probe); what must grow is the sequence of
        # distinct budgets.
        budgets = sorted(set(seen_k))
        assert len(budgets) >= 2, f"escalation loop ran once only; k values: {seen_k}"
        assert seen_k == sorted(seen_k), f"k must never shrink: {seen_k}"

    def test_unfiltered_hot_path_runs_one_query(self, haystack_backend, monkeypatch):
        """No filter, ``limit`` filled on the first budget: one query, no COUNT.

        The unconditional ``SELECT COUNT(*) FROM vec_entry`` an earlier draft
        used to size the escalation ceiling is a full scan of the vector table
        on every semantic search, paid even when nothing escalates. The KNN
        result itself already says when ``k`` has covered the table: fewer rows
        back than ``k`` asked for means there are no more neighbours.
        """
        sql_log = _spy_on_sql(haystack_backend, monkeypatch)
        rows = haystack_backend.search_semantic(_near_vector(), kb_name="test", limit=5)
        assert len(rows) == 5

        scans = [s for s, _ in sql_log if "COUNT(*) FROM vec_entry" in " ".join(s.split())]
        assert scans == [], f"unfiltered hot path scanned the whole vector table: {scans}"
        knn = [s for s, _ in sql_log if "MATCH" in s and "k = ?" in s]
        assert len(knn) == 1, f"unfiltered hot path ran {len(knn)} queries against vec_entry"
