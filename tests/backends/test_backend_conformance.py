"""
SearchBackend conformance test suite.

Every test here runs against every registered backend via the parametrized
``backend`` fixture.  A backend passes conformance when all tests pass.
"""

import math
from typing import Any

import pytest
from pyrite.services.access_policy import UNSCOPED
from pyrite.storage.backends import sqlite_backend


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


class TestNonEmptyFilter:
    """``non_empty`` (#200): leave out entries with no body and no summary.

    Blank means nothing but space, tab, CR or LF (or NULL). The default
    returns them; a summary without a body is not blank.
    """

    BLANK_BODIES = {
        "empty": "",
        "null": None,
        "spaces": "   ",
        "mixed": " \t\r\n \n\t",
    }

    def _populate(self, backend):
        for name, body in self.BLANK_BODIES.items():
            backend.upsert_entry(
                _make_entry(f"blank-{name}", title="zebra crossing", body=body, summary="")
            )
        backend.upsert_entry(
            _make_entry("blank-summary-ws", title="zebra crossing", body="", summary=" \t\r\n ")
        )
        backend.upsert_entry(
            _make_entry("summary-only", title="zebra crossing", body="", summary="a real summary")
        )
        backend.upsert_entry(_make_entry("with-body", title="zebra crossing", body="some words"))

    def test_keyword_leg_excludes_blank_and_keeps_the_rest(self, backend):
        self._populate(backend)
        got = {r["id"] for r in backend.search("zebra", non_empty=True)}
        assert got == {"summary-only", "with-body"}

    def test_keyword_leg_default_returns_them(self, backend):
        self._populate(backend)
        got = {r["id"] for r in backend.search("zebra")}
        assert {f"blank-{n}" for n in self.BLANK_BODIES} | {"blank-summary-ws"} <= got

    def test_limit_counts_only_qualifying_rows(self, backend):
        self._populate(backend)
        got = backend.search("zebra", non_empty=True, limit=2)
        assert {r["id"] for r in got} == {"summary-only", "with-body"}

    def test_semantic_leg(self, backend):
        if not getattr(backend, "vec_available", False):
            pytest.skip("vector search not available on this backend")
        self._populate(backend)
        vec = [0.05] * 384
        for eid in [f"blank-{n}" for n in self.BLANK_BODIES] + [
            "blank-summary-ws",
            "summary-only",
            "with-body",
        ]:
            backend.upsert_embedding(eid, "test", vec)
        got = {r["id"] for r in backend.search_semantic(vec, limit=20, non_empty=True)}
        assert got == {"summary-only", "with-body"}
        everything = {r["id"] for r in backend.search_semantic(vec, limit=20)}
        assert "blank-spaces" in everything


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

    def test_backlink_relation_fields_match_contract(self, backend, monkeypatch):
        """#622: Keep documented backlink relation fields consistent per backend."""
        from pyrite.schema import provenance

        backend.upsert_entry(_make_entry("target"))
        backend.upsert_entry(
            _make_entry(
                "declared-source",
                links=[{"target": "target", "relation": "supports"}],
            )
        )
        backend.upsert_entry(
            _make_entry(
                "custom-source",
                links=[{"target": "target", "relation": "informs"}],
            )
        )
        # This type is still unknown when the link is indexed, so its stored
        # inverse remains the fallback after the type is registered below.
        backend.upsert_entry(
            _make_entry(
                "stale-source",
                links=[{"target": "target", "relation": "transclusion"}],
            )
        )

        declared_types = dict(provenance.get_all_relationship_types())
        assert "transclusion" not in declared_types
        monkeypatch.setattr(
            provenance,
            "get_all_relationship_types",
            lambda: {
                **declared_types,
                "transclusion": {
                    "inverse": "transcluded_by",
                    "description": "Declared after the link was indexed",
                },
            },
        )

        expected = {
            "declared-source": {
                "forward_relation": "supports",
                "relation": "supported_by",
                "inverse_relation": "supported_by",
            },
            "custom-source": {
                "forward_relation": "informs",
                "relation": "related_to",
                "inverse_relation": None,
            },
            "stale-source": {
                "forward_relation": "transclusion",
                "relation": "related_to",
                "inverse_relation": "transcluded_by",
            },
        }

        def relation_fields(rows):
            return {
                row["id"]: {
                    field: row[field]
                    for field in ("forward_relation", "relation", "inverse_relation")
                }
                for row in rows
            }

        backlinks = backend.get_backlinks("target", "test", readable_kbs=UNSCOPED)
        assert relation_fields(backlinks) == expected

        all_backlinks = backend.get_all_backlinks_for_kb("test")["target"]
        assert relation_fields(all_backlinks) == expected

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

    def test_the_similarity_floor_does_not_cost_recall(self, embedded_backend):
        """A cutoff that excludes nothing must not reduce the rows returned.

        Postgres applied the cutoff in Python *after* ``LIMIT``, so a
        culled row was one the LIMIT had already spent — it under-returned
        where sqlite escalates its ``k``. Asking for as many rows as match,
        with a cutoff wide enough to exclude none, must give all of them on
        every backend.
        """
        rows = embedded_backend.search_semantic(
            _near_vector(), kb_name="test", limit=3, min_similarity=-1.0
        )
        assert {r["id"] for r in rows} == {"mech", "theme", "task"}

    @pytest.mark.control(
        reason="the three-entry fixture fits inside the first k on dev, so the filter never "
        "culls a candidate; the beyond-the-cap regime is "
        "TestSemanticKnnBudget::test_selective_filter_reaches_candidates_beyond_the_k_cap"
    )
    def test_search_semantic_fills_limit_despite_selective_filter(self, embedded_backend):
        """A selective filter must not cost recall.

        Two entries share a status; asking for both must get both after the
        semantic backend applies the predicate to its KNN candidate set.
        """
        rows = embedded_backend.search_semantic(
            _near_vector(), kb_name="test", limit=2, status="unprocessed"
        )
        assert {r["id"] for r in rows} == {"mech", "task"}


