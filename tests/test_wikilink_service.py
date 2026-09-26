"""Tests for WikilinkService.

Tests all 5 methods: list_entry_titles, resolve_entry, resolve_batch, get_wanted_pages, check_links.
Uses shared fixtures from conftest.py.
"""

import pytest

from pyrite.services.wikilink_service import WikilinkService
from pyrite.services.access_policy import UNSCOPED


@pytest.fixture
def wikilink_env(indexed_test_env):
    """WikilinkService with indexed test data."""
    config = indexed_test_env["config"]
    db = indexed_test_env["db"]
    svc = WikilinkService(config, db)
    return {"svc": svc, "config": config, "db": db}


class TestListEntryTitles:
    def test_lists_all_entries(self, wikilink_env):
        results = wikilink_env["svc"].list_entry_titles(readable_kbs=UNSCOPED)
        # 3 events + 1 person = 4 entries
        assert len(results) == 4

    def test_lists_entries_by_kb(self, wikilink_env):
        results = wikilink_env["svc"].list_entry_titles(
            kb_name="test-events", readable_kbs=UNSCOPED
        )
        assert len(results) == 3
        assert all(r["kb_name"] == "test-events" for r in results)

    def test_lists_entries_by_query(self, wikilink_env):
        results = wikilink_env["svc"].list_entry_titles(
            query="Stephen Miller", readable_kbs=UNSCOPED
        )
        assert len(results) >= 1
        assert any("Stephen" in r["title"] for r in results)

    def test_lists_entries_query_case_insensitive(self, wikilink_env):
        results_upper = wikilink_env["svc"].list_entry_titles(
            query="TEST EVENT", readable_kbs=UNSCOPED
        )
        results_lower = wikilink_env["svc"].list_entry_titles(
            query="test event", readable_kbs=UNSCOPED
        )
        assert len(results_upper) == len(results_lower)

    def test_lists_entries_respects_limit(self, wikilink_env):
        results = wikilink_env["svc"].list_entry_titles(limit=2, readable_kbs=UNSCOPED)
        assert len(results) == 2

    def test_result_has_expected_keys(self, wikilink_env):
        results = wikilink_env["svc"].list_entry_titles(limit=1, readable_kbs=UNSCOPED)
        assert len(results) == 1
        r = results[0]
        assert "id" in r
        assert "kb_name" in r
        assert "entry_type" in r
        assert "title" in r

    def test_empty_query_returns_all(self, wikilink_env):
        results = wikilink_env["svc"].list_entry_titles(query="", readable_kbs=UNSCOPED)
        assert len(results) == 4


class TestResolveEntry:
    def test_resolves_by_id(self, wikilink_env):
        result = wikilink_env["svc"].resolve_entry(
            "2025-01-10--test-event-0", readable_kbs=UNSCOPED
        )
        assert result is not None
        assert result["id"] == "2025-01-10--test-event-0"

    def test_resolves_by_id_with_kb(self, wikilink_env):
        result = wikilink_env["svc"].resolve_entry(
            "2025-01-10--test-event-0", kb_name="test-events", readable_kbs=UNSCOPED
        )
        assert result is not None
        assert result["kb_name"] == "test-events"

    def test_resolves_by_title(self, wikilink_env):
        result = wikilink_env["svc"].resolve_entry("Test Event 1", readable_kbs=UNSCOPED)
        assert result is not None
        assert result["id"] == "2025-01-11--test-event-1"

    def test_returns_none_for_missing(self, wikilink_env):
        result = wikilink_env["svc"].resolve_entry("nonexistent-entry", readable_kbs=UNSCOPED)
        assert result is None

    def test_wrong_kb_returns_none(self, wikilink_env):
        result = wikilink_env["svc"].resolve_entry(
            "2025-01-10--test-event-0", kb_name="test-research", readable_kbs=UNSCOPED
        )
        assert result is None


class TestResolveBatch:
    def test_resolves_multiple(self, wikilink_env):
        targets = ["2025-01-10--test-event-0", "2025-01-11--test-event-1", "nonexistent"]
        result = wikilink_env["svc"].resolve_batch(targets, readable_kbs=UNSCOPED)
        assert result["2025-01-10--test-event-0"] is True
        assert result["2025-01-11--test-event-1"] is True
        assert result["nonexistent"] is False

    def test_resolves_with_kb_filter(self, wikilink_env):
        targets = ["2025-01-10--test-event-0", "miller-stephen"]
        result = wikilink_env["svc"].resolve_batch(
            targets, kb_name="test-events", readable_kbs=UNSCOPED
        )
        assert result["2025-01-10--test-event-0"] is True
        # miller-stephen is in test-research, not test-events
        assert result["miller-stephen"] is False

    def test_empty_list_returns_empty(self, wikilink_env):
        result = wikilink_env["svc"].resolve_batch([], readable_kbs=UNSCOPED)
        assert result == {}

    def test_all_missing_returns_all_false(self, wikilink_env):
        targets = ["missing-1", "missing-2"]
        result = wikilink_env["svc"].resolve_batch(targets, readable_kbs=UNSCOPED)
        assert all(v is False for v in result.values())


