"""Tests for Collections (Phase 1) — model, repository, service, API."""

import tempfile
from pathlib import Path

import pytest

from pyrite.models.collection import CollectionEntry
from pyrite.models.core_types import ENTRY_TYPE_REGISTRY, get_entry_class
from pyrite.models.factory import build_entry
from pyrite.schema import CORE_TYPES
from pyrite.services.access_policy import UNSCOPED


# =============================================================================
# Model tests
# =============================================================================


class TestCollectionEntryModel:
    """Tests for CollectionEntry dataclass."""

    def test_collection_entry_from_frontmatter(self):
        meta = {
            "id": "collection-notes",
            "title": "Notes Collection",
            "type": "collection",
            "description": "All general notes",
            "source_type": "folder",
            "icon": "book",
            "view_config": {"default_view": "table", "table_columns": ["title", "tags"]},
            "folder_path": "notes",
            "tags": ["core"],
        }
        entry = CollectionEntry.from_frontmatter(meta, "")
        assert entry.id == "collection-notes"
        assert entry.title == "Notes Collection"
        assert entry.entry_type == "collection"
        assert entry.description == "All general notes"
        assert entry.source_type == "folder"
        assert entry.icon == "book"
        assert entry.view_config["default_view"] == "table"
        assert entry.folder_path == "notes"
        assert entry.tags == ["core"]

    def test_collection_entry_to_frontmatter(self):
        entry = CollectionEntry(
            id="collection-docs",
            title="Documents",
            description="Reference documents",
            icon="file",
            view_config={"default_view": "table"},
            folder_path="documents",
        )
        fm = entry.to_frontmatter()
        assert fm["title"] == "Documents"
        assert fm["description"] == "Reference documents"
        assert fm["icon"] == "file"
        assert fm["view_config"]["default_view"] == "table"
        assert fm["folder_path"] == "documents"
        # source_type is always written to prevent round-trip data loss
        assert fm["source_type"] == "folder"

    def test_collection_from_collection_yaml(self):
        yaml_data = {
            "title": "Notes Collection",
            "description": "All notes",
            "icon": "book",
            "view_config": {"default_view": "list"},
        }
        entry = CollectionEntry.from_collection_yaml(yaml_data, "notes")
        assert entry.id == "collection-notes"
        assert entry.title == "Notes Collection"
        assert entry.folder_path == "notes"
        assert entry.source_type == "folder"

    def test_collection_id_generation(self):
        yaml_data = {"title": "My Research"}
        entry = CollectionEntry.from_collection_yaml(yaml_data, "my-research")
        assert entry.id == "collection-my-research"

        # With explicit id
        yaml_data2 = {"title": "Custom", "id": "custom-collection"}
        entry2 = CollectionEntry.from_collection_yaml(yaml_data2, "folder")
        assert entry2.id == "custom-collection"

    def test_collection_in_registry(self):
        assert "collection" in ENTRY_TYPE_REGISTRY
        cls = get_entry_class("collection")
        assert cls is CollectionEntry


class TestCollectionTypeRegistration:
    """Test that collection is properly registered in schema."""

    def test_collection_in_core_types(self):
        assert "collection" in CORE_TYPES
        ct = CORE_TYPES["collection"]
        assert ct["subdirectory"] is None
        assert "description" in ct["fields"]

    def test_build_entry_collection(self):
        entry = build_entry(
            "collection",
            title="Test Collection",
            description="A test",
            folder_path="test-folder",
        )
        assert isinstance(entry, CollectionEntry)
        assert entry.title == "Test Collection"
        assert entry.description == "A test"
        assert entry.folder_path == "test-folder"
        assert entry.source_type == "folder"


# =============================================================================
# Repository tests
# =============================================================================


