"""Tests for KB compaction: staleness detection and archival candidates."""

from datetime import UTC, datetime, timedelta

import pytest

from pyrite.config import PyriteConfig
from pyrite.services.qa_service import QAService
from pyrite.services.access_policy import UNSCOPED


@pytest.fixture
def qa_setup(tmp_path):
    """Set up config, db, and QA service for compaction tests."""
    from pyrite.config import KBConfig
    from pyrite.storage.database import PyriteDB

    db_path = tmp_path / "test.db"
    config = PyriteConfig()
    kb_config = KBConfig(name="test", path=tmp_path / "test-kb")
    config.knowledge_bases = [kb_config]
    db = PyriteDB(str(db_path))
    db.register_kb("test", "generic", str(tmp_path / "test-kb"))
    svc = QAService(config, db)
    return {"config": config, "db": db, "svc": svc, "kb_name": "test"}


def _insert_entry(db, entry_id, kb_name, entry_type="note", title="Test", updated_at=None):
    """Insert a test entry with a specific updated_at timestamp."""
    if updated_at is None:
        updated_at = datetime.now(UTC).isoformat()
    elif isinstance(updated_at, datetime):
        updated_at = updated_at.isoformat()
    db.execute_sql(
        "INSERT INTO entry (id, kb_name, entry_type, title, body, updated_at, lifecycle) "
        "VALUES (:id, :kb, :etype, :title, '', :updated, 'active')",
        {"id": entry_id, "kb": kb_name, "etype": entry_type, "title": title, "updated": updated_at},
    )


class TestFindStale:
    """Test QAService.find_stale() method."""

    def test_finds_stale_entries(self, qa_setup):
        """Entries not updated in over N days are flagged as stale."""
        db, svc, kb = qa_setup["db"], qa_setup["svc"], qa_setup["kb_name"]

        old_date = datetime.now(UTC) - timedelta(days=200)
        _insert_entry(db, "old-note", kb, "note", "Old Note", old_date)
        _insert_entry(db, "fresh-note", kb, "note", "Fresh Note")

        results = svc.find_stale(kb, max_age_days=90)
        stale_ids = [r["entry_id"] for r in results]
        assert "old-note" in stale_ids
        assert "fresh-note" not in stale_ids

    def test_type_aware_staleness(self, qa_setup):
        """Types with staleness_days=0 in metadata are exempt from staleness checks."""
        db, svc, kb = qa_setup["db"], qa_setup["svc"], qa_setup["kb_name"]

        old_date = datetime.now(UTC) - timedelta(days=200)
        # ADRs are historical records — they should never be flagged as stale
        _insert_entry(db, "old-adr", kb, "adr", "Old ADR", old_date)
        _insert_entry(db, "old-note", kb, "note", "Old Note", old_date)

        results = svc.find_stale(kb, max_age_days=90)
        stale_ids = [r["entry_id"] for r in results]
        assert "old-adr" not in stale_ids
        assert "old-note" in stale_ids

    def test_excludes_archived(self, qa_setup):
        """Archived entries are excluded from staleness checks."""
        db, svc, kb = qa_setup["db"], qa_setup["svc"], qa_setup["kb_name"]

        old_date = datetime.now(UTC) - timedelta(days=200)
        db.execute_sql(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, updated_at, lifecycle) "
            "VALUES (:id, :kb, 'note', 'Archived', '', :updated, 'archived')",
            {"id": "archived-note", "kb": kb, "updated": old_date.isoformat()},
        )

        results = svc.find_stale(kb, max_age_days=90)
        stale_ids = [r["entry_id"] for r in results]
        assert "archived-note" not in stale_ids

    def test_custom_max_age(self, qa_setup):
        """max_age_days parameter controls the staleness threshold."""
        db, svc, kb = qa_setup["db"], qa_setup["svc"], qa_setup["kb_name"]

        medium_old = datetime.now(UTC) - timedelta(days=45)
        _insert_entry(db, "medium-note", kb, "note", "Medium", medium_old)

        # 90-day threshold — not stale
        results_90 = svc.find_stale(kb, max_age_days=90)
        assert len(results_90) == 0

        # 30-day threshold — stale
        results_30 = svc.find_stale(kb, max_age_days=30)
        assert len(results_30) == 1

    def test_returns_entry_metadata(self, qa_setup):
        """Stale entries include useful metadata: id, type, title, days_stale."""
        db, svc, kb = qa_setup["db"], qa_setup["svc"], qa_setup["kb_name"]

        old_date = datetime.now(UTC) - timedelta(days=100)
        _insert_entry(db, "old-note", kb, "note", "Old Note", old_date)

        results = svc.find_stale(kb, max_age_days=90)
        assert len(results) == 1
        entry = results[0]
        assert entry["entry_id"] == "old-note"
        assert entry["entry_type"] == "note"
        assert entry["title"] == "Old Note"
        assert entry["days_stale"] >= 99  # At least 100 - 1 tolerance


