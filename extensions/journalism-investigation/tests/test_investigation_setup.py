"""Tests for investigation guided setup and status reporting."""

import pytest
from pyrite_journalism_investigation.investigation_setup import (
    build_investigation_status,
    create_investigation,
)

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB

KB_NAME = "test-investigation"


@pytest.fixture
def ji_svc(tmp_path):
    """A KBService over an empty investigation KB: creates go through it."""
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB_NAME, path=kb_path, kb_type="journalism-investigation")],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(tmp_path / "index.db")
    db.register_kb(KB_NAME, "journalism-investigation", str(kb_path))
    yield KBService(config, db)
    db.close()


@pytest.fixture
def ji_db(ji_svc):
    """Fresh DB with an empty investigation KB."""
    return ji_svc.db, KB_NAME


@pytest.fixture
def db_with_investigation(ji_db):
    """DB with a populated investigation KB."""
    db, kb_name = ji_db

    # Create an investigation note entry
    db.upsert_entry(
        {
            "id": "investigation-acme-fraud",
            "kb_name": kb_name,
            "title": "ACME Corp Fraud Investigation",
            "entry_type": "note",
            "body": "Investigation into ACME Corp financial irregularities",
            "importance": 8,
            "tags": ["investigation", "fraud", "acme"],
            "metadata": {
                "investigation_status": "active",
                "key_questions": ["Where did the money go?"],
            },
        }
    )

    # Create some entities
    db.upsert_entry(
        {
            "id": "john-smith",
            "kb_name": kb_name,
            "title": "John Smith",
            "entry_type": "person",
            "importance": 7,
            "tags": ["ceo", "acme"],
        }
    )
    db.upsert_entry(
        {
            "id": "acme-corp",
            "kb_name": kb_name,
            "title": "ACME Corporation",
            "entry_type": "organization",
            "importance": 8,
            "tags": ["company", "target"],
        }
    )

    # Create events
    db.upsert_entry(
        {
            "id": "event-wire-transfer",
            "kb_name": kb_name,
            "title": "Suspicious wire transfer",
            "entry_type": "transaction",
            "date": "2025-06-15",
            "importance": 9,
            "tags": ["financial"],
            "metadata": {"actors": ["John Smith"], "sender": "ACME Corp", "receiver": "Shell LLC"},
        }
    )
    db.upsert_entry(
        {
            "id": "event-foia-response",
            "kb_name": kb_name,
            "title": "FOIA response received",
            "entry_type": "investigation_event",
            "date": "2025-08-01",
            "importance": 6,
            "tags": ["foia"],
            "metadata": {"actors": ["EPA"]},
        }
    )

    # Create claims at different stages
    db.upsert_entry(
        {
            "id": "claim-embezzlement",
            "kb_name": kb_name,
            "title": "Embezzlement by CEO",
            "entry_type": "claim",
            "importance": 9,
            "tags": ["fraud"],
            "metadata": {
                "assertion": "John Smith embezzled $2M from ACME Corp",
                "claim_status": "partially_verified",
                "confidence": "medium",
                "evidence_refs": ["evidence-bank-records"],
            },
        }
    )
    db.upsert_entry(
        {
            "id": "claim-shell-company",
            "kb_name": kb_name,
            "title": "Shell company ownership",
            "entry_type": "claim",
            "importance": 7,
            "tags": ["fraud"],
            "metadata": {
                "assertion": "Shell LLC is owned by Smith's spouse",
                "claim_status": "unverified",
                "confidence": "low",
            },
        }
    )

    # Create a source
    db.upsert_entry(
        {
            "id": "source-bank-records",
            "kb_name": kb_name,
            "title": "Bank records subpoena response",
            "entry_type": "document_source",
            "importance": 8,
            "tags": ["financial"],
            "metadata": {"reliability": "high", "classification": "court_filing"},
        }
    )

    return db, kb_name


