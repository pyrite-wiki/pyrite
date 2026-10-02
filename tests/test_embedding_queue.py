"""
Tests for the background embedding pipeline: queue table, worker, status endpoint.

Tests cover:
- embed_queue table creation and operations
- EmbeddingWorker queue processing with retry logic
- _auto_embed is queue-based, always (ADR-0035): a write records the debt and
  returns; it never loads the embedding model. The old "fall back to a
  synchronous embed when no worker is set" branch was #13 -- nothing in
  production ever set the worker, so that fall-back was the only path taken.
- Status endpoint /api/index/embed-status
- CLI `pyrite index embed --status`
"""

import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB


@pytest.fixture
def tmp_db():
    """Create a temporary database with entries for testing."""
    with tempfile.TemporaryDirectory() as d:
        db_path = Path(d) / "index.db"
        kb_path = Path(d) / "kb"
        kb_path.mkdir()

        db = PyriteDB(db_path)
        db.register_kb(name="test-kb", kb_type="generic", path=str(kb_path))

        # Insert some test entries directly
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, title, body, entry_type, file_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            (
                "entry-1",
                "test-kb",
                "Test Entry 1",
                "Some content here",
                "note",
                str(kb_path / "entry-1.md"),
            ),
        )
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, title, body, entry_type, file_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            (
                "entry-2",
                "test-kb",
                "Test Entry 2",
                "More content here",
                "note",
                str(kb_path / "entry-2.md"),
            ),
        )
        db._raw_conn.commit()

        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="test-kb", path=kb_path)],
            settings=Settings(index_path=db_path),
        )
        yield db, config, Path(d)
        db.close()


# =============================================================================
# Queue table tests
# =============================================================================


