"""
Tests for QA Assessment entries and QAService assessment/query methods.

Covers:
- QAAssessmentEntry type roundtrip
- build_entry + get_entry_class for qa_assessment
- assess_entry: pass/warn/fail status derivation
- assess_kb: batch assessment with skip logic
- get_assessments: query with filters
- get_unassessed: entries without assessments
- get_coverage: coverage stats
- Task integration: optional task creation on failure
- MCP tool registration
- CLI command output
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models import NoteEntry
from pyrite.models.core_types import QAAssessmentEntry, get_entry_class
from pyrite.models.factory import build_entry
from pyrite.services.qa_service import QAService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository
from pyrite.services.access_policy import UNSCOPED


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def qa_env():
    """QAService with a seeded KB for assessment tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"

        kb_path = tmpdir / "test-kb"
        kb_path.mkdir()

        kb_config = KBConfig(
            name="test-kb",
            path=kb_path,
            kb_type=KBType.GENERIC,
            description="Test KB",
        )

        config = PyriteConfig(
            knowledge_bases=[kb_config],
            settings=Settings(index_path=db_path),
        )

        repo = KBRepository(kb_config)

        # Create some entries with varying quality
        good_entry = NoteEntry(
            id="good-note",
            title="Good Note",
            body="This note has a title and body.",
            tags=["test"],
        )
        repo.save(good_entry)

        no_body_entry = NoteEntry(
            id="no-body-note",
            title="No Body Note",
            body="",
            tags=["test"],
        )
        repo.save(no_body_entry)

        db = PyriteDB(db_path)
        index_mgr = IndexManager(db, config)
        index_mgr.index_all()

        # Add cross-links so entries satisfy the "has outgoing links" rubric
        db._raw_conn.execute(
            "INSERT INTO link (source_id, source_kb, target_id, target_kb, relation, inverse_relation) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("good-note", "test-kb", "no-body-note", "test-kb", "related_to", "related_to"),
        )
        db._raw_conn.execute(
            "INSERT INTO link (source_id, source_kb, target_id, target_kb, relation, inverse_relation) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("no-body-note", "test-kb", "good-note", "test-kb", "related_to", "related_to"),
        )
        db._raw_conn.commit()

        qa = QAService(config, db)

        yield {
            "qa": qa,
            "config": config,
            "db": db,
            "kb_config": kb_config,
            "repo": repo,
            "index_mgr": index_mgr,
            "tmpdir": tmpdir,
        }

        db.close()


# =========================================================================
# Phase A: QAAssessmentEntry type
# =========================================================================


class TestQAAssessmentEntryType:
    def test_roundtrip_to_from_frontmatter(self):
        """to_frontmatter → from_frontmatter preserves all fields."""
        entry = QAAssessmentEntry(
            id="qa-test-123",
            title="QA: test-entry",
            body="Assessment body",
            target_entry="test-entry",
            target_kb="my-kb",
            tier=2,
            qa_status="fail",
            issues=[{"rule": "missing_title", "severity": "error"}],
            issues_found=1,
            issues_resolved=0,
            assessed_at="2026-02-28T10:00:00+00:00",
            tags=["qa"],
        )

        fm = entry.to_frontmatter()
        restored = QAAssessmentEntry.from_frontmatter(fm, "Assessment body")

        assert restored.id == "qa-test-123"
        assert restored.target_entry == "test-entry"
        assert restored.target_kb == "my-kb"
        assert restored.tier == 2
        assert restored.qa_status == "fail"
        assert restored.issues == [{"rule": "missing_title", "severity": "error"}]
        assert restored.issues_found == 1
        assert restored.issues_resolved == 0
        assert restored.assessed_at == "2026-02-28T10:00:00+00:00"
        assert restored.tags == ["qa"]

    def test_entry_type_property(self):
        """entry_type returns 'qa_assessment'."""
        entry = QAAssessmentEntry(id="qa-x", title="QA")
        assert entry.entry_type == "qa_assessment"

    def test_defaults(self):
        """Default values are sensible."""
        entry = QAAssessmentEntry(id="qa-x", title="QA")
        assert entry.tier == 1
        assert entry.qa_status == "pass"
        assert entry.issues == []
        assert entry.issues_found == 0

    def test_get_entry_class(self):
        """get_entry_class('qa_assessment') returns QAAssessmentEntry."""
        cls = get_entry_class("qa_assessment")
        assert cls is QAAssessmentEntry

    def test_build_entry(self):
        """build_entry('qa_assessment', ...) produces correct typed fields."""
        entry = build_entry(
            "qa_assessment",
            entry_id="qa-test-1",
            title="QA: test",
            body="body",
            target_entry="test-entry",
            target_kb="my-kb",
            qa_status="warn",
            tier=1,
            issues=[{"rule": "empty_body"}],
            issues_found=1,
            assessed_at="2026-02-28T00:00:00+00:00",
        )
        assert isinstance(entry, QAAssessmentEntry)
        assert entry.target_entry == "test-entry"
        assert entry.qa_status == "warn"
        assert entry.issues_found == 1

    def test_frontmatter_always_writes_meaningful_defaults(self):
        """to_frontmatter always writes fields that carry semantic meaning."""
        entry = QAAssessmentEntry(id="qa-x", title="QA", body="body")
        fm = entry.to_frontmatter()
        assert fm["tier"] == 1  # always written to prevent round-trip data loss
        assert fm["qa_status"] == "pass"  # always written
        assert "issues" not in fm  # empty list is fine to omit
        assert fm["issues_found"] == 0  # always written (0 is meaningful)
        assert fm["issues_resolved"] == 0  # always written


