"""Tests for plugin API and import/export endpoints."""

import json
import tempfile
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")
from fastapi.testclient import TestClient

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models import EventEntry
from pyrite.server.api import create_app
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository


@pytest.fixture
def test_env():
    """Create test environment with a KB and sample data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"

        kb_path = tmpdir / "test-kb"
        kb_path.mkdir()

        kb_config = KBConfig(
            name="test-kb",
            path=kb_path,
            kb_type=KBType.GENERIC,
        )

        config = PyriteConfig(
            knowledge_bases=[kb_config],
            settings=Settings(index_path=db_path),
        )

        # Create a sample entry
        repo = KBRepository(kb_config)
        event = EventEntry.create(
            date="2025-01-10",
            title="Test Event",
            body="Body for test event.",
            importance=5,
        )
        event.tags = ["test"]
        repo.save(event)

        db = PyriteDB(db_path)
        index_mgr = IndexManager(db, config)
        index_mgr.index_all()

        from pyrite.server.api import get_config, get_db, get_index_mgr

        app = create_app(config)
        app.dependency_overrides[get_config] = lambda: config
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_index_mgr] = lambda: index_mgr
        client = TestClient(app)

        yield {
            "client": client,
            "config": config,
            "db": db,
            "kb_config": kb_config,
        }

        db.close()


class TestPluginEndpoints:
    def test_list_plugins(self, test_env):
        resp = test_env["client"].get("/api/plugins")
        assert resp.status_code == 200
        data = resp.json()
        assert "plugins" in data
        assert "total" in data
        assert isinstance(data["plugins"], list)

    def test_get_plugin_not_found(self, test_env):
        resp = test_env["client"].get("/api/plugins/nonexistent")
        assert resp.status_code == 404


class TestImportExportEndpoints:
    def test_export_json(self, test_env):
        resp = test_env["client"].get("/api/entries/export?kb=test-kb&format=json")
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]

    def test_export_csv(self, test_env):
        resp = test_env["client"].get("/api/entries/export?kb=test-kb&format=csv")
        assert resp.status_code == 200

    def test_export_markdown(self, test_env):
        resp = test_env["client"].get("/api/entries/export?kb=test-kb&format=markdown")
        assert resp.status_code == 200

    def test_export_unknown_kb(self, test_env):
        resp = test_env["client"].get("/api/entries/export?kb=nonexistent&format=json")
        assert resp.status_code == 404

    def test_import_json(self, test_env):
        data = json.dumps([{"title": "Imported Entry", "body": "Content"}])
        resp = test_env["client"].post(
            "/api/entries/import?kb=test-kb&format=json",
            files={"file": ("test.json", data, "application/json")},
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["imported"] >= 1

    def test_import_markdown_is_one_entry_unless_stream_is_asked_for(self, test_env):
        md = "---\ntype: note\ntitle: First\n---\nbody\n---\ntype: note\ntitle: Second\n---\nmore\n"
        client = test_env["client"]
        one = client.post(
            "/api/entries/import?kb=test-kb&format=markdown",
            files={"file": ("s.md", md, "text/markdown")},
        )
        two = client.post(
            "/api/entries/import?kb=test-kb&format=markdown&stream=true",
            files={
                "file": (
                    "s2.md",
                    md.replace("First", "Third").replace("Second", "Fourth"),
                    "text/markdown",
                )
            },
        )
        bad = client.post(
            "/api/entries/import?kb=test-kb&format=json&stream=true",
            files={"file": ("s.json", "[]", "application/json")},
        )
        assert one.status_code == 200 and one.json()["imported"] == 1
        assert two.status_code == 200 and two.json()["imported"] == 2
        assert bad.status_code == 400
        assert bad.json()["detail"]["code"] == "STREAM_NOT_SUPPORTED"

    def test_import_csv(self, test_env):
        csv_data = "title,body,tags\nCSV Entry,Body,tag1;tag2\n"
        resp = test_env["client"].post(
            "/api/entries/import?kb=test-kb&format=csv",
            files={"file": ("test.csv", csv_data, "text/csv")},
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["imported"] >= 1

    def test_import_unknown_kb(self, test_env):
        resp = test_env["client"].post(
            "/api/entries/import?kb=nonexistent&format=json",
            files={"file": ("test.json", "[]", "application/json")},
        )
        assert resp.status_code == 404

    def test_import_auto_detect_format(self, test_env):
        """Format is auto-detected from file extension when not specified."""
        data = json.dumps([{"title": "Auto Entry", "body": "Content"}])
        resp = test_env["client"].post(
            "/api/entries/import?kb=test-kb",
            files={"file": ("test.json", data, "application/json")},
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["imported"] >= 1

    def test_import_bad_format(self, test_env):
        resp = test_env["client"].post(
            "/api/entries/import?kb=test-kb&format=xml",
            files={"file": ("test.xml", "<entries/>", "text/xml")},
        )
        assert resp.status_code == 400
