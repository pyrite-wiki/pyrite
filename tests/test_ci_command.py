"""Tests for the `pyrite ci` CLI command — CI/CD-optimized KB validation."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models import EventEntry, NoteEntry
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

runner = CliRunner()


@pytest.fixture
def ci_env():
    """Environment for CI command tests with valid entries."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"

        events_path = tmpdir / "events"
        events_path.mkdir()

        research_path = tmpdir / "research"
        research_path.mkdir()
        (research_path / "actors").mkdir()

        events_kb = KBConfig(
            name="test-events",
            path=events_path,
            kb_type=KBType.EVENTS,
            description="Test events KB",
        )
        research_kb = KBConfig(
            name="test-research",
            path=research_path,
            kb_type=KBType.RESEARCH,
        )

        config = PyriteConfig(
            knowledge_bases=[events_kb, research_kb],
            settings=Settings(index_path=db_path),
        )

        # Create valid entries
        events_repo = KBRepository(events_kb)
        for i in range(3):
            event = EventEntry.create(
                date=f"2025-01-{10 + i:02d}",
                title=f"Test Event {i}",
                body=f"Body for event {i} about testing.",
                importance=5 + i,
            )
            event.tags = ["test"]
            events_repo.save(event)

        db = PyriteDB(db_path)
        IndexManager(db, config).index_all()

        # Add links between events so they satisfy rubric "has outgoing links"
        event_ids = [
            row["id"]
            for row in db.execute_sql(
                "SELECT id FROM entry WHERE kb_name = 'test-events' ORDER BY id"
            )
        ]
        for i, eid in enumerate(event_ids):
            target = event_ids[(i + 1) % len(event_ids)]
            db._raw_conn.execute(
                "INSERT INTO link (source_id, source_kb, target_id, target_kb, relation, inverse_relation) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (eid, "test-events", target, "test-events", "related_to", "related_to"),
            )
        db._raw_conn.commit()
        db.close()

        yield {
            "config": config,
            "tmpdir": tmpdir,
            "events_kb": events_kb,
            "research_kb": research_kb,
        }


@pytest.fixture
def ci_env_with_errors():
    """Environment with entries that have validation errors."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"

        kb_path = tmpdir / "broken"
        kb_path.mkdir()

        kb_config = KBConfig(
            name="broken-kb",
            path=kb_path,
            kb_type=KBType.RESEARCH,
            description="KB with errors",
        )

        config = PyriteConfig(
            knowledge_bases=[kb_config],
            settings=Settings(index_path=db_path),
        )

        # Register KB and create entries with issues
        db = PyriteDB(db_path)
        db.register_kb("broken-kb", "research", str(kb_path), "KB with errors")
        db.upsert_entry(
            {
                "id": "bad-entry",
                "kb_name": "broken-kb",
                "entry_type": "note",
                "title": "",  # empty title = error
                "body": "Some body text",
                "importance": 5,
                "status": "active",
            }
        )
        # Also create a valid entry with empty body (warning)
        db.upsert_entry(
            {
                "id": "warn-entry",
                "kb_name": "broken-kb",
                "entry_type": "note",
                "title": "Has Title",
                "body": "",  # empty body = warning
                "importance": 5,
                "status": "active",
            }
        )
        db.close()

        yield {
            "config": config,
            "tmpdir": tmpdir,
            "kb_config": kb_config,
        }


def _patch_ci(env):
    """Patch load_config for CI command tests."""
    target = env["config"]
    return patch("pyrite.cli.load_config", return_value=target)


@pytest.mark.cli
class TestCICleanKB:
    def test_ci_clean_kb_exits_zero(self, ci_env):
        """KB with valid entries should exit 0."""
        with _patch_ci(ci_env):
            result = runner.invoke(app, ["ci"])
            assert result.exit_code == 0
            assert "PASS" in result.output


@pytest.mark.cli
class TestCIInvalidEntry:
    def test_ci_invalid_entry_exits_nonzero(self, ci_env_with_errors):
        """KB with schema violation (missing title) should exit 1."""
        with _patch_ci(ci_env_with_errors):
            result = runner.invoke(app, ["ci"])
            assert result.exit_code == 1
            assert "FAIL" in result.output
            assert "error" in result.output.lower()


@pytest.mark.cli
class TestCISpecificKB:
    def test_ci_specific_kb(self, ci_env):
        """--kb flag validates only that KB."""
        with _patch_ci(ci_env):
            result = runner.invoke(app, ["ci", "--kb", "test-events"])
            assert result.exit_code == 0
            assert "test-events" in result.output
            # Should not mention the other KB
            assert "test-research" not in result.output


@pytest.mark.cli
class TestCIJsonOutput:
    def test_ci_json_output(self, ci_env):
        """--format json produces valid JSON with expected structure."""
        with _patch_ci(ci_env):
            result = runner.invoke(app, ["ci", "--format", "json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert "kbs" in data
            assert "total_entries" in data
            assert "total_errors" in data
            assert "total_warnings" in data
            assert "result" in data
            assert data["result"] == "pass"

    def test_ci_json_output_with_errors(self, ci_env_with_errors):
        """--format json with errors shows fail result."""
        with _patch_ci(ci_env_with_errors):
            result = runner.invoke(app, ["ci", "--format", "json"])
            assert result.exit_code == 1
            data = json.loads(result.output)
            assert data["result"] == "fail"
            assert data["total_errors"] > 0


@pytest.mark.cli
class TestCISeverityFilter:
    def test_ci_severity_error_ignores_warnings(self, ci_env_with_errors):
        """--severity error should only fail on errors, not warnings."""
        with _patch_ci(ci_env_with_errors):
            # With default severity=warning, should fail (has both errors and warnings)
            result_default = runner.invoke(app, ["ci"])
            assert result_default.exit_code == 1

            # With severity=error, should still fail (has errors)
            result_error = runner.invoke(app, ["ci", "--severity", "error"])
            assert result_error.exit_code == 1

    def test_ci_severity_error_passes_when_only_warnings(self, ci_env):
        """--severity error should pass when there are only warnings (or nothing)."""
        with _patch_ci(ci_env):
            result = runner.invoke(app, ["ci", "--severity", "error"])
            assert result.exit_code == 0


@pytest.mark.cli
class TestCINoKBs:
    def test_ci_no_kbs(self):
        """No KBs configured should print friendly message, exit 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = PyriteConfig(
                knowledge_bases=[],
                settings=Settings(index_path=Path(tmpdir) / "index.db"),
            )
            with patch("pyrite.cli.load_config", return_value=config):
                result = runner.invoke(app, ["ci"])
                assert result.exit_code == 0
                assert "no" in result.output.lower() and "kb" in result.output.lower()