# =========================================================================
# Similarity — one number, the same on every backend (#668)
# =========================================================================


def _vector_at(cosine: float, axis: int, length: float = 1.0) -> list[float]:
    """A vector of ``length`` whose cosine with ``_QUERY`` (axis 0) is ``cosine``."""
    vec = [0.0] * 384
    vec[0] = cosine * length
    vec[axis] = math.sqrt(1.0 - cosine * cosine) * length
    return vec


_QUERY = [1.0] + [0.0] * 383


class TestSimilarityConformance:
    """Every row of the vector leg carries ``similarity``: cosine, 3 places.

    Before #668 a row carried only ``distance``, which is sqlite-vec's L2 on
    one backend and pgvector's cosine distance on the other, and one cutoff of
    1.3 was applied to both: a floor of cos 0.155 on SQLite and no floor at
    all on Postgres. These tests state the numbers, so each backend is held to
    the same value rather than to the other backend.
    """

    @pytest.fixture
    def known(self, backend_with_db):
        """Entries at cos 0.8, 0.6, 0.3 and 0.1 to ``_QUERY``; one in another KB.

        ``long`` is three times unit length: its cosine is still 0.6, and
        ``1 - d**2 / 2`` over its L2 distance is not.
        """
        specs = [
            ("near", "test", 0.8, 1.0),
            ("long", "test", 0.6, 3.0),
            ("far", "test", 0.3, 1.0),
            ("cut", "test", 0.1, 1.0),
            ("elsewhere", "other", 0.7, 1.0),
        ]
        backend, _db = backend_with_db
        for axis, (entry_id, kb, cosine, length) in enumerate(specs, start=1):
            backend.upsert_entry(_make_entry(entry_id, kb_name=kb))
            if not backend.upsert_embedding(entry_id, kb, _vector_at(cosine, axis, length)):
                pytest.skip("backend cannot store embeddings (no vector support)")
        if not backend.has_embeddings():
            pytest.skip("backend cannot store embeddings (no vector support)")
        return backend

    def test_similarity_is_the_cosine_of_the_two_vectors(self, known):
        rows = known.search_semantic(_QUERY, kb_name="test", limit=10)
        assert [(r["id"], r["similarity"]) for r in rows] == [
            ("near", 0.8),
            ("long", 0.6),
            ("far", 0.3),
        ]

    @pytest.mark.control(reason="`distance` is kept as it was, for one release")
    def test_rows_still_carry_the_backends_own_distance(self, known):
        """``distance`` stays for one release; nothing asserts what it means."""
        rows = known.search_semantic(_QUERY, kb_name="test", limit=10)
        assert all(isinstance(r["distance"], float) for r in rows)

    @pytest.mark.control(
        reason="SQLite already cut here (L2 1.3 is cos 0.155); it is Postgres that cut nothing"
    )
    def test_an_entry_at_cosine_point_one_is_cut_by_default(self, known):
        """The default floor is 0.155, on every backend."""
        rows = known.search_semantic(_QUERY, kb_name="test", limit=10)
        assert "cut" not in {r["id"] for r in rows}

    def test_the_floor_is_the_callers_to_move(self, known):
        rows = known.search_semantic(_QUERY, kb_name="test", limit=10, min_similarity=0.0)
        assert [r["id"] for r in rows] == ["near", "long", "far", "cut"]
        assert rows[-1]["similarity"] == 0.1
        rows = known.search_semantic(_QUERY, kb_name="test", limit=10, min_similarity=0.7)
        assert [r["id"] for r in rows] == ["near"]

    def test_kb_names_is_applied_inside_the_query(self, known):
        """The readable set costs no recall: ``limit`` readable rows come back
        however many nearer rows sit in a KB outside it."""
        rows = known.search_semantic(_QUERY, limit=1, kb_names={"other"})
        assert [(r["id"], r["similarity"]) for r in rows] == [("elsewhere", 0.7)]
        rows = known.search_semantic(_QUERY, limit=10, kb_names={"test", "other"})
        assert [r["id"] for r in rows] == ["near", "elsewhere", "long", "far"]
        assert known.search_semantic(_QUERY, limit=10, kb_names=set()) == []
        assert known.search_semantic(_QUERY, limit=10, kb_names={"test"}, kb_name="other") == []

    def test_a_readable_set_reaches_rows_beyond_4096_nearer_unreadable_ones(self, backend_with_db):
        """The readable set at the real size of sqlite-vec's ceiling (#668).

        4,100 rows in `test`, all nearer to the query than the three rows in
        `other`. A caller who may read only `other` gets its three rows: the
        readable set is a candidate filter, inside the KNN, like `kb_name`.
        Applied after the 4,096 nearest it finds nothing.
        """
        backend, _db = backend_with_db
        backend.upsert_entry(_make_entry("probe"))
        if not backend.upsert_embedding("probe", "test", _near_vector()):
            pytest.skip("backend cannot store embeddings (no vector support)")
        backend.delete_entry("probe", "test")
        for i in range(4100):
            backend.upsert_entry(_make_entry(f"n{i}"))
            backend.upsert_embedding(f"n{i}", "test", _near_vector(i))
        for i in range(3):
            backend.upsert_entry(_make_entry(f"mine-{i}", kb_name="other"))
            far = _near_vector()
            far[i] += 0.4
            backend.upsert_embedding(f"mine-{i}", "other", far)

        rows = backend.search_semantic(_near_vector(), limit=3, kb_names={"other"})

        assert {r["id"] for r in rows} == {"mine-0", "mine-1", "mine-2"}
        assert not getattr(rows, "capped", False)
        assert {r["kb_name"] for r in rows} == {"other"}

    @pytest.mark.parametrize(
        ("filters", "expected"),
        [
            ({"kb_name": "other"}, {"far-0", "far-1"}),
            ({"kb_names": {"other"}}, {"far-0", "far-1"}),
            ({"entry_type": "rare"}, {"far-0", "far-1"}),
            ({"tags": ["rare"]}, {"far-0", "far-1"}),
            ({"state": "MI"}, {"far-0", "far-1"}),
            ({"fips": "26163"}, {"far-0", "far-1"}),
            ({"status": "processed"}, {"far-0", "far-1"}),
            ({"date_from": "2026-02-01"}, {"far-0", "far-1"}),
            ({"date_to": "2025-12-31"}, {"early-0", "early-1"}),
        ],
        ids=lambda v: next(iter(v)) if isinstance(v, dict) else None,
    )
    def test_every_caller_filter_rides_inside_the_knn(
        self, backend_with_db, monkeypatch, filters, expected
    ):
        """One case per condition that makes a search "selective", the readable
        set among them: each must put its predicate inside the KNN.

        Twenty rows nearer than the ones the filter admits, at a ceiling of 8,
        and ``include_archived=True``. That last part matters: without it the
        archived retry pushes the whole WHERE into the KNN when a search comes
        up short, and so rescues a filter that was left out of the KNN: the
        test passes for the wrong reason. With archived rows included there is
        no retry, and a filter applied after the k nearest finds nothing.
        """
        backend, _db = backend_with_db
        if hasattr(backend, "_raw_conn"):
            monkeypatch.setattr(sqlite_backend, "_SQLITE_VEC_MAX_K", 8)
        common = {"status": "unprocessed", "date": "2026-01-10"}
        backend.upsert_entry(_make_entry("probe"))
        if not backend.upsert_embedding("probe", "test", _near_vector()):
            pytest.skip("backend cannot store embeddings (no vector support)")
        backend.delete_entry("probe", "test")
        for i in range(20):
            backend.upsert_entry(_make_entry(f"near-{i}", **common))
            backend.upsert_embedding(f"near-{i}", "test", _near_vector(i))
        rare = {
            "kb_name": "other",
            "entry_type": "rare",
            "tags": ["rare"],
            "state": "MI",
            "fips": "26163",
            "status": "processed",
            "date": "2026-02-20",
        }
        for i in range(2):
            far = _near_vector()
            far[i] += 0.4
            backend.upsert_entry(_make_entry(f"far-{i}", **rare))
            backend.upsert_embedding(f"far-{i}", "other", far)
            backend.upsert_entry(_make_entry(f"early-{i}", status="unprocessed", date="2025-06-01"))
            backend.upsert_embedding(f"early-{i}", "test", far)

        rows = backend.search_semantic(_near_vector(), limit=2, include_archived=True, **filters)

        assert {r["id"] for r in rows} == expected
        assert not getattr(rows, "capped", False)

    def test_has_embeddings_can_be_asked_of_some_kbs(self, known):
        """What a scoped search says about embeddings depends on its own KBs."""
        assert known.has_embeddings(kb_name="other")
        known.delete_embedding("elsewhere", "other")  # `other` keeps its entry, loses its vector
        assert known.has_embeddings()
        assert known.has_embeddings(kb_name="test")
        assert known.has_embeddings(kb_names={"test", "other"})
        assert not known.has_embeddings(kb_name="other")
        assert not known.has_embeddings(kb_names={"other"})
        assert not known.has_embeddings(kb_names=set())
        assert not known.has_embeddings(kb_name="test", kb_names={"other"})

    def test_a_vector_with_no_direction_is_never_a_match(self, known):
        known.upsert_entry(_make_entry("zero"))
        known.upsert_embedding("zero", "test", [0.0] * 384)
        rows = known.search_semantic(_QUERY, kb_name="test", limit=10, min_similarity=-1.0)
        assert "zero" not in {r["id"] for r in rows}


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
    ceiling and passes these trivially. Selective SQLite predicates are now
    applied before KNN, but more than 4096 matching rows or distance culling
    can still limit recall.
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

    def test_rows_the_ceiling_hides_are_returned_or_the_result_says_capped(
        self, big_embedded_backend
    ):
        """An empty result must not look like "nothing matches" when it is not.

        4097 near-identical rows, and one row of another type that is farther
        than all of them (cos 0.6, well above the floor). Postgres filters in
        its WHERE and returns it. sqlite-vec considers the 4096 nearest and
        never reaches it: the result is empty, and must be marked ``capped``
        (`SemanticRows`) so the search verdict does not call it an absence
        (#668). A backend that filters before its KNN returns the row, and
        that passes too.
        """
        backend = big_embedded_backend
        query = _near_vector()
        norm = math.sqrt(sum(v * v for v in query))
        far = [0.6 * v / norm for v in query]
        far[383] += 0.8  # cos 0.6 with the query, to within the 0.05 it adds on that axis
        backend.upsert_entry(_make_entry("far", entry_type="rare"))
        backend.upsert_embedding("far", "test", far)

        rows = backend.search_semantic(query, kb_name="test", limit=1, entry_type="rare")

        assert [r["id"] for r in rows] == ["far"] or getattr(rows, "capped", False)

    @pytest.mark.control(reason="the marker must not fire when no filter hid anything")
    def test_a_result_the_floor_cut_short_is_not_capped(self, big_embedded_backend):
        """Only filtered rows can hide beyond the ceiling. With no filter, what
        lies beyond the 4096 nearest is farther still, so a short result is the
        whole answer."""
        rows = big_embedded_backend.search_semantic(
            _near_vector(), kb_name=None, limit=5, min_similarity=2.0
        )
        assert rows == [] and not getattr(rows, "capped", False)

    @pytest.fixture
    def far_needle_backend(self, backend):
        """More than 4096 rows nearer to the query than the one matching row.

        The needle is the *farthest* vector, so the 4096 nearest are all
        ``note`` rows: a filter applied after the KNN sees none of them match.
        """
        backend.upsert_entry(_make_entry("probe"))
        if not backend.upsert_embedding("probe", "test", _near_vector()):
            pytest.skip("backend cannot store embeddings (no vector support)")
        backend.delete_entry("probe", "test")

        for i in range(4100):
            backend.upsert_entry(_make_entry(f"n{i}", entry_type="note", tags=["common"]))
            backend.upsert_embedding(f"n{i}", "test", _near_vector(i))
        for i in range(3):
            backend.upsert_entry(_make_entry(f"needle-{i}", entry_type="needle", tags=["rare"]))
            far = _near_vector()
            far[i] += 0.4
            backend.upsert_embedding(f"needle-{i}", "test", far)
        return backend

    def test_real_cap_filter_reaches_rows_beyond_4096_nearer_rows(
        self, far_needle_backend, monkeypatch
    ):
        """The bug at its real size: 4,100 nearer rows, matches farther than all of them."""
        by_type = far_needle_backend.search_semantic(
            _near_vector(), kb_name="test", limit=3, entry_type="needle"
        )
        assert {r["id"] for r in by_type} == {"needle-0", "needle-1", "needle-2"}
        by_tag = far_needle_backend.search_semantic(
            _near_vector(), kb_name="test", limit=3, tags=["rare"]
        )
        assert {r["id"] for r in by_tag} == {"needle-0", "needle-1", "needle-2"}

        # The stated limit of the fix: when more than 4,096 rows match, a
        # search still caps at 4,096 and must not raise (sqlite-vec refuses
        # ``k`` above it). Postgres has no such ceiling and returns all 4,100.
        rows = far_needle_backend.search_semantic(
            _near_vector(), kb_name="test", limit=4500, entry_type="note"
        )
        assert len(rows) == (4096 if hasattr(far_needle_backend, "_raw_conn") else 4100)
        assert all(r["entry_type"] == "note" for r in rows)

        # A limit between half the cap and the cap (``k`` = 2 * limit clamps
        # to 4096 on the first pass): filled, so one KNN statement, with the
        # candidate subquery when filtered and without it when not.
        if hasattr(far_needle_backend, "_raw_conn"):
            sql_log = _spy_on_sql(far_needle_backend, monkeypatch)
            filtered = far_needle_backend.search_semantic(
                _near_vector(), kb_name="test", limit=3000, entry_type="note"
            )
            plain = far_needle_backend.search_semantic(_near_vector(), limit=3000)
            knn = [" ".join(s.split()) for s, _ in sql_log if "MATCH" in s and "k = ?" in s]
            assert len(filtered) == len(plain) == 3000
            assert len(knn) == 2, knn
            assert "rowid IN" in knn[0] and "rowid IN" not in knn[1], knn

    @pytest.mark.parametrize(
        (
            "filter_name",
            "target_value",
            "distractor_value",
            "target_overrides",
            "distractor_overrides",
        ),
        [
            ("entry_type", "needle", "note", {}, {}),
            ("tags", ["target"], ["noise"], {"tags": ["target"]}, {"tags": ["noise"]}),
            ("state", "MI", "LA", {"state": "MI"}, {"state": "LA"}),
            ("fips", "26163", "22071", {"fips": "26163"}, {"fips": "22071"}),
            (
                "status",
                "processed",
                "unprocessed",
                {"status": "processed"},
                {"status": "unprocessed"},
            ),
            (
                "date_from",
                "2026-02-01",
                "2026-02-01",
                {"date": "2026-02-15"},
                {"date": "2026-01-15"},
            ),
            ("date_to", "2026-02-28", "2026-02-28", {"date": "2026-02-15"}, {"date": "2026-03-15"}),
        ],
        ids=["entry-type", "tags", "state", "fips", "status", "date-from", "date-to"],
    )
    def test_selective_filter_reaches_candidates_beyond_the_k_cap(
        self,
        backend,
        monkeypatch,
        filter_name,
        target_value,
        distractor_value,
        target_overrides,
        distractor_overrides,
    ):
        """Each supported filter keeps matching rows beyond the small KNN cap."""
        if not hasattr(backend, "_raw_conn"):
            pytest.skip("only SQLite has a hard KNN candidate cap")

        monkeypatch.setattr(sqlite_backend, "_SQLITE_VEC_MAX_K", 8)
        query = _near_vector()
        for i in range(20):
            entry_id = f"distractor-{i}"
            backend.upsert_entry(_make_entry(entry_id, entry_type="note", **distractor_overrides))
            if not backend.upsert_embedding(entry_id, "test", query):
                pytest.skip("backend cannot store embeddings (no vector support)")
        for i in range(3):
            entry_id = f"needle-{i}"
            backend.upsert_entry(_make_entry(entry_id, entry_type="needle", **target_overrides))
            if not backend.upsert_embedding(entry_id, "test", _near_vector(i + 1)):
                pytest.skip("backend cannot store embeddings (no vector support)")

        with backend._raw_cursor() as cur:
            matching_vectors = cur.execute(
                "SELECT COUNT(*) FROM vec_entry v JOIN entry e ON v.rowid = e.rowid "
                "WHERE e.kb_name = ?",
                ("test",),
            ).fetchone()[0]
        assert matching_vectors == 23, "embedding rowids must match entry rowids for KNN filters"

        rows = backend.search_semantic(
            query, kb_name="test", limit=2, **{filter_name: target_value}
        )
        assert len(rows) == 2
        assert all(row["id"].startswith("needle-") for row in rows)

    def test_archived_exclusion_alone_does_not_let_archived_rows_fill_the_cap(
        self, backend, monkeypatch
    ):
        """No caller filter at all: archived rows nearer than live ones must not hide them.

        The default archived exclusion applies to every search, so it must
        also reach the KNN when the nearest ``k`` rows are all archived
        (review of #716, item 4). Without a ``kb_name`` nothing else makes
        the query selective.
        """
        if not hasattr(backend, "_raw_conn"):
            pytest.skip("only SQLite has a hard KNN candidate cap")

        monkeypatch.setattr(sqlite_backend, "_SQLITE_VEC_MAX_K", 8)
        for i in range(20):
            backend.upsert_entry(_make_entry(f"gone-{i}", lifecycle="archived"))
            if not backend.upsert_embedding(f"gone-{i}", "test", _near_vector()):
                pytest.skip("backend cannot store embeddings (no vector support)")
        for i in range(3):
            backend.upsert_entry(_make_entry(f"live-{i}"))
            far = _near_vector()
            far[i] += 0.3
            backend.upsert_embedding(f"live-{i}", "test", far)

        rows = backend.search_semantic(_near_vector(), limit=2)
        assert len(rows) == 2
        assert all(r["id"].startswith("live-") for r in rows)

        explicit = backend.search_semantic(_near_vector(), limit=8, include_archived=True)
        assert len(explicit) == 8, "include_archived=True must still see the archived rows"

    def test_kb_filter_keeps_other_kbs_out_of_the_cap(self, backend_with_db, monkeypatch):
        """``kb_name`` is a filter like the rest: rows of another KB cannot fill the cap."""
        backend, _db = backend_with_db
        if not hasattr(backend, "_raw_conn"):
            pytest.skip("only SQLite has a hard KNN candidate cap")

        monkeypatch.setattr(sqlite_backend, "_SQLITE_VEC_MAX_K", 8)
        for i in range(20):
            backend.upsert_entry(_make_entry(f"other-{i}", kb_name="other"))
            if not backend.upsert_embedding(f"other-{i}", "other", _near_vector()):
                pytest.skip("backend cannot store embeddings (no vector support)")
        for i in range(3):
            backend.upsert_entry(_make_entry(f"mine-{i}"))
            far = _near_vector()
            far[i] += 0.3
            backend.upsert_embedding(f"mine-{i}", "test", far)

        rows = backend.search_semantic(_near_vector(), kb_name="test", limit=3)
        assert {r["id"] for r in rows} == {"mine-0", "mine-1", "mine-2"}

    @pytest.mark.control(
        reason="nothing matches, so dev also returned []; pins empty-not-error after the fix"
    )
    def test_zero_matches_beyond_the_k_cap_returns_empty(self, backend, monkeypatch):
        """A selective filter with no matches returns empty despite nearer distractors."""
        if not hasattr(backend, "_raw_conn"):
            pytest.skip("only SQLite has a hard KNN candidate cap")

        monkeypatch.setattr(sqlite_backend, "_SQLITE_VEC_MAX_K", 8)
        for i in range(20):
            entry_id = f"distractor-{i}"
            backend.upsert_entry(_make_entry(entry_id, entry_type="note"))
            if not backend.upsert_embedding(entry_id, "test", _near_vector(i)):
                pytest.skip("backend cannot store embeddings (no vector support)")

        rows = backend.search_semantic(
            _near_vector(), kb_name="test", limit=2, entry_type="no-matches"
        )
        assert rows == []

    @pytest.mark.control(
        reason="dev's unfiltered path already ran one query with no subquery; these tests, "
        "formerly passing kb_name and so on the filtered path, now pin the unfiltered path"
    )
    def test_unfiltered_search_above_the_k_cap_does_not_raise(
        self, big_embedded_backend, monkeypatch
    ):
        """``min_similarity`` culling drives the same escalation with no filter.

        A cutoff no row can satisfy means ``limit`` is never reached, so the
        loop escalates ``k`` to its ceiling exactly as a selective filter does.
        The honest answer is an empty list, never an exception.
        """
        sql_log = (
            _spy_on_sql(big_embedded_backend, monkeypatch)
            if hasattr(big_embedded_backend, "_raw_conn")
            else None
        )
        rows = big_embedded_backend.search_semantic(_near_vector(), limit=5, min_similarity=2.0)
        assert rows == []
        if sql_log is not None:
            # Culling, not archived rows, made it short: no retry. One statement
            # per budget, k strictly growing to the cap, none with the subquery.
            knn = [(" ".join(q.split()), p[1]) for q, p in sql_log if "MATCH" in q and "k = ?" in q]
            ks = [k for _, k in knn]
            assert ks == sorted(set(ks)) and ks[-1] == 4096, ks
            assert not [q for q, _ in knn if "rowid IN" in q], "a cull must not trigger the retry"


