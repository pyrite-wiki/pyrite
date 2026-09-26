"""Tests for GraphService."""

from unittest.mock import MagicMock

import pytest

from pyrite.services.graph_service import GraphService


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def graph_svc(mock_db):
    return GraphService(mock_db)


class TestGetGraph:
    def test_delegates_to_db(self, graph_svc, mock_db):
        mock_db.get_graph_data.return_value = {"nodes": [], "edges": []}
        result = graph_svc.get_graph(center="abc", kb_name="test")
        mock_db.get_graph_data.assert_called_once_with(
            center="abc",
            center_kb=None,
            kb_name="test",
            entry_type=None,
            depth=2,
            limit=500,
            readable_kbs=None,
        )
        assert result == {"nodes": [], "edges": []}

    def test_passes_all_params(self, graph_svc, mock_db):
        mock_db.get_graph_data.return_value = {}
        graph_svc.get_graph(
            center="x",
            center_kb="ck",
            kb_name="kb",
            entry_type="note",
            depth=3,
            limit=100,
            readable_kbs={"kb"},
        )
        mock_db.get_graph_data.assert_called_once_with(
            center="x",
            center_kb="ck",
            kb_name="kb",
            entry_type="note",
            depth=3,
            limit=100,
            readable_kbs={"kb"},
        )


class TestGetBacklinks:
    def test_delegates_to_db(self, graph_svc, mock_db):
        mock_db.get_backlinks.return_value = [{"id": "linked"}]
        result = graph_svc.get_backlinks("entry1", "mykb")
        mock_db.get_backlinks.assert_called_once_with(
            "entry1", "mykb", limit=0, offset=0, readable_kbs=None
        )
        assert result == [{"id": "linked"}]

    def test_with_limit_offset(self, graph_svc, mock_db):
        mock_db.get_backlinks.return_value = []
        graph_svc.get_backlinks("e1", "kb", limit=10, offset=5)
        mock_db.get_backlinks.assert_called_once_with(
            "e1", "kb", limit=10, offset=5, readable_kbs=None
        )


class TestGetOutlinks:
    def test_delegates_to_db(self, graph_svc, mock_db):
        mock_db.get_outlinks.return_value = [{"id": "target"}]
        result = graph_svc.get_outlinks("entry1", "mykb")
        mock_db.get_outlinks.assert_called_once_with("entry1", "mykb", readable_kbs=None)
        assert result == [{"id": "target"}]


class TestGetRefsTo:
    def test_delegates_to_db(self, graph_svc, mock_db):
        mock_db.get_refs_to.return_value = [{"id": "ref1"}]
        result = graph_svc.get_refs_to("entry1", "mykb")
        mock_db.get_refs_to.assert_called_once_with("entry1", "mykb")
        assert result == [{"id": "ref1"}]


class TestGetRefsFrom:
    def test_delegates_to_db(self, graph_svc, mock_db):
        mock_db.get_refs_from.return_value = [{"id": "ref2"}]
        result = graph_svc.get_refs_from("entry1", "mykb")
        mock_db.get_refs_from.assert_called_once_with("entry1", "mykb")
        assert result == [{"id": "ref2"}]