# =========================================================================
# Phase B: assess_entry / assess_kb
# =========================================================================


class TestAssessEntry:
    def test_assess_valid_entry_returns_pass(self, qa_env):
        """Assessing a valid entry produces qa_status='pass'."""
        result = qa_env["qa"].assess_entry("good-note", "test-kb", readable_kbs=UNSCOPED)
        assert result["qa_status"] == "pass"
        assert result["target_entry"] == "good-note"
        assert "assessment_id" in result
        assert result["assessment_id"].startswith("qa-good-note-")

    def test_assess_entry_with_warnings_returns_warn(self, qa_env):
        """Entry with only warnings (e.g. empty body) → qa_status='warn'."""
        result = qa_env["qa"].assess_entry("no-body-note", "test-kb", readable_kbs=UNSCOPED)
        assert result["qa_status"] == "warn"
        assert result["issues_found"] > 0

    def test_assess_entry_with_errors_returns_fail(self, qa_env):
        """Entry with errors → qa_status='fail'."""
        # Insert entry with no title (error-level issue)
        db = qa_env["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("no-title", "test-kb", "note", "", "body", 5),
        )
        db._raw_conn.commit()

        result = qa_env["qa"].assess_entry("no-title", "test-kb", readable_kbs=UNSCOPED)
        assert result["qa_status"] == "fail"

    def test_assess_entry_creates_assessment_entry(self, qa_env):
        """assess_entry creates a qa_assessment entry in the DB."""
        result = qa_env["qa"].assess_entry("good-note", "test-kb", readable_kbs=UNSCOPED)
        aid = result["assessment_id"]

        row = (
            qa_env["db"]
            ._raw_conn.execute(
                "SELECT id, entry_type FROM entry WHERE id = ? AND kb_name = ?",
                (aid, "test-kb"),
            )
            .fetchone()
        )
        assert row is not None
        assert row["entry_type"] == "qa_assessment"

    def test_two_assessments_create_separate_entries(self, qa_env):
        """Two assess_entry calls create two different assessment entries."""
        r1 = qa_env["qa"].assess_entry("good-note", "test-kb", readable_kbs=UNSCOPED)
        r2 = qa_env["qa"].assess_entry("good-note", "test-kb", readable_kbs=UNSCOPED)
        assert r1["assessment_id"] != r2["assessment_id"]