class TestCollectionRepository:
    """Tests for __collection.yaml discovery in KBRepository."""

    @pytest.fixture
    def kb_with_collection(self):
        """Create a temp KB with a __collection.yaml file."""
        from pyrite.config import KBConfig, KBType
        from pyrite.models.core_types import NoteEntry
        from pyrite.storage.repository import KBRepository

        with tempfile.TemporaryDirectory() as tmpdir:
            kb_path = Path(tmpdir)
            notes_dir = kb_path / "notes"
            notes_dir.mkdir()

            # Create collection yaml
            (notes_dir / "__collection.yaml").write_text(
                "title: Notes Collection\ndescription: All notes\nicon: book\n"
            )

            # Create a sample note in the folder
            note = NoteEntry(id="test-note", title="Test Note", body="Hello")
            note_path = notes_dir / "test-note.md"
            note.save(note_path)

            kb_config = KBConfig(name="test-kb", path=kb_path, kb_type=KBType.GENERIC)
            repo = KBRepository(kb_config)
            yield repo, kb_path

    def test_list_entries_discovers_collections(self, kb_with_collection):
        repo, _ = kb_with_collection
        entries = list(repo.list_entries())
        types = [e.entry_type for e, _ in entries]
        assert "collection" in types
        collection_entries = [(e, p) for e, p in entries if e.entry_type == "collection"]
        assert len(collection_entries) == 1
        entry, path = collection_entries[0]
        assert entry.id == "collection-notes"
        assert entry.title == "Notes Collection"
        assert entry.folder_path == "notes"

    def test_list_entries_without_collection_yaml(self):
        """Folders without __collection.yaml should not produce collection entries."""
        from pyrite.config import KBConfig, KBType
        from pyrite.storage.repository import KBRepository

        with tempfile.TemporaryDirectory() as tmpdir:
            kb_path = Path(tmpdir)
            (kb_path / "notes").mkdir()

            kb_config = KBConfig(name="test-kb", path=kb_path, kb_type=KBType.GENERIC)
            repo = KBRepository(kb_config)
            entries = list(repo.list_entries())
            types = [e.entry_type for e, _ in entries]
            assert "collection" not in types

    def test_collection_exists(self, kb_with_collection):
        repo, _ = kb_with_collection
        assert repo.exists("collection-notes")

    def test_collection_find_file(self, kb_with_collection):
        repo, kb_path = kb_with_collection
        path = repo.find_file("collection-notes")
        assert path is not None
        assert path.name == "__collection.yaml"
        assert path.parent.name == "notes"

    def test_collection_count(self, kb_with_collection):
        repo, _ = kb_with_collection
        count = repo.count()
        # Should include the markdown file AND the collection yaml
        assert count >= 2


# =============================================================================
# Service / API tests
# =============================================================================


class TestCollectionService:
    """Tests for KBService collection methods."""

    @pytest.fixture
    def svc_env(self):
        """Create test environment with indexed collection data."""
        from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
        from pyrite.models.core_types import NoteEntry
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager
        from pyrite.storage.repository import KBRepository
        from pyrite.services.kb_service import KBService

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            db_path = tmpdir / "index.db"
            kb_path = tmpdir / "kb"
            kb_path.mkdir()

            notes_dir = kb_path / "notes"
            notes_dir.mkdir()

            # Create collection yaml
            (notes_dir / "__collection.yaml").write_text(
                "title: Notes Collection\n"
                "description: All notes\n"
                "icon: book\n"
                "view_config:\n"
                "  default_view: list\n"
                "  table_columns:\n"
                "    - title\n"
                "    - tags\n"
            )

            # Create notes in the folder
            kb_config = KBConfig(name="test-kb", path=kb_path, kb_type=KBType.GENERIC)
            repo = KBRepository(kb_config)
            for i in range(3):
                note = NoteEntry(
                    id=f"note-{i}",
                    title=f"Note {i}",
                    body=f"Content of note {i}",
                    tags=["test"],
                )
                note.save(notes_dir / f"note-{i}.md")

            config = PyriteConfig(
                knowledge_bases=[kb_config],
                settings=Settings(index_path=db_path),
            )
            with PyriteDB(db_path) as db:
                index_mgr = IndexManager(db, config)
                index_mgr.index_all()

                svc = KBService(config, db)
                yield svc, db

    def test_list_collections(self, svc_env):
        svc, _ = svc_env
        collections = svc.list_collections(kb_name="test-kb")
        assert len(collections) == 1
        c = collections[0]
        assert c["entry_type"] == "collection"
        assert c["title"] == "Notes Collection"

    def test_get_collection_entries(self, svc_env):
        svc, _ = svc_env
        entries, total = svc.get_collection_entries(
            "collection-notes", "test-kb", readable_kbs=UNSCOPED
        )
        assert total == 3
        assert len(entries) == 3

    def test_get_collection_entries_metadata_is_dict(self, svc_env):
        """Regression: get_collection_entries returns rows whose `metadata`
        field is a dict, not a JSON-encoded string.

        Pre-fix, the raw-SQL list path (list_entries_in_folder → _exec)
        returned metadata as the column-text string '{}', which broke the
        REST endpoint's EntryResponse(metadata: dict) at construction. See
        bug-collection-entries-endpoint-metadata-string-pydantic-rejection.
        """
        svc, _ = svc_env
        entries, _ = svc.get_collection_entries(
            "collection-notes", "test-kb", readable_kbs=UNSCOPED
        )
        assert entries, "fixture must return entries for this test to be meaningful"
        for r in entries:
            assert isinstance(r.get("metadata"), dict), (
                f"entry {r.get('id')} metadata must be a dict, got "
                f"{type(r.get('metadata')).__name__}: {r.get('metadata')!r}"
            )

    def test_get_collection_entries_nonexistent(self, svc_env):
        from pyrite.exceptions import EntryNotFoundError

        svc, _ = svc_env
        with pytest.raises(EntryNotFoundError):
            svc.get_collection_entries("collection-nonexistent", "test-kb", readable_kbs=UNSCOPED)