class TestCreateInvestigation:
    """Test creating a new investigation."""

    def test_creates_investigation_entry(self, ji_svc, ji_db):
        db, kb_name = ji_db
        result = create_investigation(
            ji_svc,
            kb_name=kb_name,
            title="New Investigation",
            scope="Looking into XYZ Corp",
            key_questions=["Who is involved?", "Where did the money go?"],
        )
        assert "created" in result
        assert result["title"] == "New Investigation"
        # Entry should exist in DB
        entry = db.get_entry(result["created"], kb_name)
        assert entry is not None
        assert entry["title"] == "New Investigation"

    def test_creates_initial_entities(self, ji_svc, ji_db):
        db, kb_name = ji_db
        result = create_investigation(
            ji_svc,
            kb_name=kb_name,
            title="Corp Investigation",
            scope="Corporate fraud",
            initial_entities=[
                {"name": "Jane Doe", "type": "person"},
                {"name": "BigCo Inc", "type": "organization"},
            ],
        )
        assert len(result.get("entities_created", [])) == 2

    def test_returns_investigation_id(self, ji_svc, ji_db):
        db, kb_name = ji_db
        result = create_investigation(
            ji_svc,
            kb_name=kb_name,
            title="Test Investigation",
            scope="Test scope",
        )
        assert result["created"]
        assert isinstance(result["created"], str)
        assert len(result["created"]) > 0

    def test_key_questions_stored(self, ji_svc, ji_db):
        db, kb_name = ji_db
        questions = ["Question 1?", "Question 2?"]
        result = create_investigation(
            ji_svc,
            kb_name=kb_name,
            title="Questions Test",
            scope="Scope",
            key_questions=questions,
        )
        entry = db.get_entry(result["created"], kb_name)
        assert entry is not None
        # Key questions should be in body
        body = entry.get("body", "")
        assert "Question 1?" in body
        assert "Question 2?" in body

    @pytest.mark.control(reason="refused or read before any write; unchanged by this change")
    def test_empty_title_returns_error(self, ji_svc, ji_db):
        db, kb_name = ji_db
        result = create_investigation(
            ji_svc,
            kb_name=kb_name,
            title="",
            scope="Some scope",
        )
        assert "error" in result


class TestBuildInvestigationStatus:
    """Test building investigation status reports."""

    def test_returns_overview(self, db_with_investigation):
        db, kb_name = db_with_investigation
        status = build_investigation_status(db, kb_name)
        assert "summary" in status
        assert "entity_count" in status
        assert "event_count" in status
        assert "claim_count" in status

    def test_counts_entities(self, db_with_investigation):
        db, kb_name = db_with_investigation
        status = build_investigation_status(db, kb_name)
        assert status["entity_count"] >= 2  # john-smith, acme-corp

    def test_counts_events(self, db_with_investigation):
        db, kb_name = db_with_investigation
        status = build_investigation_status(db, kb_name)
        assert status["event_count"] >= 2

    def test_counts_claims_by_status(self, db_with_investigation):
        db, kb_name = db_with_investigation
        status = build_investigation_status(db, kb_name)
        assert "claim_breakdown" in status
        assert status["claim_breakdown"].get("unverified", 0) >= 1
        assert status["claim_breakdown"].get("partially_verified", 0) >= 1

    def test_identifies_unverified_claims(self, db_with_investigation):
        db, kb_name = db_with_investigation
        status = build_investigation_status(db, kb_name)
        assert "unverified_claims" in status
        unverified_ids = [c["id"] for c in status["unverified_claims"]]
        assert "claim-shell-company" in unverified_ids

    def test_source_count(self, db_with_investigation):
        db, kb_name = db_with_investigation
        status = build_investigation_status(db, kb_name)
        assert status.get("source_count", 0) >= 1

    def test_summary_text(self, db_with_investigation):
        db, kb_name = db_with_investigation
        status = build_investigation_status(db, kb_name)
        # Summary should mention key counts
        summary = status["summary"]
        assert isinstance(summary, str)
        assert len(summary) > 0

    @pytest.mark.control(reason="refused or read before any write; unchanged by this change")
    def test_empty_kb_returns_status(self, ji_svc, ji_db):
        db, kb_name = ji_db
        status = build_investigation_status(db, kb_name)
        assert status["entity_count"] == 0
        assert status["event_count"] == 0
        assert status["claim_count"] == 0