class TestEmbedQueueTable:
    """Test embed_queue table creation and operations."""

    def test_queue_table_exists(self, tmp_db):
        """embed_queue table should be created when EmbeddingWorker initializes."""
        from pyrite.services.embedding_worker import EmbeddingWorker

        db, config, _ = tmp_db
        worker = EmbeddingWorker(db)
        tables = [
            r[0]
            for r in db._raw_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "embed_queue" in tables

    def test_enqueue_entry(self, tmp_db):
        """enqueue() should add an entry to the queue."""
        from pyrite.services.embedding_worker import EmbeddingWorker

        db, config, _ = tmp_db
        worker = EmbeddingWorker(db)
        worker.enqueue("entry-1", "test-kb")

        rows = db._raw_conn.execute("SELECT entry_id, kb_name, status FROM embed_queue").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "entry-1"
        assert rows[0][1] == "test-kb"
        assert rows[0][2] == "pending"

    def test_enqueue_idempotent(self, tmp_db):
        """Enqueueing the same entry twice should not create duplicates."""
        from pyrite.services.embedding_worker import EmbeddingWorker

        db, config, _ = tmp_db
        worker = EmbeddingWorker(db)
        worker.enqueue("entry-1", "test-kb")
        worker.enqueue("entry-1", "test-kb")

        count = db._raw_conn.execute(
            "SELECT COUNT(*) FROM embed_queue WHERE entry_id = 'entry-1'"
        ).fetchone()[0]
        assert count == 1

    def test_queue_status(self, tmp_db):
        """get_status() should return queue depth and counts."""
        from pyrite.services.embedding_worker import EmbeddingWorker

        db, config, _ = tmp_db
        worker = EmbeddingWorker(db)
        worker.enqueue("entry-1", "test-kb")
        worker.enqueue("entry-2", "test-kb")

        status = worker.get_status()
        assert status["pending"] == 2
        assert status["processing"] == 0
        assert status["failed"] == 0


# =============================================================================
# Worker processing tests
# =============================================================================


class TestEmbeddingWorkerProcessing:
    """Test the worker's process_batch method."""

    def test_process_batch_embeds_entries(self, tmp_db):
        """process_batch should mark entries as done on success."""
        from pyrite.services.embedding_worker import EmbeddingWorker

        db, config, _ = tmp_db
        worker = EmbeddingWorker(db)
        worker.enqueue("entry-1", "test-kb")

        # Mock the embedding service
        mock_svc = MagicMock()
        mock_svc.embed_entry.return_value = True
        worker._embedding_svc = mock_svc

        processed = worker.process_batch(batch_size=10)
        assert processed == 1
        mock_svc.embed_entry.assert_called_once_with("entry-1", "test-kb")

        status = worker.get_status()
        assert status["pending"] == 0

    def test_process_batch_handles_failure(self, tmp_db):
        """process_batch should increment attempts on failure, not mark as done."""
        from pyrite.services.embedding_worker import EmbeddingWorker

        db, config, _ = tmp_db
        worker = EmbeddingWorker(db)
        worker.enqueue("entry-1", "test-kb")

        mock_svc = MagicMock()
        mock_svc.embed_entry.side_effect = RuntimeError("Model not loaded")
        worker._embedding_svc = mock_svc

        processed = worker.process_batch(batch_size=10)
        assert processed == 0

        row = db._raw_conn.execute(
            "SELECT attempts, status, error FROM embed_queue WHERE entry_id = 'entry-1'"
        ).fetchone()
        assert row[0] == 1  # attempts incremented
        assert row[1] == "pending"  # still pending, not failed yet
        assert "Model not loaded" in row[2]

    def test_process_batch_marks_failed_after_max_attempts(self, tmp_db):
        """After max attempts, entry should be marked as failed."""
        from pyrite.services.embedding_worker import EmbeddingWorker

        db, config, _ = tmp_db
        worker = EmbeddingWorker(db, max_attempts=3)
        worker.enqueue("entry-1", "test-kb")

        mock_svc = MagicMock()
        mock_svc.embed_entry.side_effect = RuntimeError("fail")
        worker._embedding_svc = mock_svc

        # Process 3 times (max_attempts)
        for _ in range(3):
            worker.process_batch(batch_size=10)

        row = db._raw_conn.execute(
            "SELECT status, attempts FROM embed_queue WHERE entry_id = 'entry-1'"
        ).fetchone()
        assert row[0] == "failed"
        assert row[1] == 3

    def test_process_batch_respects_batch_size(self, tmp_db):
        """process_batch should only process up to batch_size entries."""
        from pyrite.services.embedding_worker import EmbeddingWorker

        db, config, _ = tmp_db
        worker = EmbeddingWorker(db)
        worker.enqueue("entry-1", "test-kb")
        worker.enqueue("entry-2", "test-kb")

        mock_svc = MagicMock()
        mock_svc.embed_entry.return_value = True
        worker._embedding_svc = mock_svc

        processed = worker.process_batch(batch_size=1)
        assert processed == 1
        assert worker.get_status()["pending"] == 1


# =============================================================================
# KBService integration
# =============================================================================


class TestKBServiceQueueIntegration:
    """Test that KBService uses queue when worker is available."""

    def test_auto_embed_uses_queue_when_worker_set(self, tmp_db):
        """When _embedding_worker is set, _auto_embed should enqueue instead of sync embed."""
        from pyrite.services.embedding_worker import EmbeddingWorker
        from pyrite.services.kb_service import KBService

        db, config, _ = tmp_db
        svc = KBService(config, db)

        worker = EmbeddingWorker(db)
        svc._embedding_worker = worker

        # Call _auto_embed
        svc._auto_embed("entry-1", "test-kb")

        # Should be in queue
        count = db._raw_conn.execute(
            "SELECT COUNT(*) FROM embed_queue WHERE entry_id = 'entry-1'"
        ).fetchone()[0]
        assert count == 1

    def test_auto_embed_builds_its_own_worker_when_none_was_injected(self, tmp_db):
        """No worker set is no longer a fall-back to synchronous embedding.

        It used to be, and that was #13: `_embedding_worker` existed but
        nothing in production ever assigned it, so *every* write on *every*
        surface took the synchronous branch and the first write on a fresh
        install spent over a minute downloading a ~90 MB model inside the
        request. ADR-0035 made enqueueing the only behaviour, which means
        KBService has to be able to produce a worker itself rather than
        waiting to be handed one.
        """
        from pyrite.services.kb_service import KBService

        db, config, _ = tmp_db
        svc = KBService(config, db)

        # An embedding service is available and would work -- it still must
        # not be called, because a write does not embed.
        mock_embed_svc = MagicMock()
        svc._embedding_svc = mock_embed_svc
        svc._embedding_checked = True
        assert svc._embedding_worker is None

        svc._auto_embed("entry-1", "test-kb")

        mock_embed_svc.embed_entry.assert_not_called()
        count = db._raw_conn.execute(
            "SELECT COUNT(*) FROM embed_queue WHERE entry_id = 'entry-1' AND status = 'pending'"
        ).fetchone()[0]
        assert count == 1

    def test_auto_embed_does_nothing_at_all_when_the_setting_is_off(self, tmp_db):
        """ADR-0035 §4: `auto_embed: false` keeps its exact present meaning."""
        import dataclasses

        from pyrite.services.kb_service import KBService

        db, config, _ = tmp_db
        config = dataclasses.replace(
            config, settings=dataclasses.replace(config.settings, auto_embed=False)
        )
        svc = KBService(config, db)
        mock_embed_svc = MagicMock()
        svc._embedding_svc = mock_embed_svc
        svc._embedding_checked = True

        svc._auto_embed("entry-1", "test-kb")

        mock_embed_svc.embed_entry.assert_not_called()
        rows = db._raw_conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='embed_queue'"
        ).fetchone()[0]
        if rows:  # the table may exist from another test on this db
            assert db._raw_conn.execute("SELECT COUNT(*) FROM embed_queue").fetchone()[0] == 0

    def test_a_queue_failure_is_logged_and_never_fails_the_write(self, tmp_db, caplog):
        """The entry is on disk and indexed either way; losing the queue row
        costs semantic freshness, not the write. It must still be visible --
        a silent drop is how "why can't semantic search find this" starts."""
        import logging

        from pyrite.services.kb_service import KBService

        db, config, _ = tmp_db
        svc = KBService(config, db)
        svc._embedding_worker = MagicMock(
            **{"enqueue.side_effect": RuntimeError("queue table is read-only")}
        )

        with caplog.at_level(logging.WARNING):
            svc._auto_embed("entry-1", "test-kb")  # must not raise

        assert any(
            record.levelno >= logging.WARNING and "entry-1" in record.message
            for record in caplog.records
        )