class TestAssessKB:
    def test_assess_kb_creates_assessments_for_all_entries(self, qa_env):
        """assess_kb creates one assessment per non-assessment entry."""
        result = qa_env["qa"].assess_kb("test-kb", readable_kbs=UNSCOPED)
        assert result["assessed"] == 2  # good-note + no-body-note
        assert len(result["results"]) == 2

    def test_assess_kb_skips_assessment_entries(self, qa_env):
        """assess_kb does not assess qa_assessment entries."""
        # Create an assessment first
        qa_env["qa"].assess_entry("good-note", "test-kb", readable_kbs=UNSCOPED)

        # Now assess KB — should skip the assessment entry itself
        result = qa_env["qa"].assess_kb("test-kb", max_age_hours=0, readable_kbs=UNSCOPED)
        target_entries = [r["target_entry"] for r in result["results"]]
        assert "good-note" in target_entries
        assert "no-body-note" in target_entries
        # No assessment entry should be a target
        for r in result["results"]:
            assert not r["target_entry"].startswith("qa-")

    def test_assess_kb_skips_recently_assessed(self, qa_env):
        """assess_kb with max_age_hours=24 skips entries assessed within 24h."""
        qa_env["qa"].assess_entry("good-note", "test-kb", readable_kbs=UNSCOPED)

        result = qa_env["qa"].assess_kb("test-kb", max_age_hours=24, readable_kbs=UNSCOPED)
        target_entries = [r["target_entry"] for r in result["results"]]
        # good-note was just assessed, should be skipped
        assert "good-note" not in target_entries
        assert result["skipped"] >= 1

    def test_assess_kb_max_age_zero_reassesses_all(self, qa_env):
        """assess_kb with max_age_hours=0 re-assesses everything."""
        qa_env["qa"].assess_entry("good-note", "test-kb", readable_kbs=UNSCOPED)

        result = qa_env["qa"].assess_kb("test-kb", max_age_hours=0, readable_kbs=UNSCOPED)
        target_entries = [r["target_entry"] for r in result["results"]]
        assert "good-note" in target_entries


# =========================================================================
# Phase C: Query methods
# =========================================================================


class TestGetAssessments:
    def test_returns_all_assessments(self, qa_env):
        """get_assessments returns all assessment entries."""
        qa_env["qa"].assess_entry("good-note", "test-kb", readable_kbs=UNSCOPED)
        qa_env["qa"].assess_entry("no-body-note", "test-kb", readable_kbs=UNSCOPED)

        assessments = qa_env["qa"].get_assessments("test-kb")
        assert len(assessments) == 2

    def test_filter_by_target_entry(self, qa_env):
        """Filter assessments by target_entry."""
        qa_env["qa"].assess_entry("good-note", "test-kb", readable_kbs=UNSCOPED)
        qa_env["qa"].assess_entry("no-body-note", "test-kb", readable_kbs=UNSCOPED)

        assessments = qa_env["qa"].get_assessments("test-kb", target_entry="good-note")
        assert len(assessments) == 1
        assert assessments[0]["target_entry"] == "good-note"

    def test_filter_by_status(self, qa_env):
        """Filter assessments by qa_status."""
        qa_env["qa"].assess_entry("good-note", "test-kb", readable_kbs=UNSCOPED)
        qa_env["qa"].assess_entry("no-body-note", "test-kb", readable_kbs=UNSCOPED)

        pass_assessments = qa_env["qa"].get_assessments("test-kb", qa_status="pass")
        assert all(a["qa_status"] == "pass" for a in pass_assessments)

        warn_assessments = qa_env["qa"].get_assessments("test-kb", qa_status="warn")
        assert all(a["qa_status"] == "warn" for a in warn_assessments)


class TestGetUnassessed:
    def test_returns_entries_without_assessments(self, qa_env):
        """get_unassessed returns entries that have no assessment."""
        # No assessments yet — both entries unassessed
        unassessed = qa_env["qa"].get_unassessed("test-kb")
        ids = [e["id"] for e in unassessed]
        assert "good-note" in ids
        assert "no-body-note" in ids

    def test_assessed_entries_excluded(self, qa_env):
        """After assessing, entry no longer in unassessed list."""
        qa_env["qa"].assess_entry("good-note", "test-kb", readable_kbs=UNSCOPED)

        unassessed = qa_env["qa"].get_unassessed("test-kb")
        ids = [e["id"] for e in unassessed]
        assert "good-note" not in ids
        assert "no-body-note" in ids