class TestFindArchivalCandidates:
    """Test QAService.find_archival_candidates() method."""

    def test_done_backlog_items_are_candidates(self, qa_setup):
        """Completed backlog items with no inbound links are archival candidates."""
        db, svc, kb = qa_setup["db"], qa_setup["svc"], qa_setup["kb_name"]

        old_date = datetime.now(UTC) - timedelta(days=200)
        _insert_entry(db, "done-item", kb, "backlog_item", "Done Item", old_date)
        # Set status to 'completed' via metadata
        db.execute_sql(
            "UPDATE entry SET status = 'completed' WHERE id = 'done-item'",
            {},
        )

        results = svc.find_archival_candidates(kb, min_age_days=90)
        candidate_ids = [r["entry_id"] for r in results]
        assert "done-item" in candidate_ids

    def test_active_backlog_not_candidate(self, qa_setup):
        """Active backlog items are not archival candidates."""
        db, svc, kb = qa_setup["db"], qa_setup["svc"], qa_setup["kb_name"]

        old_date = datetime.now(UTC) - timedelta(days=200)
        _insert_entry(db, "active-item", kb, "backlog_item", "Active Item", old_date)
        db.execute_sql(
            "UPDATE entry SET status = 'proposed' WHERE id = 'active-item'",
            {},
        )

        results = svc.find_archival_candidates(kb, min_age_days=90)
        candidate_ids = [r["entry_id"] for r in results]
        assert "active-item" not in candidate_ids

    def test_recent_done_items_not_candidate(self, qa_setup):
        """Recently completed items are not candidates regardless of status."""
        db, svc, kb = qa_setup["db"], qa_setup["svc"], qa_setup["kb_name"]

        recent = datetime.now(UTC) - timedelta(days=10)
        _insert_entry(db, "recent-done", kb, "backlog_item", "Recent Done", recent)
        db.execute_sql(
            "UPDATE entry SET status = 'completed' WHERE id = 'recent-done'",
            {},
        )

        results = svc.find_archival_candidates(kb, min_age_days=90)
        candidate_ids = [r["entry_id"] for r in results]
        assert "recent-done" not in candidate_ids

    def test_old_orphan_entries_are_candidates(self, qa_setup):
        """Old entries with no links and low importance are archival candidates."""
        db, svc, kb = qa_setup["db"], qa_setup["svc"], qa_setup["kb_name"]

        old_date = datetime.now(UTC) - timedelta(days=200)
        _insert_entry(db, "orphan-note", kb, "note", "Orphan Note", old_date)
        db.execute_sql(
            "UPDATE entry SET importance = 2 WHERE id = 'orphan-note'",
            {},
        )

        results = svc.find_archival_candidates(kb, min_age_days=180)
        candidate_ids = [r["entry_id"] for r in results]
        assert "orphan-note" in candidate_ids


class TestStalenessQARule:
    """Test staleness integrated into validate_kb via _check_staleness."""

    def test_staleness_in_validate_kb(self, qa_setup):
        """validate_kb includes staleness warnings when check_staleness=True."""
        db, svc, kb = qa_setup["db"], qa_setup["svc"], qa_setup["kb_name"]

        old_date = datetime.now(UTC) - timedelta(days=200)
        _insert_entry(db, "old-note", kb, "note", "Old Note", old_date)

        result = svc.validate_kb(kb, check_staleness=True, staleness_days=90, readable_kbs=UNSCOPED)
        stale_issues = [i for i in result["issues"] if i["rule"] == "stale_entry"]
        assert len(stale_issues) == 1
        assert stale_issues[0]["severity"] == "info"
        assert stale_issues[0]["entry_id"] == "old-note"

    def test_staleness_off_by_default(self, qa_setup):
        """validate_kb does NOT check staleness by default."""
        db, svc, kb = qa_setup["db"], qa_setup["svc"], qa_setup["kb_name"]

        old_date = datetime.now(UTC) - timedelta(days=200)
        _insert_entry(db, "old-note", kb, "note", "Old Note", old_date)

        result = svc.validate_kb(kb, readable_kbs=UNSCOPED)
        stale_issues = [i for i in result["issues"] if i["rule"] == "stale_entry"]
        assert len(stale_issues) == 0