@pytest.mark.cli
class TestCIMultipleKBs:
    def test_ci_multiple_kbs(self, ci_env):
        """Validates all KBs and aggregates results."""
        with _patch_ci(ci_env):
            result = runner.invoke(app, ["ci"])
            assert result.exit_code == 0
            # Both KBs should appear in output
            assert "test-events" in result.output
            assert "test-research" in result.output
            # Should show total counts
            assert "entries" in result.output.lower()


@pytest.mark.cli
class TestCITierOption:
    def test_ci_tier2_with_stub_is_refused(self, ci_env):
        """--tier 2 with the stub provider is refused, never passed (#795).

        It used to print an advisory and pass on tier 1 alone.
        """
        with _patch_ci(ci_env):
            result = runner.invoke(app, ["ci", "--tier", "2"])
            assert result.exit_code == 1
            assert "AI_NOT_CONFIGURED" in result.output

    def test_ci_default_tier1_no_llm_calls(self, ci_env):
        """Default (no --tier) uses tier=1, no LLM calls."""
        with _patch_ci(ci_env):
            result = runner.invoke(app, ["ci"])
            assert result.exit_code == 0
            # No LLM-related output
            assert "LLM not configured" not in result.output


@pytest.fixture
def ci_env_only_broken_links():
    """Environment where the only issue is a broken wikilink (wanted page)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"

        kb_path = tmpdir / "wiki"
        kb_path.mkdir()

        kb_config = KBConfig(
            name="wiki-kb",
            path=kb_path,
            kb_type=KBType.RESEARCH,
            description="Wiki-style KB with wanted pages",
        )

        config = PyriteConfig(
            knowledge_bases=[kb_config],
            settings=Settings(index_path=db_path),
        )

        db = PyriteDB(db_path)
        db.register_kb("wiki-kb", "research", str(kb_path), "Wiki-style KB")
        # Valid entry — no structural errors.
        db.upsert_entry(
            {
                "id": "page-a",
                "kb_name": "wiki-kb",
                "entry_type": "note",
                "title": "Page A",
                "body": "Body A referencing [[not-yet-written]].",
                "importance": 5,
                "status": "active",
            }
        )
        # Wikilink to a page that hasn't been created yet.
        db._raw_conn.execute(
            "INSERT INTO link (source_id, source_kb, target_id, target_kb, relation, inverse_relation) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("page-a", "wiki-kb", "not-yet-written", "wiki-kb", "wikilink", "wikilink"),
        )
        db._raw_conn.commit()
        db.close()

        yield {"config": config, "tmpdir": tmpdir, "kb_config": kb_config}


@pytest.mark.cli
class TestQAValidateBrokenLinkExitCode:
    """Broken wikilinks are wanted-pages, not errors — `pyrite qa validate` must exit 0."""

    def test_qa_validate_rich_exits_zero_when_only_broken_links(self, ci_env_only_broken_links):
        """Rich output of qa validate returns exit 0 when only broken links exist.

        `pyrite qa validate` (rich output path) raises `typer.Exit(1)` only when
        there is at least one error-severity issue. Broken wikilinks are
        warning-severity (wiki wanted-page semantics), so a KB whose only
        issues are broken links must still pass.
        """
        with patch(
            "pyrite.cli.context.load_config", return_value=ci_env_only_broken_links["config"]
        ):
            # Rich output is the path that gates on error_count > 0.
            # (JSON output, which is the CLI default, always exits 0 by design.)
            result = runner.invoke(app, ["qa", "validate", "wiki-kb", "--format", "rich"])
            assert result.exit_code == 0, result.output
            assert "broken_link" in result.output
            # Summary line should show 0 errors.
            assert "0 errors" in result.output

    def test_qa_validate_json_reports_broken_link_as_warning(self, ci_env_only_broken_links):
        """JSON output classifies broken_link with severity=warning."""
        with patch(
            "pyrite.cli.context.load_config", return_value=ci_env_only_broken_links["config"]
        ):
            result = runner.invoke(app, ["qa", "validate", "wiki-kb", "--format", "json"])
            assert result.exit_code == 0, result.output
            data = json.loads(result.output)
            severities = {i["severity"] for i in data["issues"] if i["rule"] == "broken_link"}
            assert severities == {"warning"}, (
                f"broken_link should be warning-level (wiki wanted pages), got {severities}"
            )
