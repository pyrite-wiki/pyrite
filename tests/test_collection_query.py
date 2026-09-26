"""Tests for Collections Phase 2 — virtual collections with query DSL."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrite.services.collection_query import (
    CACHE_TTL,
    CollectionQuery,
    _cache_key,
    _post_filter,
    clear_cache,
    evaluate_query,
    evaluate_query_cached,
    parse_query,
    query_from_dict,
    validate_query,
)
from pyrite.services.access_policy import UNSCOPED


# =============================================================================
# Query Parsing
# =============================================================================


class TestQueryParsing:
    """Test parse_query with various inline query strings."""

    def test_parse_empty_string(self):
        q = parse_query("")
        assert q.entry_type is None
        assert q.tags_any is None
        assert q.status is None

    def test_parse_whitespace_only(self):
        q = parse_query("   ")
        assert q.entry_type is None

    def test_parse_type(self):
        q = parse_query("type:backlog_item")
        assert q.entry_type == "backlog_item"

    def test_parse_status(self):
        q = parse_query("status:proposed")
        assert q.status == "proposed"

    def test_parse_tags_single(self):
        q = parse_query("tags:enhancement")
        assert q.tags_any == ["enhancement"]

    def test_parse_tags_multiple(self):
        q = parse_query("tags:enhancement,core,bugfix")
        assert q.tags_any == ["enhancement", "core", "bugfix"]

    def test_parse_tags_all(self):
        q = parse_query("tags_all:core,backend")
        assert q.tags_all == ["core", "backend"]

    def test_parse_kb(self):
        q = parse_query("kb:pyrite")
        assert q.kb_name == "pyrite"

    def test_parse_date_range(self):
        q = parse_query("date_from:2025-01-01 date_to:2025-12-31")
        assert q.date_from == "2025-01-01"
        assert q.date_to == "2025-12-31"

    def test_parse_sort_asc(self):
        q = parse_query("sort:title")
        assert q.sort_by == "title"
        assert q.sort_order == "asc"

    def test_parse_sort_desc(self):
        q = parse_query("sort:-updated_at")
        assert q.sort_by == "updated_at"
        assert q.sort_order == "desc"

    def test_parse_limit(self):
        q = parse_query("limit:10")
        assert q.limit == 10

    def test_parse_offset(self):
        q = parse_query("offset:20")
        assert q.offset == 20

    def test_parse_invalid_limit(self):
        q = parse_query("limit:abc")
        assert q.limit == 200  # default

    def test_parse_combined_query(self):
        q = parse_query(
            "type:backlog_item status:proposed tags:enhancement kb:pyrite sort:-updated_at limit:25"
        )
        assert q.entry_type == "backlog_item"
        assert q.status == "proposed"
        assert q.tags_any == ["enhancement"]
        assert q.kb_name == "pyrite"
        assert q.sort_by == "updated_at"
        assert q.sort_order == "desc"
        assert q.limit == 25

    def test_parse_field_comparison_colon(self):
        q = parse_query("priority:high")
        assert q.fields == {"priority": "high"}

    def test_parse_field_comparison_equals(self):
        q = parse_query("priority=high")
        assert q.fields == {"priority": "high"}

    def test_parse_empty_value_skipped(self):
        q = parse_query("type:")
        assert q.entry_type is None


# =============================================================================
# Query from Dict
# =============================================================================


class TestQueryFromDict:
    """Test query_from_dict for building from collection metadata."""

    def test_empty_dict(self):
        q = query_from_dict({})
        assert q.entry_type is None

    def test_none_input(self):
        q = query_from_dict(None)
        assert q.entry_type is None

    def test_basic_fields(self):
        q = query_from_dict(
            {
                "entry_type": "note",
                "status": "draft",
                "kb_name": "test-kb",
            }
        )
        assert q.entry_type == "note"
        assert q.status == "draft"
        assert q.kb_name == "test-kb"

    def test_type_alias(self):
        q = query_from_dict({"type": "event"})
        assert q.entry_type == "event"

    def test_tags_list(self):
        q = query_from_dict({"tags_any": ["core", "backend"]})
        assert q.tags_any == ["core", "backend"]

    def test_tags_string(self):
        q = query_from_dict({"tags": "core,backend"})
        assert q.tags_any == ["core", "backend"]

    def test_tags_all(self):
        q = query_from_dict({"tags_all": ["core", "backend"]})
        assert q.tags_all == ["core", "backend"]

    def test_date_range(self):
        q = query_from_dict({"date_from": "2025-01-01", "date_to": "2025-12-31"})
        assert q.date_from == "2025-01-01"
        assert q.date_to == "2025-12-31"

    def test_sort_and_limit(self):
        q = query_from_dict({"sort_by": "date", "sort_order": "desc", "limit": 50})
        assert q.sort_by == "date"
        assert q.sort_order == "desc"
        assert q.limit == 50

    def test_fields_dict(self):
        q = query_from_dict({"fields": {"priority": "high", "effort": "small"}})
        assert q.fields == {"priority": "high", "effort": "small"}

    def test_kb_alias(self):
        q = query_from_dict({"kb": "my-kb"})
        assert q.kb_name == "my-kb"


# =============================================================================
# Query Validation
# =============================================================================


class TestQueryValidation:
    """Test validate_query for error detection."""

    def test_valid_query(self):
        q = CollectionQuery(entry_type="note", sort_by="title", sort_order="asc")
        errors = validate_query(q)
        assert errors == []

    def test_invalid_sort_order(self):
        q = CollectionQuery(sort_order="random")
        errors = validate_query(q)
        assert any("sort_order" in e for e in errors)

    def test_invalid_sort_by(self):
        q = CollectionQuery(sort_by="nonexistent_field")
        errors = validate_query(q)
        assert any("sort_by" in e for e in errors)

    def test_negative_limit(self):
        q = CollectionQuery(limit=-1)
        errors = validate_query(q)
        assert any("limit" in e for e in errors)

    def test_too_large_limit(self):
        q = CollectionQuery(limit=5000)
        errors = validate_query(q)
        assert any("limit" in e for e in errors)

    def test_negative_offset(self):
        q = CollectionQuery(offset=-5)
        errors = validate_query(q)
        assert any("offset" in e for e in errors)

    def test_invalid_date_from(self):
        q = CollectionQuery(date_from="not-a-date")
        errors = validate_query(q)
        assert any("date_from" in e for e in errors)

    def test_invalid_date_to(self):
        q = CollectionQuery(date_to="2025/01/01")
        errors = validate_query(q)
        assert any("date_to" in e for e in errors)

    def test_valid_dates(self):
        q = CollectionQuery(date_from="2025-01-01", date_to="2025-12-31")
        errors = validate_query(q)
        assert errors == []

    def test_multiple_errors(self):
        q = CollectionQuery(sort_order="random", limit=-1, date_from="bad")
        errors = validate_query(q)
        assert len(errors) >= 3


# =============================================================================
# Post-filtering
# =============================================================================


class TestPostFilter:
    """Test _post_filter logic."""

    @pytest.fixture
    def sample_entries(self):
        return [
            {
                "id": "e1",
                "title": "A",
                "tags": ["core", "backend"],
                "date": "2025-01-10",
                "metadata": '{"status": "proposed", "priority": "high"}',
            },
            {
                "id": "e2",
                "title": "B",
                "tags": ["frontend"],
                "date": "2025-02-15",
                "metadata": '{"status": "done", "priority": "low"}',
            },
            {
                "id": "e3",
                "title": "C",
                "tags": ["core", "frontend"],
                "date": "2025-03-20",
                "metadata": '{"status": "proposed", "priority": "medium"}',
            },
        ]

    def test_tags_any_multiple(self, sample_entries):
        q = CollectionQuery(tags_any=["backend", "frontend"])
        result = _post_filter(sample_entries, q)
        assert len(result) == 3  # all have at least one

    def test_tags_all(self, sample_entries):
        q = CollectionQuery(tags_all=["core", "frontend"])
        result = _post_filter(sample_entries, q)
        assert len(result) == 1
        assert result[0]["id"] == "e3"

    def test_status_filter(self, sample_entries):
        q = CollectionQuery(status="proposed")
        result = _post_filter(sample_entries, q)
        assert len(result) == 2

    def test_date_from(self, sample_entries):
        q = CollectionQuery(date_from="2025-02-01")
        result = _post_filter(sample_entries, q)
        assert len(result) == 2

    def test_date_to(self, sample_entries):
        q = CollectionQuery(date_to="2025-02-15")
        result = _post_filter(sample_entries, q)
        assert len(result) == 2

    def test_date_range(self, sample_entries):
        q = CollectionQuery(date_from="2025-01-15", date_to="2025-03-01")
        result = _post_filter(sample_entries, q)
        assert len(result) == 1
        assert result[0]["id"] == "e2"

    def test_field_comparison(self, sample_entries):
        q = CollectionQuery(fields={"priority": "high"})
        result = _post_filter(sample_entries, q)
        assert len(result) == 1
        assert result[0]["id"] == "e1"

    def test_no_filters(self, sample_entries):
        q = CollectionQuery()
        result = _post_filter(sample_entries, q)
        assert len(result) == 3


# =============================================================================
# Query Evaluation (integration with DB)
# =============================================================================


class TestCollectionQueryEvaluation:
    """Test evaluate_query against a real DB."""

    @pytest.fixture
    def query_env(self):
        """Create a test environment with indexed entries for query testing."""
        from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
        from pyrite.models.core_types import NoteEntry
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager
        from pyrite.storage.repository import KBRepository

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            db_path = tmpdir / "index.db"
            kb_path = tmpdir / "kb"
            kb_path.mkdir()

            kb_config = KBConfig(name="test-kb", path=kb_path, kb_type=KBType.GENERIC)
            repo = KBRepository(kb_config)

            # Create diverse entries
            for i in range(5):
                note = NoteEntry(
                    id=f"note-{i}",
                    title=f"Note {i}",
                    body=f"Content of note {i}",
                    tags=["test"] + (["important"] if i < 3 else []),
                )
                repo.save(note)

            config = PyriteConfig(
                knowledge_bases=[kb_config],
                settings=Settings(index_path=db_path),
            )
            db = PyriteDB(db_path)
            IndexManager(db, config).index_all()

            yield db
            db.close()

    def test_evaluate_basic(self, query_env):
        q = CollectionQuery(kb_name="test-kb")
        entries, total = evaluate_query(q, query_env, readable_kbs=UNSCOPED)
        assert total == 5
        assert len(entries) == 5

    def test_evaluate_with_type(self, query_env):
        q = CollectionQuery(kb_name="test-kb", entry_type="note")
        entries, total = evaluate_query(q, query_env, readable_kbs=UNSCOPED)
        assert total == 5

    def test_evaluate_with_tag(self, query_env):
        q = CollectionQuery(kb_name="test-kb", tags_any=["important"])
        entries, total = evaluate_query(q, query_env, readable_kbs=UNSCOPED)
        assert total == 3

    def test_evaluate_with_limit(self, query_env):
        q = CollectionQuery(kb_name="test-kb", limit=2)
        entries, total = evaluate_query(q, query_env, readable_kbs=UNSCOPED)
        assert len(entries) == 2
        assert total == 5

    def test_evaluate_with_offset(self, query_env):
        q = CollectionQuery(kb_name="test-kb", limit=2, offset=3)
        entries, total = evaluate_query(q, query_env, readable_kbs=UNSCOPED)
        assert len(entries) == 2
        assert total == 5


# =============================================================================
# Caching
# =============================================================================


class TestQueryCaching:
    """Test query caching behavior."""

    def setup_method(self):
        clear_cache()

    def test_cache_key_stable(self):
        q1 = CollectionQuery(entry_type="note", kb_name="test")
        q2 = CollectionQuery(entry_type="note", kb_name="test")
        assert _cache_key(q1, readable_kbs=UNSCOPED) == _cache_key(q2, readable_kbs=UNSCOPED)

    def test_cache_key_differs(self):
        q1 = CollectionQuery(entry_type="note")
        q2 = CollectionQuery(entry_type="event")
        assert _cache_key(q1, readable_kbs=UNSCOPED) != _cache_key(q2, readable_kbs=UNSCOPED)

    def test_cached_returns_same_result(self):
        """Test that cached version returns consistent results."""
        mock_db = MagicMock()
        mock_db.list_entries.return_value = [
            {"id": "e1", "title": "T1", "tags": [], "entry_type": "note", "kb_name": "test"}
        ]

        q = CollectionQuery(kb_name="test")
        result1, count1 = evaluate_query_cached(q, mock_db, readable_kbs=UNSCOPED)
        result2, count2 = evaluate_query_cached(q, mock_db, readable_kbs=UNSCOPED)

        assert result1 == result2
        assert count1 == count2
        # DB should only be called once due to caching
        assert mock_db.list_entries.call_count == 1

    def test_clear_cache(self):
        mock_db = MagicMock()
        mock_db.list_entries.return_value = []

        q = CollectionQuery(kb_name="test")
        evaluate_query_cached(q, mock_db, readable_kbs=UNSCOPED)
        clear_cache()
        evaluate_query_cached(q, mock_db, readable_kbs=UNSCOPED)
        # Should call DB twice since cache was cleared
        assert mock_db.list_entries.call_count == 2


# =============================================================================
# Virtual Collection through KBService
# =============================================================================


class TestVirtualCollectionService:
    """Test get_collection_entries with source_type='query'."""

    @pytest.fixture
    def virtual_collection_env(self):
        """Create test environment with a query-based virtual collection."""
        import json

        from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
        from pyrite.models.collection import CollectionEntry
        from pyrite.models.core_types import NoteEntry
        from pyrite.services.kb_service import KBService
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager
        from pyrite.storage.repository import KBRepository

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            db_path = tmpdir / "index.db"
            kb_path = tmpdir / "kb"
            kb_path.mkdir()

            kb_config = KBConfig(name="test-kb", path=kb_path, kb_type=KBType.GENERIC)
            repo = KBRepository(kb_config)

            # Create some notes
            for i in range(4):
                note = NoteEntry(
                    id=f"note-{i}",
                    title=f"Note {i}",
                    body=f"Content {i}",
                    tags=["test"],
                )
                repo.save(note)

            # Create a query-based collection entry
            collection = CollectionEntry(
                id="virtual-notes",
                title="Virtual Notes",
                source_type="query",
                query="type:note tags:test kb:test-kb",
                description="All test notes",
            )
            repo.save(collection)

            config = PyriteConfig(
                knowledge_bases=[kb_config],
                settings=Settings(index_path=db_path),
            )
            with PyriteDB(db_path) as db:
                IndexManager(db, config).index_all()

                svc = KBService(config, db)
                yield svc, db

    def test_virtual_collection_returns_entries(self, virtual_collection_env):
        svc, _ = virtual_collection_env
        entries, total = svc.get_collection_entries(
            "virtual-notes", "test-kb", readable_kbs=UNSCOPED
        )
        assert total == 4
        assert len(entries) == 4

    def test_virtual_collection_with_sort(self, virtual_collection_env):
        svc, _ = virtual_collection_env
        entries, total = svc.get_collection_entries(
            "virtual-notes", "test-kb", sort_by="title", sort_order="desc", readable_kbs=UNSCOPED
        )
        assert total == 4
        titles = [e["title"] for e in entries]
        assert titles == sorted(titles, reverse=True)


# =============================================================================
# Collection Model — query field
# =============================================================================


class TestCollectionModelQuery:
    """Test CollectionEntry query field support."""

    def test_query_field_default(self):
        from pyrite.models.collection import CollectionEntry

        entry = CollectionEntry(id="test", title="Test")
        assert entry.query == ""

    def test_query_field_in_frontmatter(self):
        from pyrite.models.collection import CollectionEntry

        entry = CollectionEntry(
            id="virtual-test",
            title="Virtual Test",
            source_type="query",
            query="type:note tags:test",
        )
        fm = entry.to_frontmatter()
        assert fm["source_type"] == "query"
        assert fm["query"] == "type:note tags:test"

    def test_query_field_not_in_folder_frontmatter(self):
        from pyrite.models.collection import CollectionEntry

        entry = CollectionEntry(
            id="folder-test",
            title="Folder Test",
            source_type="folder",
            folder_path="notes",
        )
        fm = entry.to_frontmatter()
        assert "query" not in fm

    def test_query_field_from_frontmatter(self):
        from pyrite.models.collection import CollectionEntry

        meta = {
            "id": "virtual-test",
            "title": "Virtual Test",
            "type": "collection",
            "source_type": "query",
            "query": "type:note tags:test",
        }
        entry = CollectionEntry.from_frontmatter(meta, "")
        assert entry.source_type == "query"
        assert entry.query == "type:note tags:test"


# =============================================================================
# CLI
# =============================================================================


class TestCollectionCLI:
    """Test CLI commands for collections."""

    def test_collections_app_exists(self):
        from pyrite.cli.collection_commands import collections_app

        assert collections_app is not None

    def test_query_command_registered(self):
        from pyrite.cli.collection_commands import collections_app

        command_names = [cmd.name for cmd in collections_app.registered_commands]
        assert "query" in command_names

    def test_list_command_registered(self):
        from pyrite.cli.collection_commands import collections_app

        command_names = [cmd.name for cmd in collections_app.registered_commands]
        assert "list" in command_names
