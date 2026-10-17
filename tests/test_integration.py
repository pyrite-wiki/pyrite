"""
Integration Tests

End-to-end tests across the full stack:
File → Repository → Index → Database → Search → API/CLI
"""

import tempfile
from pathlib import Path

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.exceptions import ValidationError
from pyrite.services import KBService, SearchService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager


@pytest.fixture
def integration_env():
    """Create a complete integration test environment."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create KB directories
        timeline_path = tmpdir / "timeline"
        timeline_path.mkdir()
        (timeline_path / "events").mkdir()

        research_path = tmpdir / "research"
        research_path.mkdir()
        (research_path / "people").mkdir()
        (research_path / "organizations").mkdir()

        # Create config
        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(
                    name="test-timeline",
                    path=timeline_path,
                    kb_type=KBType.EVENTS,
                    description="Test timeline",
                ),
                KBConfig(
                    name="test-research",
                    path=research_path,
                    kb_type=KBType.RESEARCH,
                    description="Test research KB",
                ),
            ],
            settings=Settings(index_path=tmpdir / "index.db"),
        )

        # Create database
        db = PyriteDB(config.settings.index_path)

        # Create services
        kb_service = KBService(config, db)
        search_service = SearchService(db)
        index_mgr = IndexManager(db, config)

        yield {
            "tmpdir": tmpdir,
            "config": config,
            "db": db,
            "kb_service": kb_service,
            "search_service": search_service,
            "index_mgr": index_mgr,
            "timeline_path": timeline_path,
            "research_path": research_path,
        }

        db.close()


class TestFileToSearchFlow:
    """Test the complete flow from file creation to search results."""

    def test_create_event_and_search(self, integration_env):
        """Create an event file, index it, and find it via search."""
        env = integration_env

        # 1. Create an event entry via service
        entry = env["kb_service"].create_entry(
            kb_name="test-timeline",
            entry_id="test-event-001",
            title="Important Test Event",
            entry_type="event",
            body="This is a test event with searchable content about democracy.",
            date="2024-01-15",
            importance=4,
            tags=["test", "democracy"],
            participants=["Test Person"],
        )

        assert entry.id == "test-event-001"

        # 2. Verify file was created (events go to events/ subdir)
        file_path = env["timeline_path"] / "events" / "test-event-001.md"
        assert file_path.exists()

        # 3. Search for it
        results = env["search_service"].search("democracy")
        assert len(results) == 1
        assert results[0]["id"] == "test-event-001"
        assert results[0]["title"] == "Important Test Event"

    def test_create_actor_and_search(self, integration_env):
        """Create an actor entry and find it via search."""
        env = integration_env

        # Create actor
        entry = env["kb_service"].create_entry(
            kb_name="test-research",
            entry_id="doe-john",
            title="John Doe",
            entry_type="actor",
            body="John Doe is a test actor involved in various activities.",
            tags=["test-actor"],
            role="operative",
        )

        # Search by name
        results = env["search_service"].search("John Doe")
        assert len(results) == 1
        assert results[0]["title"] == "John Doe"

        # Search by body content
        results = env["search_service"].search("activities")
        assert len(results) == 1

    def test_cross_kb_search(self, integration_env):
        """Search across multiple KBs."""
        env = integration_env

        # Create event
        env["kb_service"].create_entry(
            kb_name="test-timeline",
            entry_id="cross-kb-event",
            title="Cross KB Event",
            entry_type="event",
            body="This event involves the crossover topic.",
            date="2024-02-01",
        )

        # Create research entry
        env["kb_service"].create_entry(
            kb_name="test-research",
            entry_id="cross-kb-actor",
            title="Cross KB Actor",
            entry_type="actor",
            body="This actor is related to the crossover topic.",
        )

        # Search across all KBs
        results = env["search_service"].search("crossover")
        assert len(results) == 2

        # Search specific KB
        results = env["search_service"].search("crossover", kb_name="test-timeline")
        assert len(results) == 1
        assert results[0]["kb_name"] == "test-timeline"


class TestUpdateAndDeleteFlow:
    """Test update and delete operations across the stack."""

    def test_update_entry_reindexes(self, integration_env):
        """Updating an entry should update the search index."""
        env = integration_env

        # Create entry
        env["kb_service"].create_entry(
            kb_name="test-timeline",
            entry_id="update-test",
            title="Original Title",
            entry_type="event",
            body="Original content about apples.",
            date="2024-01-01",
        )

        # Verify original is searchable
        results = env["search_service"].search("apples")
        assert len(results) == 1

        # Update entry
        env["kb_service"].update_entry(
            entry_id="update-test",
            kb_name="test-timeline",
            body="Updated content about oranges.",
        )

        # Original term should still match (title unchanged)
        # But new content should be searchable
        results = env["search_service"].search("oranges")
        assert len(results) == 1
        assert results[0]["id"] == "update-test"

    def test_update_entry_merges_metadata(self, integration_env):
        """Updating metadata should merge shallowly, not clobber other keys."""
        env = integration_env

        env["kb_service"].create_entry(
            kb_name="test-timeline",
            entry_id="meta-merge-test",
            title="Meta Merge",
            entry_type="event",
            body="Body.",
            date="2024-01-01",
        )

        # Seed one metadata key.
        env["kb_service"].update_entry(
            entry_id="meta-merge-test",
            kb_name="test-timeline",
            metadata={"existing_key": "keep-me"},
        )

        # A partial update with a different key must not drop existing_key.
        updated = env["kb_service"].update_entry(
            entry_id="meta-merge-test",
            kb_name="test-timeline",
            metadata={"review_comments": [{"id": "c1", "note": "hi"}]},
        )

        assert updated.metadata["existing_key"] == "keep-me"
        assert updated.metadata["review_comments"][0]["id"] == "c1"

        # Reload from disk to confirm the merge persisted to frontmatter.
        from pyrite.storage.repository import KBRepository

        repo = KBRepository(env["config"].get_kb("test-timeline"))
        reloaded = repo.load("meta-merge-test")
        assert reloaded.metadata["existing_key"] == "keep-me"
        assert reloaded.metadata["review_comments"][0]["id"] == "c1"

    def test_delete_entry_removes_from_index(self, integration_env):
        """Deleting an entry should remove it from search."""
        env = integration_env

        # Create entry
        env["kb_service"].create_entry(
            kb_name="test-timeline",
            entry_id="delete-test",
            title="Entry To Delete",
            entry_type="event",
            body="This entry will be deleted.",
            date="2024-01-01",
        )

        # Verify searchable
        results = env["search_service"].search("deleted")
        assert len(results) == 1

        # Delete
        deleted = env["kb_service"].delete_entry("delete-test", "test-timeline")
        assert deleted is True

        # Verify not searchable
        results = env["search_service"].search("deleted")
        assert len(results) == 0

        # Verify file is gone
        file_path = env["timeline_path"] / "delete-test.md"
        assert not file_path.exists()


class TestTagAndActorFlow:
    """Test tag and actor indexing across the stack."""

    def test_tag_filtering(self, integration_env):
        """Tags should be indexed and filterable."""
        env = integration_env

        # Create entries with different tags
        env["kb_service"].create_entry(
            kb_name="test-timeline",
            entry_id="tag-test-1",
            title="Tagged Event 1",
            entry_type="event",
            body="First tagged event.",
            date="2024-01-01",
            tags=["alpha", "beta"],
        )

        env["kb_service"].create_entry(
            kb_name="test-timeline",
            entry_id="tag-test-2",
            title="Tagged Event 2",
            entry_type="event",
            body="Second tagged event.",
            date="2024-01-02",
            tags=["beta", "gamma"],
        )

        # Get all tags
        tags = env["kb_service"].get_tags()
        tag_names = {t["name"] for t in tags}
        assert "alpha" in tag_names
        assert "beta" in tag_names
        assert "gamma" in tag_names

        # Beta should have count of 2
        beta_tag = next(t for t in tags if t["name"] == "beta")
        assert beta_tag["count"] == 2

    def test_actor_indexing(self, integration_env):
        """Actors should be indexed and searchable."""
        env = integration_env

        # Create events with actors
        env["kb_service"].create_entry(
            kb_name="test-timeline",
            entry_id="actor-test-1",
            title="Event with Actors",
            entry_type="event",
            body="Event involving multiple actors.",
            date="2024-01-01",
            participants=["Alice Smith", "Bob Jones"],
        )

        env["kb_service"].create_entry(
            kb_name="test-timeline",
            entry_id="actor-test-2",
            title="Another Event",
            entry_type="event",
            body="Another event.",
            date="2024-01-02",
            participants=["Alice Smith"],
        )

        # Verify events were created and searchable
        results = env["search_service"].search("Actors")
        assert len(results) >= 1

        results = env["search_service"].search("Another Event")
        assert len(results) >= 1


class TestTimelineFlow:
    """Test timeline-specific functionality."""

    def test_timeline_date_filtering(self, integration_env):
        """Timeline should filter by date range."""
        env = integration_env

        # Create events on different dates
        for i, date in enumerate(["2024-01-01", "2024-02-15", "2024-03-30"]):
            env["kb_service"].create_entry(
                kb_name="test-timeline",
                entry_id=f"timeline-{i}",
                title=f"Event on {date}",
                entry_type="event",
                body="Timeline test event.",
                date=date,
                importance=3,
            )

        # Get all events
        events = env["kb_service"].get_timeline()
        assert len(events) == 3

        # Filter by date range
        events = env["kb_service"].get_timeline(
            date_from="2024-02-01",
            date_to="2024-02-28",
        )
        assert len(events) == 1
        assert events[0]["date"] == "2024-02-15"

    def test_timeline_importance_filtering(self, integration_env):
        """Timeline should filter by importance."""
        env = integration_env

        # Create events with different importance
        for i, importance in enumerate([1, 3, 5]):
            env["kb_service"].create_entry(
                kb_name="test-timeline",
                entry_id=f"importance-{i}",
                title=f"Importance {importance} Event",
                entry_type="event",
                body="Importance test.",
                date="2024-01-01",
                importance=importance,
            )

        # Get high importance only
        events = env["kb_service"].get_timeline(min_importance=4)
        assert len(events) == 1
        assert events[0]["importance"] == 5


class TestIndexSyncFlow:
    """Test index synchronization."""

    def test_manual_file_creation_syncs(self, integration_env):
        """Manually created files should be picked up by sync."""
        env = integration_env

        # Manually create a file (simulating external edit)
        file_path = env["timeline_path"] / "manual-entry.md"
        file_path.write_text("""---
