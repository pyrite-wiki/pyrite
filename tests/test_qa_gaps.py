"""
Tests for QAService.analyze_gaps() — structural coverage gap analysis.
"""

import json
import tempfile
from pathlib import Path

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models import EventEntry, NoteEntry
from pyrite.schema.core_types import CORE_TYPES
from pyrite.schema.kb_schema import KBSchema
from pyrite.services.qa_service import QAService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository


@pytest.fixture
def gaps_setup():
    """Create a QAService with seeded test data for gap analysis."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"

        kb_path = tmpdir / "test-kb"
        kb_path.mkdir()
        (kb_path / "notes").mkdir()
        (kb_path / "events").mkdir()

        # Write a kb.yaml with custom types
        kb_yaml = kb_path / "kb.yaml"
        kb_yaml.write_text(
            "name: test-kb\n"
            "description: Test KB for gap analysis\n"
            "guidelines:\n"
            "  tagging: 'Entries should use #important and #review tags'\n"
            "goals:\n"
            "  coverage: 'All #core topics should be covered'\n"
            "types:\n"
            "  note:\n"
            "    description: A note\n"
            "  event:\n"
            "    description: An event\n"
            "  custom_report:\n"
            "    description: A custom report type\n"
        )

        kb = KBConfig(
            name="test-kb",
            path=kb_path,
            kb_type=KBType.RESEARCH,
            description="Test KB",
        )

        config = PyriteConfig(
            knowledge_bases=[kb],
            settings=Settings(index_path=db_path),
        )

        repo = KBRepository(kb)

        # Create some notes (no links between them)
        note1 = NoteEntry(
            id="alpha-note",
            title="Alpha Note",
            body="First note content.",
            tags=["important"],
            importance=8,
        )
        repo.save(note1)

        note2 = NoteEntry(
            id="beta-note",
            title="Beta Note",
            body="Second note content.",
            tags=["review"],
            importance=3,
        )
        repo.save(note2)

        # Create one event
        event1 = EventEntry(
            id="launch-event",
            date="2025-06-15",
            title="Launch Event",
            body="Event body.",
            tags=["important"],
            importance=9,
        )
        repo.save(event1)

        db = PyriteDB(db_path)
        index_mgr = IndexManager(db, config)
        index_mgr.index_all()

        qa = QAService(config, db)

        yield {
            "qa": qa,
            "config": config,
            "db": db,
            "kb": kb,
            "repo": repo,
            "index_mgr": index_mgr,
        }

        db.close()


class TestAnalyzeGaps:
    """Tests for the analyze_gaps() method."""

    def test_returns_kb_not_found_on_invalid_kb(self, gaps_setup):
        qa = gaps_setup["qa"]
        result = qa.analyze_gaps("nonexistent-kb")
        assert "error" in result
        assert "not found" in result["error"]

    def test_reports_empty_types(self, gaps_setup):
        """custom_report is declared in kb.yaml but has 0 entries."""
        qa = gaps_setup["qa"]
        result = qa.analyze_gaps("test-kb")
        assert "custom_report" in result["empty_types"]

    def test_reports_sparse_types(self, gaps_setup):
        """event has 1 entry, which is below default threshold of 3."""
        qa = gaps_setup["qa"]
        result = qa.analyze_gaps("test-kb", threshold=3)
        sparse_type_names = [s["type"] for s in result["sparse_types"]]
        assert "event" in sparse_type_names

    def test_custom_threshold(self, gaps_setup):
        """With threshold=1, event (count=1) should not be sparse."""
        qa = gaps_setup["qa"]
        result = qa.analyze_gaps("test-kb", threshold=1)
        sparse_type_names = [s["type"] for s in result["sparse_types"]]
        assert "event" not in sparse_type_names

    def test_no_outlinks_detected(self, gaps_setup):
        """All entries have no outbound links since none were created."""
        qa = gaps_setup["qa"]
        result = qa.analyze_gaps("test-kb")
        # All 3 entries should appear
        assert len(result["no_outlinks"]) >= 3

    def test_no_inlinks_detected(self, gaps_setup):
        """All entries have no inbound links since none were created."""
        qa = gaps_setup["qa"]
        result = qa.analyze_gaps("test-kb")
        assert len(result["no_inlinks"]) >= 3

    def test_entries_per_type_distribution(self, gaps_setup):
        qa = gaps_setup["qa"]
        result = qa.analyze_gaps("test-kb")
        dist = result["distribution"]["entries_per_type"]
        assert dist.get("note") == 2
        assert dist.get("event") == 1

    def test_importance_distribution(self, gaps_setup):
        qa = gaps_setup["qa"]
        result = qa.analyze_gaps("test-kb")
        importance = result["distribution"]["importance"]
        # We set importance 8, 3, and 9
        assert importance.get("8") == 1
        assert importance.get("3") == 1
        assert importance.get("9") == 1

    def test_tag_distribution(self, gaps_setup):
        qa = gaps_setup["qa"]
        result = qa.analyze_gaps("test-kb")
        tag_dist = result["distribution"]["top_tags"]
        tag_names = [t["tag"] for t in tag_dist]
        assert "important" in tag_names
        assert "review" in tag_names

    def test_unused_tags_from_schema(self, gaps_setup):
        """Tags mentioned in guidelines (#core) with 0 entries."""
        qa = gaps_setup["qa"]
        result = qa.analyze_gaps("test-kb")
        # #core is mentioned in goals but no entry uses it
        assert "core" in result["unused_tags"]

    def test_total_entries(self, gaps_setup):
        qa = gaps_setup["qa"]
        result = qa.analyze_gaps("test-kb")
        assert result["total_entries"] == 3

    def test_json_output_is_serializable(self, gaps_setup):
        qa = gaps_setup["qa"]
        result = qa.analyze_gaps("test-kb")
        # Should not raise
        serialized = json.dumps(result)
        parsed = json.loads(serialized)
        assert parsed["kb_name"] == "test-kb"


class TestExtractTagsFromSchema:
    """Tests for _extract_tags_from_schema static method."""

    def test_extracts_hashtag_tokens(self):
        schema = KBSchema(
            guidelines={"tagging": "Use #alpha and #beta tags"},
            goals={"coverage": "Cover #gamma topics"},
        )
        tags = QAService._extract_tags_from_schema(schema)
        assert tags == {"alpha", "beta", "gamma"}

    def test_empty_schema_returns_empty(self):
        tags = QAService._extract_tags_from_schema(None)
        assert tags == set()

    def test_no_hashtags_returns_empty(self):
        schema = KBSchema(
            guidelines={"note": "No tags here"},
        )
        tags = QAService._extract_tags_from_schema(schema)
        assert tags == set()


def test_software_template_gap_report_only_lists_declared_core_types(tmp_path):
    kb_path = tmp_path / "software"
    kb_path.mkdir()
    template_yaml = Path(__file__).resolve().parents[1] / "kb" / "kb.yaml"
    (kb_path / "kb.yaml").write_text(template_yaml.read_text(encoding="utf-8"), encoding="utf-8")
    kb = KBConfig(name="software", path=kb_path, kb_type="software")
    config = PyriteConfig(
        knowledge_bases=[kb],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        declared_types = set(kb.kb_schema.declared_types())
        undeclared_core_types = set(CORE_TYPES) - declared_types
        assert undeclared_core_types

        result = QAService(config, db).analyze_gaps("software")

        assert set(result["empty_types"]) == declared_types
        assert not (set(result["empty_types"]) & undeclared_core_types)
    finally:
        db.close()


def test_untyped_qa_gap_analysis_falls_back_to_all_core_types(tmp_path):
    kb_path = tmp_path / "plain"
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text("name: plain\n", encoding="utf-8")
    kb = KBConfig(name="plain", path=kb_path, kb_type="generic")
    config = PyriteConfig(
        knowledge_bases=[kb],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        assert kb.kb_schema.declared_types() == []
        result = QAService(config, db).analyze_gaps("plain")
        assert set(result["empty_types"]) == set(CORE_TYPES)
    finally:
        db.close()
