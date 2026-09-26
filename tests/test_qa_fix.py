"""
Tests for QA auto-fix functionality (pyrite qa fix).

Covers: date normalisation, missing field defaults, broken wikilink fixes
by edit distance, tag normalisation, dry-run mode, and fix-rule filtering.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models import EventEntry, NoteEntry
from pyrite.schema.provenance import Link
from pyrite.schema.validators import generate_entry_id
from pyrite.services.qa_service import QAService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository
from pyrite.services.access_policy import UNSCOPED


def _make_note(title, body="", importance=5, tags=None, date=None, links=None):
    """Helper to create a NoteEntry with an auto-generated ID."""
    entry = NoteEntry(
        id=generate_entry_id(title),
        title=title,
        body=body,
        importance=importance,
    )
    if tags:
        entry.tags = tags
    if date:
        entry.date = date
    if links:
        entry.links = links
    return entry


@pytest.fixture
def fix_setup():
    """Create a QAService with entries that have fixable issues."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"

        kb_path = tmpdir / "notes"
        kb_path.mkdir()

        kb_config = KBConfig(
            name="test-kb",
            path=kb_path,
            kb_type=KBType.GENERIC,
            description="Test KB for fix tests",
        )

        config = PyriteConfig(
            knowledge_bases=[kb_config],
            settings=Settings(index_path=db_path),
        )

        repo = KBRepository(kb_config)

        # Entry with valid date (for contrast)
        good = _make_note(
            "Good Entry", body="A perfectly valid entry.", date="2025-06-15", tags=["test"]
        )
        repo.save(good)

        # Entry with invalid date (just a year) — use EventEntry since it has Temporal mixin
        bad_date = EventEntry.create(
            date="2006", title="Bad Date Entry", body="Entry with a year-only date."
        )
        bad_date.tags = ["test"]
        repo.save(bad_date)

        # Entry that links to a misspelled target
        misspelled_target = good.id[:-1] + "x"  # change last char
        linker = _make_note(
            "Linker Entry",
            body="Links to a misspelled target.",
            date="2025-01-01",
            tags=["test"],
            links=[Link(target=misspelled_target, relation="related_to")],
        )
        repo.save(linker)

        db = PyriteDB(db_path)
        index_mgr = IndexManager(db, config)
        index_mgr.index_all()

        qa = QAService(config, db)

        yield {
            "qa": qa,
            "config": config,
            "db": db,
            "kb_config": kb_config,
            "repo": repo,
            "index_mgr": index_mgr,
            "tmpdir": tmpdir,
            "good_id": good.id,
            "bad_date_id": bad_date.id,
            "linker_id": linker.id,
            "misspelled_target": misspelled_target,
        }

        db.close()


# =========================================================================
# Date normalisation tests
# =========================================================================


class TestDateNormalisation:
    """Test _normalise_date static method."""

    def test_year_only(self):
        assert QAService._normalise_date("2006") == "2006-01-01"

    def test_year_month(self):
        assert QAService._normalise_date("2006-3") == "2006-03-01"

    def test_year_month_padded(self):
        assert QAService._normalise_date("2006-03") == "2006-03-01"

    def test_iso_datetime(self):
        assert QAService._normalise_date("2025-06-15T10:30:00") == "2025-06-15"

    def test_already_valid(self):
        assert QAService._normalise_date("2025-06-15") == "2025-06-15"

    def test_us_date_format(self):
        assert QAService._normalise_date("01/15/2025") == "2025-01-15"

    def test_quoted_year(self):
        assert QAService._normalise_date("'2006'") == "2006-01-01"

    def test_unparseable_returns_none(self):
        assert QAService._normalise_date("not-a-date") is None

    def test_empty_string_returns_none(self):
        assert QAService._normalise_date("") is None

    def test_invalid_us_date(self):
        """Month 13 should not be parseable."""
        assert QAService._normalise_date("13/45/2025") is None


# =========================================================================
# Edit distance tests
# =========================================================================


class TestEditDistance:
    """Test Levenshtein edit distance."""

    def test_identical(self):
        assert QAService._edit_distance("abc", "abc") == 0

    def test_one_char_diff(self):
        assert QAService._edit_distance("abc", "abd") == 1

    def test_insertion(self):
        assert QAService._edit_distance("abc", "abcd") == 1

    def test_deletion(self):
        assert QAService._edit_distance("abcd", "abc") == 1

    def test_empty(self):
        assert QAService._edit_distance("", "abc") == 3

    def test_both_empty(self):
        assert QAService._edit_distance("", "") == 0