class TestSemanticKnnEscalation:
    """Filtered recall and distance-driven KNN escalation are both observed.

    The filtered search puts the matching entry beyond the first KNN budget;
    pushing its predicate into sqlite-vec lets it find that row without
    unrelated candidates consuming the budget. A separate unfiltered query
    verifies that distance culling still escalates ``k``.
    """

    @pytest.fixture
    def haystack_backend(self, backend):
        """200 rows where only the *farthest* one matches the filter.

        The first ``k`` (``limit * 2`` = 2) cannot reach it, so a correct
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
            # ``min_similarity`` cutoff, so ordering — not culling — is what
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

    @pytest.mark.control(reason="distance-culling escalation already held on dev without this fix")
    def test_unfiltered_distance_culling_escalates(self, haystack_backend, monkeypatch):
        """Distance culling still requires a larger KNN budget.

        Selective predicates are now applied inside sqlite-vec's KNN query, so
        they can find a distant matching row in the first budget. An
        unfiltered query whose results all fail ``min_similarity`` still has to
        escalate. Count the distinct ``k`` values to pin that behavior.
        """
        sql_log = _spy_on_sql(haystack_backend, monkeypatch)
        rows = haystack_backend.search_semantic(_near_vector(), limit=1, min_similarity=2.0)
        assert rows == []
        assert not [s for s, _ in sql_log if "rowid IN" in " ".join(s.split())], (
            "an unfiltered search must not pay for the candidate subquery"
        )
        seen_k = [p[1] for s, p in sql_log if "MATCH" in s and "k = ?" in s]
        # The same k can appear twice in one round (the main query plus the
        # filter-independent size probe); what must grow is the sequence of
        # distinct budgets.
        budgets = sorted(set(seen_k))
        assert len(budgets) >= 2, f"escalation loop ran once only; k values: {seen_k}"
        assert seen_k == sorted(seen_k), f"k must never shrink: {seen_k}"

    def test_filtered_search_finds_a_far_match_in_the_first_budget(
        self, haystack_backend, monkeypatch
    ):
        """The filtered query's pinned shape: the predicate rides inside the KNN.

        Before the fix a filtered search over the haystack escalated ``k``
        (2, 8, 32, 128, ...) until the far needle came into range. With the
        candidate rowids passed into the KNN the needle is found at the first
        budget: one distinct ``k``, and every ``MATCH`` query carries the
        ``rowid IN (...)`` restriction. This replaces the filtered query-count
        test that the unfiltered rewrite displaced.
        """
        sql_log = _spy_on_sql(haystack_backend, monkeypatch)
        rows = haystack_backend.search_semantic(
            _near_vector(), kb_name="test", limit=1, entry_type="needle"
        )
        assert {r["id"] for r in rows} == {"h199"}
        knn = [(s, p) for s, p in sql_log if "MATCH" in s and "k = ?" in s]
        assert {p[1] for _, p in knn} == {knn[0][1][1]}, f"filtered search escalated: {knn}"
        assert all("rowid IN" in " ".join(s.split()) for s, _ in knn), "predicate not in the KNN"

    @pytest.mark.control(
        reason="dev's unfiltered path already ran one query with no subquery; these tests, "
        "formerly passing kb_name and so on the filtered path, now pin the unfiltered path"
    )
    def test_unfiltered_hot_path_runs_one_query(self, haystack_backend, monkeypatch):
        """No filter, ``limit`` filled on the first budget: one query, no COUNT.

        The unconditional ``SELECT COUNT(*) FROM vec_entry`` an earlier draft
        used to size the escalation ceiling is a full scan of the vector table
        on every semantic search, paid even when nothing escalates. The KNN
        result itself already says when ``k`` has covered the table: fewer rows
        back than ``k`` asked for means there are no more neighbours.
        """
        sql_log = _spy_on_sql(haystack_backend, monkeypatch)
        rows = haystack_backend.search_semantic(_near_vector(), limit=5)
        assert len(rows) == 5

        scans = [s for s, _ in sql_log if "COUNT(*) FROM vec_entry" in " ".join(s.split())]
        assert scans == [], f"unfiltered hot path scanned the whole vector table: {scans}"
        knn = [s for s, _ in sql_log if "MATCH" in s and "k = ?" in s]
        assert len(knn) == 1, f"unfiltered hot path ran {len(knn)} queries against vec_entry"
        assert "rowid IN" not in " ".join(knn[0].split()), "no filter, so no candidate subquery"


class TestArchivedExclusionRetry:
    """When the archived-exclusion retry fires, and when it must not.

    A first pass with no caller filter can come back shorter than ``limit``
    for four reasons, and the retry (the KNN run again with the archived
    exclusion inside it) can help in exactly one:

    ====================================  =====================================
    cause of the shortfall                retry
    ====================================  =====================================
    KNN exhausted (fewer rows than ``k``) must not fire: every row was seen
    ``min_similarity`` culled a live row    must not fire: farther rows fail too
    archived rows filled the k-set        fires, and finds the live rows
    a caller filter, the readable set     never short: it rides inside the KNN
    ====================================  =====================================

    A search with no caller filter and no archived shortfall runs the queries
    it ran before the retry existed: one KNN statement per budget, none with
    ``rowid IN``.

    The documented cost: the retry also fires, and cannot help, in three
    shapes, all returning the correct result at the price of one extra query
    (a full ``entry`` scan for the candidate subquery):

    * archived rows fill the k-set and every live row is beyond ``min_similarity``;
    * every live row is already in the k-set (3 live nearest, 10 archived
      farther, ``limit=6`` at cap 8; the common shape on a KB over 4,096
      embedded rows);
    * ``limit`` above the cap, archived rows in the k-set, no live rows beyond.

    An empty k-set at the cap runs the main query plus the size probe (at the
    real cap that needs ``limit >= 2048``).
    """

    @staticmethod
    def _knn(sql_log):
        return [(" ".join(s.split()), p) for s, p in sql_log if "MATCH" in s and "k = ?" in s]

    @pytest.fixture
    def capped(self, backend, monkeypatch):
        if not hasattr(backend, "_raw_conn"):
            pytest.skip("only SQLite has a hard KNN candidate cap")
        monkeypatch.setattr(sqlite_backend, "_SQLITE_VEC_MAX_K", 8)
        return backend

    def _add(self, backend, entry_id, vec, **overrides):
        backend.upsert_entry(_make_entry(entry_id, **overrides))
        if not backend.upsert_embedding(entry_id, "test", vec):
            pytest.skip("backend cannot store embeddings (no vector support)")

    @pytest.mark.control(
        reason="dev has no retry, so it never over-fires; this pins that the retry added here "
        "does not (red against the previous head of this PR, febcbca2)"
    )
    def test_exhausted_knn_does_not_retry(self, capped, monkeypatch):
        """5 live rows, ``limit=6``: the index has fewer rows than ``k``; nothing to find."""
        for i in range(5):
            self._add(capped, f"live-{i}", _near_vector(i))
        sql_log = _spy_on_sql(capped, monkeypatch)
        rows = capped.search_semantic(_near_vector(), limit=6)
        assert len(rows) == 5
        knn = self._knn(sql_log)
        assert len(knn) == 1 and "rowid IN" not in knn[0][0], knn

    @pytest.mark.control(
        reason="dev has no retry, so it never over-fires; this pins that the retry added here "
        "does not (red against the previous head of this PR, febcbca2)"
    )
    def test_exhausted_knn_with_some_archived_rows_does_not_retry(self, capped, monkeypatch):
        """Archived rows in a k-set that covers the whole index hide nothing."""
        for i in range(3):
            self._add(capped, f"live-{i}", _near_vector(i))
            self._add(capped, f"gone-{i}", _near_vector(i), lifecycle="archived")
        sql_log = _spy_on_sql(capped, monkeypatch)
        rows = capped.search_semantic(_near_vector(), limit=6)
        assert len(rows) == 3
        knn = self._knn(sql_log)
        assert len(knn) == 1 and "rowid IN" not in knn[0][0], knn

    @pytest.mark.control(
        reason="dev has no retry, so it never over-fires; this pins that the retry added here "
        "does not (red against the previous head of this PR, febcbca2)"
    )
    def test_distance_culling_does_not_retry(self, capped, monkeypatch):
        """30 live rows, ``min_similarity=2``: farther rows fail the cutoff too."""
        for i in range(30):
            self._add(capped, f"live-{i}", _near_vector(i))
        sql_log = _spy_on_sql(capped, monkeypatch)
        assert capped.search_semantic(_near_vector(), limit=2, min_similarity=2.0) == []
        assert not [q for q, _ in self._knn(sql_log) if "rowid IN" in q]

    @pytest.mark.control(
        reason="dev has no retry and the previous head did not fire with include_archived "
        "either (its condition tested `where`, empty here); a control against both"
    )
    def test_include_archived_never_retries(self, capped, monkeypatch):
        """Opting in to archived rows leaves nothing to exclude, so nothing to retry."""
        for i in range(5):
            self._add(capped, f"live-{i}", _near_vector(i))
        sql_log = _spy_on_sql(capped, monkeypatch)
        rows = capped.search_semantic(_near_vector(), limit=6, include_archived=True)
        assert len(rows) == 5
        knn = self._knn(sql_log)
        assert len(knn) == 1 and "rowid IN" not in knn[0][0], knn

    def test_archived_rows_filling_the_kset_retry_once_and_help(self, capped, monkeypatch):
        """20 archived rows on the query vector, 3 live ones farther: retried once, found."""
        for i in range(20):
            self._add(capped, f"gone-{i}", _near_vector(), lifecycle="archived")
        for i in range(3):
            far = _near_vector()
            far[i] += 0.3
            self._add(capped, f"live-{i}", far)
        sql_log = _spy_on_sql(capped, monkeypatch)
        rows = capped.search_semantic(_near_vector(), limit=2)
        assert len(rows) == 2 and all(r["id"].startswith("live-") for r in rows)
        with_subquery = [q for q, _ in self._knn(sql_log) if "rowid IN" in q]
        assert len(with_subquery) == 1, "the retry runs exactly once"

    def test_filters_combine_inside_the_knn(self, capped):
        """KB, type, tags, status and a date range at once, beyond the cap."""
        match = {
            "entry_type": "needle",
            "tags": ["t"],
            "status": "processed",
            "date": "2026-02-15",
        }
        # Each distractor sits on the query vector and fails exactly one predicate.
        misses = [
            {"entry_type": "note"},
            {"tags": ["other"]},
            {"status": "unprocessed"},
            {"date": "2026-05-01"},
        ]
        for i in range(20):
            self._add(capped, f"d-{i}", _near_vector(), **{**match, **misses[i % 4]})
        for i in range(2):
            far = _near_vector()
            far[i] += 0.3
            self._add(capped, f"hit-{i}", far, **match)
        rows = capped.search_semantic(
            _near_vector(),
            kb_name="test",
            limit=2,
            entry_type="needle",
            tags=["t"],
            status="processed",
            date_from="2026-02-01",
            date_to="2026-02-28",
        )
        assert {r["id"] for r in rows} == {"hit-0", "hit-1"}

    def test_filtered_search_escalates_under_similarity_culling(self, capped, monkeypatch):
        """kb_name plus a cutoff no row meets: ``rowid IN`` at each k (4, then 8), result [].

        Restores the filtered-path coverage the unfiltered rewrite of the old
        tests displaced. Red on dev, which ran no ``rowid IN`` and a different
        ``k`` sequence (limit * 3).
        """
        for i in range(30):
            self._add(capped, f"live-{i}", _near_vector(i))
        sql_log = _spy_on_sql(capped, monkeypatch)
        rows = capped.search_semantic(_near_vector(), kb_name="test", limit=2, min_similarity=2.0)
        assert rows == []
        knn = self._knn(sql_log)
        assert [p[1] for _, p in knn] == [4, 8], knn
        assert all("rowid IN" in q for q, _ in knn), knn