id: manual-entry
title: Manually Created Event
date: "2024-01-15"
importance: 3
tags:
  - manual
---

This event was created manually outside the API.
""")

        # Before sync, not searchable
        results = env["search_service"].search("manually")
        assert len(results) == 0

        # Sync
        env["index_mgr"].sync_incremental("test-timeline")

        # After sync, searchable
        results = env["search_service"].search("manually")
        assert len(results) == 1
        assert results[0]["id"] == "manual-entry"

    def test_deleted_file_syncs(self, integration_env):
        """Deleted files should be removed from index on sync."""
        env = integration_env

        # Create via service
        env["kb_service"].create_entry(
            kb_name="test-timeline",
            entry_id="will-delete",
            title="Will Be Deleted",
            entry_type="event",
            body="This file will be deleted externally.",
            date="2024-01-01",
        )

        # Verify searchable
        results = env["search_service"].search("externally")
        assert len(results) == 1

        # Manually delete the file (simulating external deletion)
        file_path = env["timeline_path"] / "events" / "will-delete.md"
        file_path.unlink()

        # Sync
        sync_result = env["index_mgr"].sync_incremental("test-timeline")
        assert sync_result["removed"] == 1

        # No longer searchable
        results = env["search_service"].search("externally")
        assert len(results) == 0


class TestFTS5EdgeCases:
    """Test FTS5 search edge cases."""

    def test_hyphenated_search(self, integration_env):
        """Hyphenated terms should work correctly."""
        env = integration_env

        env["kb_service"].create_entry(
            kb_name="test-research",
            entry_id="alex-jones",
            title="Alex Jones",
            entry_type="actor",
            body="Alex Jones is a well-known figure.",
        )

        # Search with hyphen (the service should sanitize this)
        results = env["search_service"].search("alex-jones")
        assert len(results) == 1

    def test_special_characters_search(self, integration_env):
        """Special characters should not break search."""
        env = integration_env

        env["kb_service"].create_entry(
            kb_name="test-research",
            entry_id="special-chars",
            title="Test & Special (Characters)",
            entry_type="actor",
            body="Contains special: @#$% characters.",
        )

        # Search should handle this gracefully
        results = env["search_service"].search("special")
        assert len(results) == 1

    def test_empty_query(self, integration_env):
        """Empty query raises the classified QuerySyntaxError, not a raw
        sqlite3.OperationalError — search-query-syntax-error-contract
        reclassifies FTS5 syntax failures so CLI/MCP/REST can surface a
        stable QUERY_SYNTAX code instead of leaking the backend exception."""
        from pyrite.exceptions import QuerySyntaxError

        env = integration_env

        with pytest.raises(QuerySyntaxError):
            env["search_service"].search("")

    def test_unicode_search(self, integration_env):
        """Unicode content can be indexed and searched."""
        env = integration_env

        env["kb_service"].create_entry(
            kb_name="test-research",
            entry_id="unicode-entry",
            title="Política económica",
            entry_type="theme",
            body="La política económica afecta a todos los ciudadanos.",
        )

        results = env["search_service"].search("política")
        assert len(results) == 1
        assert results[0]["id"] == "unicode-entry"

    def test_no_results_returns_empty_list(self, integration_env):
        """Query with no matches returns empty list, not None or error."""
        env = integration_env

        results = env["search_service"].search("xyznonexistent99")
        assert results == []

    def test_search_with_invalid_date_range(self, integration_env):
        """Search with reversed date range returns no results."""
        env = integration_env

        env["kb_service"].create_entry(
            kb_name="test-research",
            entry_id="dated-entry",
            title="Dated Event",
            entry_type="event",
            body="An event that happened.",
            date="2025-06-15",
        )

        results = env["search_service"].search(
            "Dated", date_from="2025-12-31", date_to="2020-01-01"
        )
        assert results == []

    def test_search_invalid_mode_is_refused(self, integration_env):
        """Invalid mode string is refused, not silently run as keyword (#917)."""
        env = integration_env

        env["kb_service"].create_entry(
            kb_name="test-research",
            entry_id="mode-test",
            title="Mode Test Entry",
            entry_type="theme",
            body="Testing mode validation.",
        )

        with pytest.raises(ValidationError):
            env["search_service"].search("Mode", mode="nonexistent")


class TestMigrationIntegration:
    """Test that migrations work correctly with real data."""

    def test_new_db_gets_migrated(self, integration_env):
        """New database should be automatically migrated."""
        env = integration_env

        # Check migration status
        status = env["db"].get_migration_status()

        assert status["up_to_date"] is True
        assert status["current_version"] >= 1
        assert len(status["pending"]) == 0

    def test_schema_version_persists(self, integration_env):
        """Schema version should persist across connections."""
        env = integration_env

        # Get version
        version1 = env["db"].get_schema_version()

        # Close and reopen
        db_path = env["config"].settings.index_path
        env["db"].close()

        db2 = PyriteDB(db_path)
        version2 = db2.get_schema_version()
        db2.close()

        assert version1 == version2