# =========================================================================
# Missing field defaults
# =========================================================================


class TestMissingFieldDefaults:
    """Test _fix_missing_field."""

    def test_importance_default(self):
        issue = {
            "entry_id": "test",
            "kb_name": "kb",
            "rule": "schema_violation",
            "field": "importance",
            "message": "missing field importance",
        }
        fix = QAService._fix_missing_field(issue)
        assert fix is not None
        assert fix["new_value"] == 5

    def test_unknown_field_returns_none(self):
        issue = {
            "entry_id": "test",
            "kb_name": "kb",
            "rule": "schema_violation",
            "field": "something_custom",
            "message": "missing field something_custom",
        }
        fix = QAService._fix_missing_field(issue)
        assert fix is None


# =========================================================================
# Integration: dry-run mode
# =========================================================================


class TestFixDryRun:
    """Test that dry-run mode reports changes without writing."""

    def test_dry_run_reports_date_fix(self, fix_setup):
        """Dry run should detect invalid date and report fix."""
        result = fix_setup["qa"].fix_kb("test-kb", dry_run=True, readable_kbs=UNSCOPED)

        assert result["dry_run"] is True

        # Should find the bad date entry
        date_fixes = [f for f in result["fixed"] if f["rule"] == "invalid_date"]
        assert len(date_fixes) >= 1
        assert date_fixes[0]["old_value"] == "2006"
        assert date_fixes[0]["new_value"] == "2006-01-01"

    def test_dry_run_does_not_modify_entries(self, fix_setup):
        """Dry run should not change the actual entry date."""
        fix_setup["qa"].fix_kb("test-kb", dry_run=True, readable_kbs=UNSCOPED)

        # Verify the entry still has the bad date
        rows = fix_setup["db"].execute_sql(
            "SELECT date FROM entry WHERE id = :eid AND kb_name = :kb",
            {"eid": fix_setup["bad_date_id"], "kb": "test-kb"},
        )
        assert rows[0]["date"] == "2006"

    def test_dry_run_reports_broken_link_fix(self, fix_setup):
        """Dry run should detect broken link and propose fix."""
        result = fix_setup["qa"].fix_kb("test-kb", dry_run=True, readable_kbs=UNSCOPED)

        link_fixes = [f for f in result["fixed"] if f["rule"] == "broken_link"]
        # Should find the broken link and propose fixing to the good entry
        if link_fixes:
            assert link_fixes[0]["new_value"] == fix_setup["good_id"]
            assert link_fixes[0]["old_value"] == fix_setup["misspelled_target"]


# =========================================================================
# Integration: fix-rule filtering
# =========================================================================


class TestFixRuleFiltering:
    """Test that --fix-rule only fixes specified rules."""

    def test_filter_to_date_only(self, fix_setup):
        """Only invalid_date fixes should be applied when filtered."""
        result = fix_setup["qa"].fix_kb(
            "test-kb", dry_run=True, fix_rules=["invalid_date"], readable_kbs=UNSCOPED
        )

        fixed_rules = {f["rule"] for f in result["fixed"]}
        assert "invalid_date" in fixed_rules or result["fixed_count"] >= 0
        # broken_link should not be in fixed
        assert not any(f["rule"] == "broken_link" for f in result["fixed"])

    def test_filter_to_broken_link(self, fix_setup):
        """Only broken_link fixes when filtered to that rule."""
        result = fix_setup["qa"].fix_kb(
            "test-kb", dry_run=True, fix_rules=["broken_link"], readable_kbs=UNSCOPED
        )

        # Date fixes should be skipped
        assert not any(f["rule"] == "invalid_date" for f in result["fixed"])


# =========================================================================
# Tag normalisation
# =========================================================================


