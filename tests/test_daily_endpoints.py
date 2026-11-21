"""Tests for the Daily Notes endpoint (web-daily-notes-view-side-effect).

GET /api/daily/{date} must never write for a read-tier caller: merely
navigating to a date with no existing note should show an empty state,
not silently create an entry. Uses real auth (not a mock) so the
request carries a real read-tier user, matching the reported bug's
exact condition.
"""

import tempfile
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app, get_config, get_db
from pyrite.storage.database import PyriteDB
from tests.auth_seed import seed_and_sign_in


@pytest.fixture
def daily_env():
    with tempfile.TemporaryDirectory() as d:
        tmpdir = Path(d)
        db_path = tmpdir / "index.db"
        kb_path = tmpdir / "kb"
        kb_path.mkdir()

        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(name="test-kb", path=kb_path, kb_type="generic", default_role="read")
            ],
            settings=Settings(
                index_path=db_path,
                auth=AuthConfig(enabled=True, allow_registration=True),
            ),
        )

        app = create_app(config=config)
        db = PyriteDB(db_path)
        app.dependency_overrides[get_config] = lambda: config
        app.dependency_overrides[get_db] = lambda: db

        client = TestClient(app)
        # The sole admin (write access), seeded via the operator path.
        seed_and_sign_in(client, "admin", "password123")
        admin_client = client

        # Second user is read-tier by default.
        reader_client = TestClient(app)
        reader_client.post("/auth/register", json={"username": "reader", "password": "password123"})

        yield {
            "admin_client": admin_client,
            "reader_client": reader_client,
            "config": config,
            "db": db,
        }
        db.close()


class TestCreateDailyNoteEndpoint:
    def test_write_tier_can_explicitly_create(self, daily_env):
        admin = daily_env["admin_client"]
        response = admin.post("/api/daily/2026-02-01?kb=test-kb")
        assert response.status_code == 200
        assert response.json()["id"] == "daily-2026-02-01"

    def test_read_tier_cannot_create(self, daily_env):
        reader = daily_env["reader_client"]
        response = reader.post("/api/daily/2026-02-02?kb=test-kb")
        assert response.status_code == 403

        kb_path = daily_env["config"].knowledge_bases[0].path
        assert not (kb_path / "daily-2026-02-02.md").exists()

    def test_create_on_already_existing_note_returns_it(self, daily_env):
        admin = daily_env["admin_client"]
        first = admin.post("/api/daily/2026-02-03?kb=test-kb")
        assert first.status_code == 200

        second = admin.post("/api/daily/2026-02-03?kb=test-kb")
        assert second.status_code == 200
        assert second.json()["id"] == "daily-2026-02-03"


class TestDailyNoteReadTierNoSideEffect:
    def test_read_tier_viewing_new_date_creates_no_entry(self, daily_env):
        reader = daily_env["reader_client"]
        response = reader.get("/api/daily/2026-01-15?kb=test-kb")

        # Must not silently create an entry for a read-tier caller.
        assert response.status_code == 404

        # Confirm no entry was actually written to the KB.
        kb_path = daily_env["config"].knowledge_bases[0].path
        assert not (kb_path / "daily-2026-01-15.md").exists()

    def test_write_tier_viewing_new_date_creates_no_entry(self, daily_env):
        """A GET writes nothing for any caller: it never asks where a write
        lands, so a note it made would land canonical past a review KB's
        change set (ADR-0044 L2). The UI creates with POST."""
        admin = daily_env["admin_client"]
        response = admin.get("/api/daily/2026-01-16?kb=test-kb")

        assert response.status_code == 404
        kb_path = daily_env["config"].knowledge_bases[0].path
        assert not (kb_path / "daily-2026-01-16.md").exists()

    @pytest.mark.control(reason="reading an existing note is unchanged; it is now made by POST")
    def test_read_tier_can_still_read_an_existing_note(self, daily_env):
        admin = daily_env["admin_client"]
        reader = daily_env["reader_client"]

        # Admin creates it first (existing note case).
        created = admin.post("/api/daily/2026-01-17?kb=test-kb")
        assert created.status_code == 200

        # Reader can view the now-existing note without any error.
        response = reader.get("/api/daily/2026-01-17?kb=test-kb")
        assert response.status_code == 200
        assert response.json()["id"] == "daily-2026-01-17"
