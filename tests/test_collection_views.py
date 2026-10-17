"""Tests for collection view support — PATCH field update endpoint."""

import tempfile
from pathlib import Path

import pytest


class TestPatchEntryEndpoint:
    """Tests for PATCH /api/entries/{entry_id} field update."""

    @pytest.fixture
    def client(self):
        """Create test client with entry data for PATCH testing."""
        fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")
        from fastapi.testclient import TestClient

        from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
        from pyrite.models.core_types import NoteEntry
        from pyrite.storage.entry_writer import create_entry_text
        from pyrite.storage.repository import new_entry_text
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

            kb_config = KBConfig(name="test-kb", path=kb_path, kb_type=KBType.GENERIC)
            repo = KBRepository(kb_config)

            for i in range(2):
                note = NoteEntry(
                    id=f"note-{i}",
                    title=f"Note {i}",
                    body=f"Content {i}",
                    tags=["test"],
                )
                create_entry_text(notes_dir / f"note-{i}.md", new_entry_text(note))

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

    def test_patch_entry_field(self, client):
        """PATCH should update a single field on an entry."""
        resp = client.patch(
            "/api/entries/note-0",
            json={"kb": "test-kb", "field": "title", "value": "Updated Title"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] is True
        assert data["id"] == "note-0"

        # Verify the field was updated
        get_resp = client.get("/api/entries/note-0?kb=test-kb")
        assert get_resp.status_code == 200
        assert get_resp.json()["title"] == "Updated Title"

    def test_patch_entry_not_found(self, client):
        """PATCH should return 404 for nonexistent entry."""
        resp = client.patch(
            "/api/entries/nonexistent",
            json={"kb": "test-kb", "field": "title", "value": "Nope"},
        )
        assert resp.status_code == 404

    def test_patch_entry_requires_kb(self, client):
        """PATCH should require kb field."""
        resp = client.patch(
            "/api/entries/note-0",
            json={"field": "title", "value": "Missing KB"},
        )
        assert resp.status_code == 422
