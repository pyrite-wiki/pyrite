"""Tests for GraphService edge-entity methods."""

import pytest
from unittest.mock import MagicMock

from pyrite.services.graph_service import GraphService
from pyrite.services.access_policy import UNSCOPED


@pytest.fixture
def graph_service():
    db = MagicMock()
    return GraphService(db)


class TestGraphServiceEdgeMethods:
    def test_get_edge_endpoints_delegates(self, graph_service):
        graph_service.db.get_edge_endpoints.return_value = [
            {"role": "source", "endpoint_id": "person-1"}
        ]
        result = graph_service.get_edge_endpoints("own-1", "test")
        assert len(result) == 1
        graph_service.db.get_edge_endpoints.assert_called_once_with("own-1", "test")

    def test_get_edges_by_endpoint_delegates(self, graph_service):
        graph_service.db.get_edges_by_endpoint.return_value = []
        result = graph_service.get_edges_by_endpoint("person-1", "test")
        assert result == []

    def test_get_edges_between_delegates(self, graph_service):
        graph_service.db.get_edges_between.return_value = []
        result = graph_service.get_edges_between("a", "b", "test")
        assert result == []


class TestMergedBacklinks:
    def test_merged_backlinks_combines_links_and_edges(self, graph_service):
        graph_service.db.get_backlinks.return_value = [
            {
                "id": "note-1",
                "kb_name": "test",
                "title": "Note 1",
                "entry_type": "note",
                "relation": "related_to",
            }
        ]
        graph_service.db.get_edges_by_endpoint.return_value = [
            {
                "id": "own-1",
                "kb_name": "test",
                "title": "Ownership",
                "entry_type": "ownership",
                "edge_type": "ownership",
                "role": "target",
            }
        ]
        result = graph_service.get_merged_backlinks("company-1", "test", readable_kbs=UNSCOPED)
        assert len(result) == 2
        assert result[0]["source_type"] == "link"
        assert result[1]["source_type"] == "edge"

    def test_merged_backlinks_deduplicates(self, graph_service):
        # Same entry appears in both links and edges
        graph_service.db.get_backlinks.return_value = [
            {
                "id": "own-1",
                "kb_name": "test",
                "title": "Ownership",
                "entry_type": "ownership",
                "relation": "owns",
            }
        ]
        graph_service.db.get_edges_by_endpoint.return_value = [
            {
                "id": "own-1",
                "kb_name": "test",
                "title": "Ownership",
                "entry_type": "ownership",
                "edge_type": "ownership",
                "role": "target",
            }
        ]
        result = graph_service.get_merged_backlinks("company-1", "test", readable_kbs=UNSCOPED)
        assert len(result) == 1  # Deduplicated
        assert result[0]["source_type"] == "link"  # Link version preferred

    def test_merged_backlinks_with_limit(self, graph_service):
        graph_service.db.get_backlinks.return_value = [
            {
                "id": f"note-{i}",
                "kb_name": "test",
                "title": f"Note {i}",
                "entry_type": "note",
                "relation": "related_to",
            }
            for i in range(5)
        ]
        graph_service.db.get_edges_by_endpoint.return_value = []
        result = graph_service.get_merged_backlinks(
            "company-1", "test", limit=3, readable_kbs=UNSCOPED
        )
        assert len(result) == 3

    def test_merged_backlinks_edge_dedup_multiple_endpoints(self, graph_service):
        # An edge entry can appear multiple times in get_edges_by_endpoint if
        # the queried entity is referenced by multiple roles (unusual but possible)
        graph_service.db.get_backlinks.return_value = []
        graph_service.db.get_edges_by_endpoint.return_value = [
            {
                "id": "edge-1",
                "kb_name": "test",
                "title": "Edge",
                "entry_type": "funding",
                "edge_type": "funding",
                "role": "source",
            },
            {
                "id": "edge-1",
                "kb_name": "test",
                "title": "Edge",
                "entry_type": "funding",
                "edge_type": "funding",
                "role": "target",
            },
        ]
        result = graph_service.get_merged_backlinks("entity-1", "test", readable_kbs=UNSCOPED)
        assert len(result) == 1  # Same edge deduplicated

    def test_merged_backlinks_empty(self, graph_service):
        graph_service.db.get_backlinks.return_value = []
        graph_service.db.get_edges_by_endpoint.return_value = []
        result = graph_service.get_merged_backlinks("entity-1", "test", readable_kbs=UNSCOPED)
        assert result == []