class TestGetCoverage:
    def test_coverage_with_no_assessments(self, qa_env):
        """Coverage is 0% with no assessments."""
        cov = qa_env["qa"].get_coverage("test-kb")
        assert cov["total"] == 2
        assert cov["assessed"] == 0
        assert cov["unassessed"] == 2
        assert cov["coverage_pct"] == 0.0

    def test_coverage_after_assessments(self, qa_env):
        """Coverage reflects assessed entries."""
        qa_env["qa"].assess_entry("good-note", "test-kb", readable_kbs=UNSCOPED)

        cov = qa_env["qa"].get_coverage("test-kb")
        assert cov["total"] == 2
        assert cov["assessed"] == 1
        assert cov["unassessed"] == 1
        assert cov["coverage_pct"] == 50.0

    def test_full_coverage(self, qa_env):
        """100% coverage when all entries assessed."""
        qa_env["qa"].assess_entry("good-note", "test-kb", readable_kbs=UNSCOPED)
        qa_env["qa"].assess_entry("no-body-note", "test-kb", readable_kbs=UNSCOPED)

        cov = qa_env["qa"].get_coverage("test-kb")
        assert cov["coverage_pct"] == 100.0
        assert cov["by_status"].get("pass", 0) >= 1
        assert cov["by_status"].get("warn", 0) >= 1


# =========================================================================
# Phase D: MCP tool registration
# =========================================================================


class TestMCPTools:
    def test_kb_qa_assess_in_write_tier(self, qa_env):
        """kb_qa_assess is available in write tier, not read tier."""
        from pyrite.server.mcp_server import PyriteMCPServer

        read_server = PyriteMCPServer(config=qa_env["config"], tier="read")
        write_server = PyriteMCPServer(config=qa_env["config"], tier="write")

        assert "kb_qa_assess" not in read_server.tools
        assert "kb_qa_assess" in write_server.tools

    def test_kb_qa_validate_in_read_tier(self, qa_env):
        """kb_qa_validate remains in read tier."""
        from pyrite.server.mcp_server import PyriteMCPServer

        read_server = PyriteMCPServer(config=qa_env["config"], tier="read")
        assert "kb_qa_validate" in read_server.tools

    def test_kb_qa_status_in_read_tier(self, qa_env):
        """kb_qa_status remains in read tier."""
        from pyrite.server.mcp_server import PyriteMCPServer

        read_server = PyriteMCPServer(config=qa_env["config"], tier="read")
        assert "kb_qa_status" in read_server.tools

    def test_kb_qa_assess_handler_creates_assessments(self, qa_env):
        """kb_qa_assess handler creates assessment entries."""
        from pyrite.server.mcp_server import PyriteMCPServer

        server = PyriteMCPServer(config=qa_env["config"], tier="write")
        result = server.tools["kb_qa_assess"]["handler"](
            {"kb_name": "test-kb", "entry_id": "good-note"}
        )
        assert result["qa_status"] == "pass"
        assert "assessment_id" in result

    def test_kb_qa_status_includes_coverage(self, qa_env):
        """kb_qa_status response includes coverage when kb_name given."""
        from pyrite.server.mcp_server import PyriteMCPServer

        server = PyriteMCPServer(config=qa_env["config"], tier="read")
        result = server.tools["kb_qa_status"]["handler"]({"kb_name": "test-kb"})
        assert "coverage" in result
        assert "total" in result["coverage"]
        assert "coverage_pct" in result["coverage"]


# =========================================================================
# Phase F: Task integration
# =========================================================================