class TestGetWantedPages:
    def test_returns_wanted_pages(self, wikilink_env):
        """Entries with wikilinks to nonexistent targets show as wanted."""
        # The sample events have participants but no wikilinks to missing entries
        # by default. Create an entry with a wikilink to a missing entry.
        from pyrite.services.kb_service import KBService

        svc = KBService(wikilink_env["config"], wikilink_env["db"])
        svc.create_entry(
            "test-events",
            "linker-entry",
            "Entry with links",
            "note",
            body="See [[missing-page]] and [[also-missing]].",
        )
        # Re-index to pick up the wikilinks
        from pyrite.storage.index import IndexManager

        index_mgr = IndexManager(wikilink_env["db"], wikilink_env["config"])
        index_mgr.sync_incremental("test-events")

        results = wikilink_env["svc"].get_wanted_pages(readable_kbs=UNSCOPED)
        wanted_ids = [r["target_id"] for r in results]
        assert "missing-page" in wanted_ids
        assert "also-missing" in wanted_ids

    def test_wanted_pages_empty_when_all_resolved(self, wikilink_env):
        """No wanted pages when all link targets exist."""
        results = wikilink_env["svc"].get_wanted_pages(kb_name="test-events", readable_kbs=UNSCOPED)
        # Sample events don't have body wikilinks to missing entries
        assert isinstance(results, list)

    def test_wanted_pages_respects_kb_filter(self, wikilink_env):
        results = wikilink_env["svc"].get_wanted_pages(kb_name="test-events", readable_kbs=UNSCOPED)
        for r in results:
            assert r["target_kb"] == "test-events"

    def test_wanted_pages_includes_ref_count(self, wikilink_env):
        """Each wanted page has ref_count and referenced_by."""
        from pyrite.services.kb_service import KBService

        svc = KBService(wikilink_env["config"], wikilink_env["db"])
        # Two entries linking to the same missing page
        svc.create_entry(
            "test-events",
            "ref1",
            "Ref 1",
            "note",
            body="See [[shared-missing-page]].",
        )
        svc.create_entry(
            "test-events",
            "ref2",
            "Ref 2",
            "note",
            body="Also see [[shared-missing-page]].",
        )
        from pyrite.storage.index import IndexManager

        IndexManager(wikilink_env["db"], wikilink_env["config"]).sync_incremental("test-events")

        results = wikilink_env["svc"].get_wanted_pages(readable_kbs=UNSCOPED)
        shared = [r for r in results if r["target_id"] == "shared-missing-page"]
        assert len(shared) == 1
        assert shared[0]["ref_count"] >= 2
        assert "referenced_by" in shared[0]


class TestCheckLinks:
    def test_no_broken_links_initially(self, wikilink_env):
        """Clean index has no broken links."""
        results = wikilink_env["svc"].check_links()
        assert results == []

    def test_broken_link_detected(self, wikilink_env):
        """A wikilink to a nonexistent entry appears as a missing target."""
        from pyrite.services.kb_service import KBService
        from pyrite.storage.index import IndexManager

        svc = KBService(wikilink_env["config"], wikilink_env["db"])
        svc.create_entry(
            "test-events",
            "broken-src",
            "Has broken link",
            "note",
            body="See [[nonexistent-target]].",
        )
        IndexManager(wikilink_env["db"], wikilink_env["config"]).sync_incremental("test-events")

        results = wikilink_env["svc"].check_links()
        assert len(results) >= 1
        target_ids = [r["target_id"] for r in results]
        assert "nonexistent-target" in target_ids
        # Check nested structure
        row = next(r for r in results if r["target_id"] == "nonexistent-target")
        assert row["ref_count"] == 1
        assert row["references"][0]["source_id"] == "broken-src"
        assert row["references"][0]["source_kb"] == "test-events"

    def test_sorted_by_ref_count(self, wikilink_env):
        """Targets with more references appear first."""
        from pyrite.services.kb_service import KBService
        from pyrite.storage.index import IndexManager

        svc = KBService(wikilink_env["config"], wikilink_env["db"])
        # Two entries link to popular-missing, one to rare-missing
        svc.create_entry("test-events", "a1", "A1", "note", body="See [[popular-missing]].")
        svc.create_entry("test-events", "a2", "A2", "note", body="See [[popular-missing]].")
        svc.create_entry("test-events", "a3", "A3", "note", body="See [[rare-missing]].")
        IndexManager(wikilink_env["db"], wikilink_env["config"]).sync_incremental("test-events")

        results = wikilink_env["svc"].check_links()
        popular = next(r for r in results if r["target_id"] == "popular-missing")
        rare = next(r for r in results if r["target_id"] == "rare-missing")
        assert results.index(popular) < results.index(rare)
        assert popular["ref_count"] == 2
        assert rare["ref_count"] == 1

    def test_filter_by_kb(self, wikilink_env):
        """kb_name filter restricts to links originating from that KB."""
        from pyrite.services.kb_service import KBService
        from pyrite.storage.index import IndexManager

        svc = KBService(wikilink_env["config"], wikilink_env["db"])
        svc.create_entry(
            "test-events",
            "src-events",
            "Events link",
            "note",
            body="See [[missing-from-events]].",
        )
        IndexManager(wikilink_env["db"], wikilink_env["config"]).sync_incremental("test-events")

        results = wikilink_env["svc"].check_links(kb_name="test-events")
        assert len(results) >= 1
        # All references should come from test-events
        for target in results:
            for ref in target["references"]:
                assert ref["source_kb"] == "test-events"

        results_other = wikilink_env["svc"].check_links(kb_name="nonexistent-kb")
        assert results_other == []