class TestCollectionAPI:
    """Tests for collection REST endpoints."""

    @pytest.fixture
    def client(self):
        """Create test client with collection data."""
        fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")
        from fastapi.testclient import TestClient

        from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
        from pyrite.models.core_types import NoteEntry
        from pyrite.server.api import create_app
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager
        from pyrite.storage.repository import KBRepository

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            db_path = tmpdir / "index.db"
            kb_path = tmpdir / "kb"
            kb_path.mkdir()

            notes_dir = kb_path / "notes"
            notes_dir.mkdir()

            (notes_dir / "__collection.yaml").write_text(
                "title: Notes Collection\ndescription: All notes\n"
            )

            kb_config = KBConfig(name="test-kb", path=kb_path, kb_type=KBType.GENERIC)
            repo = KBRepository(kb_config)

            for i in range(2):
                note = NoteEntry(
                    id=f"note-{i}",
                    title=f"Note {i}",
                    body=f"Content {i}",
                )
                note.save(notes_dir / f"note-{i}.md")

            config = PyriteConfig(
                knowledge_bases=[kb_config],
                settings=Settings(index_path=db_path),
            )
            db = PyriteDB(db_path)
            IndexManager(db, config).index_all()

            from pyrite.server.api import get_config, get_db

            app = create_app(config)
            app.dependency_overrides[get_config] = lambda: config
            app.dependency_overrides[get_db] = lambda: db
            yield TestClient(app)
            db.close()

    def test_get_collections_endpoint(self, client):
        resp = client.get("/api/collections?kb=test-kb")
        assert resp.status_code == 200
        data = resp.json()
        assert "collections" in data
        assert data["total"] >= 1
        assert data["collections"][0]["title"] == "Notes Collection"

    def test_get_collection_entries_endpoint(self, client):
        resp = client.get("/api/collections/collection-notes/entries?kb=test-kb")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert data["collection_id"] == "collection-notes"
        assert data["total"] == 2

    def test_get_nonexistent_collection(self, client):
        resp = client.get("/api/collections/collection-nope/entries?kb=test-kb")
        assert resp.status_code == 404

    def test_list_collections_empty(self, client):
        """Listing collections for a KB with none should return empty list."""
        resp = client.get("/api/collections?kb=nonexistent-kb")
        assert resp.status_code == 200
        data = resp.json()
        assert data["collections"] == []
        assert data["total"] == 0

    def test_get_collection_not_found(self, client):
        """GET /api/collections/{id} for missing collection returns 404."""
        resp = client.get("/api/collections/no-such-collection?kb=test-kb")
        assert resp.status_code == 404
        data = resp.json()
        assert data["detail"]["code"] == "NOT_FOUND"

    def test_preview_query_invalid(self, client):
        """POST /api/collections/query-preview with invalid sort_by returns 400."""
        resp = client.post(
            "/api/collections/query-preview",
            json={"query": "type:note sort:BOGUS_COLUMN", "kb": "test-kb", "limit": 10},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"]["code"] == "INVALID_QUERY"

    def test_get_collection_entries_pagination(self, client):
        """GET /api/collections/{id}/entries respects limit/offset."""
        resp = client.get("/api/collections/collection-notes/entries?kb=test-kb&limit=1&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entries"]) == 1
        assert data["total"] == 2  # 2 notes total in fixture