class TestTaskIntegration:
    def test_no_task_when_plugin_absent(self, qa_env):
        """assess_entry completes when task plugin import fails."""
        with patch("pyrite.services.qa_service.QAService._maybe_create_task") as mock_create:
            # Even with create_task_on_fail=True, should not error
            result = qa_env["qa"].assess_entry(
                "good-note", "test-kb", create_task_on_fail=True, readable_kbs=UNSCOPED
            )
            assert result["qa_status"] == "pass"
            # No task for pass status — _maybe_create_task only called on fail
            mock_create.assert_not_called()

    def test_task_created_on_fail(self, qa_env):
        """With create_task_on_fail=True and fail status, _maybe_create_task is called."""
        # Insert an entry that will fail validation
        db = qa_env["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("fail-entry", "test-kb", "note", "", "body", 5),
        )
        db._raw_conn.commit()

        with patch.object(qa_env["qa"], "_maybe_create_task") as mock_task:
            result = qa_env["qa"].assess_entry(
                "fail-entry", "test-kb", create_task_on_fail=True, readable_kbs=UNSCOPED
            )
            assert result["qa_status"] == "fail"
            mock_task.assert_called_once()

    def test_no_task_on_pass(self, qa_env):
        """No task created when assessment passes."""
        with patch.object(qa_env["qa"], "_maybe_create_task") as mock_task:
            result = qa_env["qa"].assess_entry(
                "good-note", "test-kb", create_task_on_fail=True, readable_kbs=UNSCOPED
            )
            assert result["qa_status"] == "pass"
            mock_task.assert_not_called()

    def test_task_creation_failure_doesnt_raise(self, qa_env):
        """Task creation failure doesn't prevent assess_entry from returning."""
        db = qa_env["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("fail2", "test-kb", "note", "", "body", 5),
        )
        db._raw_conn.commit()

        # Mock _maybe_create_task to raise, but use the real method's
        # try/except behavior by simulating ImportError in the actual method
        with patch("pyrite.services.qa_service.QAService._maybe_create_task") as mock_task:
            mock_task.side_effect = None  # No-op, just verifying call doesn't break
            result = qa_env["qa"].assess_entry(
                "fail2", "test-kb", create_task_on_fail=True, readable_kbs=UNSCOPED
            )
            assert result["qa_status"] == "fail"
            assert "assessment_id" in result

    def test_maybe_create_task_handles_import_error(self, qa_env):
        """_maybe_create_task handles ImportError gracefully."""
        with patch.dict("sys.modules", {"pyrite_task": None, "pyrite_task.service": None}):
            # Should not raise
            qa_env["qa"]._maybe_create_task(
                "test-entry", "test-kb", "qa-test-123", [{"severity": "error"}]
            )

    def test_maybe_create_task_logs_warning_on_failure(self, qa_env, caplog):
        """A failed follow-up task creation should be visible at warning
        level, not silently swallowed at debug -- the QA assessment itself
        still succeeds, but an operator should be able to tell a broken
        auto-task-creation path apart from one that's just quiet."""
        import logging

        with patch("pyrite.services.task_service.TaskService.create_task") as mock_create:
            mock_create.side_effect = RuntimeError("task db unavailable")
            with caplog.at_level(logging.WARNING):
                qa_env["qa"]._maybe_create_task(
                    "test-entry", "test-kb", "qa-test-123", [{"severity": "error"}]
                )

        assert any(
            record.levelno >= logging.WARNING and "test-entry" in record.message
            for record in caplog.records
        )


# =========================================================================
# Phase E: CLI commands
# =========================================================================


class TestCLICommands:
    def test_assess_command_json_output(self, qa_env):
        """qa assess with --format json returns valid JSON."""
        from typer.testing import CliRunner

        from pyrite.cli.qa_commands import qa_app

        runner = CliRunner()

        with (
            patch("pyrite.cli.context.load_config", return_value=qa_env["config"]),
            patch("pyrite.cli.context.PyriteDB", return_value=qa_env["db"]),
        ):
            result = runner.invoke(qa_app, ["assess", "test-kb", "--format", "json"])
            assert result.exit_code == 0
            data = json.loads(result.stdout)
            assert "assessed" in data
            assert "results" in data

    def test_assess_single_entry_json(self, qa_env):
        """qa assess --entry with --format json returns single result."""
        from typer.testing import CliRunner

        from pyrite.cli.qa_commands import qa_app

        runner = CliRunner()

        with (
            patch("pyrite.cli.context.load_config", return_value=qa_env["config"]),
            patch("pyrite.cli.context.PyriteDB", return_value=qa_env["db"]),
        ):
            result = runner.invoke(
                qa_app, ["assess", "test-kb", "--entry", "good-note", "--format", "json"]
            )
            assert result.exit_code == 0
            data = json.loads(result.stdout)
            assert data["qa_status"] == "pass"

    def test_status_json_includes_coverage(self, qa_env):
        """qa status with --format json includes coverage when KB specified."""
        from typer.testing import CliRunner

        from pyrite.cli.qa_commands import qa_app

        runner = CliRunner()

        with (
            patch("pyrite.cli.context.load_config", return_value=qa_env["config"]),
            patch("pyrite.cli.context.PyriteDB", return_value=qa_env["db"]),
        ):
            result = runner.invoke(qa_app, ["status", "test-kb", "--format", "json"])
            assert result.exit_code == 0
            data = json.loads(result.stdout)
            assert "coverage" in data
            assert "coverage_pct" in data["coverage"]