# =============================================================================
# REST API status endpoint
# =============================================================================


class TestEmbedStatusEndpoint:
    """Test GET /api/index/embed-status."""

    def test_embed_status_returns_queue_info(self, tmp_db):
        """Endpoint should return queue depth and processing info."""
        fastapi = pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from pyrite.server.api import create_app, get_config, get_db

        db, config, _ = tmp_db
        app = create_app(config=config)
        app.dependency_overrides[get_config] = lambda: config
        app.dependency_overrides[get_db] = lambda: db

        client = TestClient(app)
        resp = client.get("/api/index/embed-status")
        assert resp.status_code == 200
        data = resp.json()
        assert "pending" in data
        assert "failed" in data
        assert "processing" in data


class TestOfflineEmbeddingModel:
    def test_unavailable_model_does_not_consume_entry_attempts(self, tmp_db):
        from pyrite.services.embedding_service import EmbeddingService
        from pyrite.services.embedding_worker import EmbeddingWorker

        db, _, _ = tmp_db
        db.backend.vec_available = True
        worker = EmbeddingWorker(db)
        worker.enqueue("entry-1", "test-kb")
        service = EmbeddingService(db, model_name="offline-test-model")
        worker._embedding_svc = service

        assert db.get_entry("entry-1", "test-kb") is not None

        with (
            patch("pyrite.services.embedding_service.is_available", return_value=True),
            patch(
                "pyrite.services.embedding_service._load_model",
                side_effect=OSError("model unavailable offline"),
            ) as load_model,
        ):
            for _ in range(3):
                worker.drain(batch_size=10)

        row = db._raw_conn.execute(
            "SELECT attempts, status, error FROM embed_queue "
            "WHERE entry_id = 'entry-1' AND kb_name = 'test-kb'"
        ).fetchone()
        assert tuple(row) == (0, "pending", None)
        assert load_model.call_count == 3