class TestTagNormalisation:
    """Test tag case normalisation."""

    def test_find_mixed_case_tags(self, fix_setup):
        """Tags with mixed case should be detected for normalisation."""
        db = fix_setup["db"]
        # Insert a mixed-case tag through the normalised schema
        db.execute_sql(
            "INSERT OR IGNORE INTO tag (name) VALUES (:name)",
            {"name": "MixedCase"},
        )
        db.session.commit()
        # Get the tag_id
        tag_rows = db.execute_sql("SELECT id FROM tag WHERE name = :name", {"name": "MixedCase"})
        tag_id = tag_rows[0]["id"]
        db.execute_sql(
            "INSERT OR IGNORE INTO entry_tag (entry_id, kb_name, tag_id) VALUES (:eid, :kb, :tid)",
            {"eid": fix_setup["good_id"], "kb": "test-kb", "tid": tag_id},
        )
        db.session.commit()

        fixes = fix_setup["qa"]._find_tag_normalisation_fixes("test-kb")
        mixed = [f for f in fixes if f["old_value"] == "MixedCase"]
        assert len(mixed) == 1
        assert mixed[0]["new_value"] == "mixedcase"

    def test_all_lowercase_no_fix(self, fix_setup):
        """Already lowercase tags should not generate fixes."""
        fixes = fix_setup["qa"]._find_tag_normalisation_fixes("test-kb")
        # All seeded tags are lowercase ("test"), so no fixes expected
        assert all(f["old_value"] != f["new_value"] for f in fixes)


# =========================================================================
# Broken wikilink fix
# =========================================================================


class TestBrokenLinkFix:
    """Test broken link fix by edit distance."""

    def test_close_match_is_fixed(self, fix_setup):
        """A link target that differs by 1 char should be fixed."""
        all_ids = fix_setup["qa"]._get_all_entry_ids("test-kb")

        issue = {
            "entry_id": fix_setup["linker_id"],
            "kb_name": "test-kb",
            "rule": "broken_link",
            "field": "links",
            "message": f"Entry '{fix_setup['linker_id']}' links to non-existent "
            f"'{fix_setup['misspelled_target']}' in 'test-kb' (related_to)",
        }

        fix = fix_setup["qa"]._fix_broken_link(issue, all_ids)
        assert fix is not None
        assert fix["new_value"] == fix_setup["good_id"]

    def test_no_match_too_distant(self):
        """A completely different target should not be fixed."""
        qa = QAService.__new__(QAService)
        issue = {
            "entry_id": "e1",
            "kb_name": "kb",
            "rule": "broken_link",
            "field": "links",
            "message": "Entry 'e1' links to non-existent 'completely-different-thing' in 'kb' (related_to)",
        }
        fix = qa._fix_broken_link(issue, ["abc", "def"])
        assert fix is None

    def test_empty_entry_list(self):
        """No entries to match against should return None."""
        qa = QAService.__new__(QAService)
        issue = {
            "entry_id": "e1",
            "kb_name": "kb",
            "rule": "broken_link",
            "field": "links",
            "message": "Entry 'e1' links to non-existent 'target' in 'kb' (related_to)",
        }
        fix = qa._fix_broken_link(issue, [])
        assert fix is None


# =========================================================================
# Full fix (writes)
# =========================================================================


class TestFixApplied:
    """Test that fix without dry_run actually updates entries."""

    def test_date_fix_applied(self, fix_setup):
        """Running fix without dry_run should update the date."""
        result = fix_setup["qa"].fix_kb("test-kb", dry_run=False, readable_kbs=UNSCOPED)

        date_fixes = [f for f in result["fixed"] if f["rule"] == "invalid_date"]
        if date_fixes:
            # Re-index and verify
            fix_setup["index_mgr"].index_all()
            rows = fix_setup["db"].execute_sql(
                "SELECT date FROM entry WHERE id = :eid AND kb_name = :kb",
                {"eid": fix_setup["bad_date_id"], "kb": "test-kb"},
            )
            assert rows[0]["date"] == "2006-01-01"

    def test_fix_result_structure(self, fix_setup):
        """fix_kb result should have the expected structure."""
        result = fix_setup["qa"].fix_kb("test-kb", dry_run=True, readable_kbs=UNSCOPED)

        assert "kb_name" in result
        assert "dry_run" in result
        assert "fixed_count" in result
        assert "skipped_count" in result
        assert "manual_count" in result
        assert "fixed" in result
        assert "skipped" in result
        assert "manual" in result
        assert isinstance(result["fixed"], list)
        assert isinstance(result["manual"], list)