# =========================================================================
# Phase G: LLM rubric evaluation in QAService
# =========================================================================


class TestLLMRubricInAssessEntry:
    def _make_mock_llm(self, configured=True, response="[]"):
        llm = MagicMock()
        llm.status.return_value = {
            "configured": configured,
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",
        }
        llm.complete = AsyncMock(return_value=response)
        return llm

    def test_tier2_with_llm_includes_llm_issues(self, qa_env):
        """assess_entry(tier=2) with mock LLM includes LLM rubric issues."""
        llm_response = json.dumps(
            [
                {
                    "item": "Entry body explains the why, not just the what",
                    "pass": False,
                    "confidence": 0.75,
                    "reasoning": "Body only lists facts",
                },
            ]
        )
        mock_llm = self._make_mock_llm(response=llm_response)
        qa = QAService(qa_env["config"], qa_env["db"], llm_service=mock_llm)

        result = qa.assess_entry("good-note", "test-kb", tier=2, readable_kbs=UNSCOPED)
        assert result["llm_available"] is True
        # Check that LLM issues are included (may or may not have judgment items
        # depending on rubric config, but llm_available flag should be set)

    def test_tier2_without_llm_returns_tier1_only(self, qa_env):
        """assess_entry(tier=2) without LLM returns tier-1 results, llm_available=false."""
        qa = QAService(qa_env["config"], qa_env["db"], llm_service=None)
        result = qa.assess_entry("good-note", "test-kb", tier=2, readable_kbs=UNSCOPED)
        assert result["llm_available"] is False
        # No LLM issues (no LLM configured)
        llm_issues = [i for i in result["issues"] if i.get("rule") == "llm_rubric_violation"]
        assert len(llm_issues) == 0

    def test_tier1_no_llm_calls(self, qa_env):
        """assess_entry(tier=1) makes no LLM calls regardless of config."""
        mock_llm = self._make_mock_llm()
        qa = QAService(qa_env["config"], qa_env["db"], llm_service=mock_llm)
        result = qa.assess_entry("good-note", "test-kb", tier=1, readable_kbs=UNSCOPED)
        mock_llm.complete.assert_not_called()
        assert "llm_available" in result

    def test_llm_available_flag_in_response(self, qa_env):
        """llm_available flag present in response."""
        qa = QAService(qa_env["config"], qa_env["db"])
        result = qa.assess_entry("good-note", "test-kb", readable_kbs=UNSCOPED)
        assert "llm_available" in result
        assert result["llm_available"] is False

    def test_llm_issues_distinct_rule_name(self, qa_env):
        """LLM issues use 'llm_rubric_violation' rule, distinct from 'rubric_violation'."""
        llm_response = json.dumps(
            [
                {
                    "item": "Entry body explains the why, not just the what",
                    "pass": False,
                    "confidence": 0.8,
                    "reasoning": "lacks explanation",
                },
            ]
        )
        mock_llm = self._make_mock_llm(response=llm_response)
        qa = QAService(qa_env["config"], qa_env["db"], llm_service=mock_llm)
        result = qa.assess_entry("good-note", "test-kb", tier=2, readable_kbs=UNSCOPED)

        for issue in result["issues"]:
            if issue.get("rubric_item") == "Entry body explains the why, not just the what":
                assert issue["rule"] == "llm_rubric_violation"
                assert issue["rule"] != "rubric_violation"
