"""Tests for SearchService cleanup and KBService additions."""

from unittest.mock import MagicMock

from pyrite.services.kb_service import KBService
from pyrite.services.search_service import SearchService
from pyrite.services.access_policy import UNSCOPED


class TestSearchServiceRemovedMethods:
    """Verify removed methods no longer exist on SearchService."""

    def test_no_get_timeline(self):
        assert not hasattr(SearchService, "get_timeline")

    def test_no_get_tags(self):
        assert not hasattr(SearchService, "get_tags")

    def test_no_get_most_linked(self):
        assert not hasattr(SearchService, "get_most_linked")

    def test_no_get_orphans(self):
        assert not hasattr(SearchService, "get_orphans")


class TestKBServiceGetMostLinked:
    """Test KBService.get_most_linked delegates to db."""

    def test_get_most_linked_default(self):
        db = MagicMock()
        db.get_most_linked.return_value = [{"id": "a", "link_count": 5}]
        svc = KBService.__new__(KBService)
        svc.db = db

        result = svc.get_most_linked("my-kb")

        db.get_most_linked.assert_called_once_with("my-kb", 20)
        assert result == [{"id": "a", "link_count": 5}]

    def test_get_most_linked_custom_limit(self):
        db = MagicMock()
        db.get_most_linked.return_value = []
        svc = KBService.__new__(KBService)
        svc.db = db

        result = svc.get_most_linked("kb", limit=5)

        db.get_most_linked.assert_called_once_with("kb", 5)
        assert result == []

    def test_get_most_linked_no_kb(self):
        db = MagicMock()
        db.get_most_linked.return_value = []
        svc = KBService.__new__(KBService)
        svc.db = db

        svc.get_most_linked()

        db.get_most_linked.assert_called_once_with(None, 20)


class TestKBServiceGetOrphans:
    """Test KBService.get_orphans delegates to db."""

    def test_get_orphans(self):
        db = MagicMock()
        db.get_orphans.return_value = [{"id": "orphan1"}]
        svc = KBService.__new__(KBService)
        svc.db = db

        result = svc.get_orphans("my-kb", readable_kbs=UNSCOPED)

        db.get_orphans.assert_called_once_with("my-kb", readable_kbs=UNSCOPED)
        assert result == [{"id": "orphan1"}]

    def test_get_orphans_no_kb(self):
        db = MagicMock()
        db.get_orphans.return_value = []
        svc = KBService.__new__(KBService)
        svc.db = db

        result = svc.get_orphans(readable_kbs=UNSCOPED)

        db.get_orphans.assert_called_once_with(None, readable_kbs=UNSCOPED)
        assert result == []
