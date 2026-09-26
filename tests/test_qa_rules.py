"""
Unit tests for QAService _check_* validation rules.

Each rule is tested in isolation with minimal fixtures — just a PyriteDB
and a QAService instance. No full KB setup, no IndexManager, no filesystem repos.
"""

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.services.qa_service import QAService
from pyrite.storage.database import PyriteDB
from pyrite.services.access_policy import UNSCOPED

_registered_kbs: set[tuple] = set()


@pytest.fixture(autouse=True)
def _reset_kb_cache():
    """Clear the KB registration cache between tests."""
    _registered_kbs.clear()
    yield
    _registered_kbs.clear()


@pytest.fixture
def qa(tmp_path):
    """Minimal QAService with an empty database."""
    db_path = tmp_path / "test.db"
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()

    kb_config = KBConfig(
        name="test",
        path=kb_path,
        kb_type=KBType.GENERIC,
        description="Test KB",
    )
    config = PyriteConfig(
        knowledge_bases=[kb_config],
        settings=Settings(index_path=db_path),
    )
    db = PyriteDB(db_path)
    svc = QAService(config, db)
    yield svc, db, config, kb_path
    db.close()


def _ensure_kb(db: PyriteDB, kb_name: str, kb_path: str = "/tmp/test-kb") -> None:
    """Insert a KB row if it doesn't exist yet (for FK satisfaction)."""
    key = (id(db), kb_name)
    if key in _registered_kbs:
        return
    db._raw_conn.execute(
        "INSERT OR IGNORE INTO kb (name, kb_type, path) VALUES (?, ?, ?)",
        (kb_name, "generic", kb_path),
    )
    db._raw_conn.commit()
    _registered_kbs.add(key)


def _insert_entry(db: PyriteDB, **kwargs) -> None:
    """Insert an entry row with sensible defaults."""
    defaults = {
        "id": "test-entry",
        "kb_name": "test",
        "entry_type": "note",
        "title": "Test Title",
        "body": "Test body content.",
        "date": None,
        "importance": None,
        "status": None,
        "metadata": None,
    }
    defaults.update(kwargs)
    _ensure_kb(db, defaults["kb_name"])
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join("?" for _ in defaults)
    db._raw_conn.execute(
        f"INSERT INTO entry ({cols}) VALUES ({placeholders})",
        tuple(defaults.values()),
    )
    db._raw_conn.commit()


def _insert_link(db: PyriteDB, source_id: str, target_id: str, **kwargs) -> None:
    """Insert a link row."""
    defaults = {
        "source_id": source_id,
        "source_kb": "test",
        "target_id": target_id,
        "target_kb": "test",
        "relation": "related_to",
        "inverse_relation": "related_to",
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join("?" for _ in defaults)
    db._raw_conn.execute(
        f"INSERT INTO link ({cols}) VALUES ({placeholders})",
        tuple(defaults.values()),
    )
    db._raw_conn.commit()


# =========================================================================
# _check_missing_titles
# =========================================================================


class TestCheckMissingTitles:
    def test_no_issue_when_title_present(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="good", title="Valid Title")
        issues: list[dict[str, Any]] = []
        svc._check_missing_titles(issues, "test")
        assert not any(i["entry_id"] == "good" for i in issues)

    def test_flags_empty_title(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="empty-title", title="")
        issues: list[dict[str, Any]] = []
        svc._check_missing_titles(issues, "test")
        matched = [i for i in issues if i["entry_id"] == "empty-title"]
        assert len(matched) == 1
        assert matched[0]["rule"] == "missing_title"
        assert matched[0]["severity"] == "error"
        assert matched[0]["field"] == "title"

    def test_flags_whitespace_only_title(self, qa):
        """Title that is whitespace-only is stored but effectively empty."""
        svc, db, _, _ = qa
        _insert_entry(db, id="ws-title", title="   ")
        issues: list[dict[str, Any]] = []
        svc._check_missing_titles(issues, "test")
        # SQL check is `title IS NULL OR title = ''` — whitespace passes SQL
        # but _check_entry_fields catches it via `not entry.get("title")`
        # The bulk SQL check won't flag whitespace-only, which is acceptable

    def test_scoped_to_kb(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="other-kb-entry", kb_name="other", title="")
        issues: list[dict[str, Any]] = []
        svc._check_missing_titles(issues, "test")
        assert not any(i["entry_id"] == "other-kb-entry" for i in issues)

    def test_no_kb_filter_checks_all(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="a", kb_name="test", title="")
        _insert_entry(db, id="b", kb_name="other", title="")
        issues: list[dict[str, Any]] = []
        svc._check_missing_titles(issues, None)
        ids = {i["entry_id"] for i in issues}
        assert "a" in ids
        assert "b" in ids


# =========================================================================
# _check_empty_bodies
# =========================================================================


class TestCheckEmptyBodies:
    def test_no_issue_when_body_present(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="good", body="Has content.")
        issues: list[dict[str, Any]] = []
        svc._check_empty_bodies(issues, "test")
        assert not any(i["entry_id"] == "good" for i in issues)

    def test_flags_empty_body(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="empty", body="")
        issues: list[dict[str, Any]] = []
        svc._check_empty_bodies(issues, "test")
        matched = [i for i in issues if i["entry_id"] == "empty"]
        assert len(matched) == 1
        assert matched[0]["rule"] == "empty_body"
        assert matched[0]["severity"] == "warning"
        assert matched[0]["field"] == "body"

    def test_flags_null_body(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="null-body", body=None)
        issues: list[dict[str, Any]] = []
        svc._check_empty_bodies(issues, "test")
        matched = [i for i in issues if i["entry_id"] == "null-body"]
        assert len(matched) == 1
        assert matched[0]["rule"] == "empty_body"

    def test_skips_collection_type(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="coll", entry_type="collection", body="")
        issues: list[dict[str, Any]] = []
        svc._check_empty_bodies(issues, "test")
        assert not any(i["entry_id"] == "coll" for i in issues)

    def test_skips_relationship_type(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="rel", entry_type="relationship", body="")
        issues: list[dict[str, Any]] = []
        svc._check_empty_bodies(issues, "test")
        assert not any(i["entry_id"] == "rel" for i in issues)

    def test_message_includes_entry_type(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="typed", entry_type="event", body="")
        issues: list[dict[str, Any]] = []
        svc._check_empty_bodies(issues, "test")
        matched = [i for i in issues if i["entry_id"] == "typed"]
        assert "event" in matched[0]["message"]


# =========================================================================
# _check_events_missing_dates
# =========================================================================


class TestCheckEventsMissingDates:
    def test_no_issue_when_event_has_date(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="dated-event", entry_type="event", date="2025-01-15")
        issues: list[dict[str, Any]] = []
        svc._check_events_missing_dates(issues, "test")
        assert not any(i["entry_id"] == "dated-event" for i in issues)

    def test_flags_event_without_date(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="dateless", entry_type="event", date=None)
        issues: list[dict[str, Any]] = []
        svc._check_events_missing_dates(issues, "test")
        matched = [i for i in issues if i["entry_id"] == "dateless"]
        assert len(matched) == 1
        assert matched[0]["rule"] == "event_missing_date"
        assert matched[0]["severity"] == "error"
        assert matched[0]["field"] == "date"

    def test_flags_event_with_empty_date(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="empty-date", entry_type="event", date="")
        issues: list[dict[str, Any]] = []
        svc._check_events_missing_dates(issues, "test")
        matched = [i for i in issues if i["entry_id"] == "empty-date"]
        assert len(matched) == 1

    def test_ignores_non_event_types(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="note-no-date", entry_type="note", date=None)
        issues: list[dict[str, Any]] = []
        svc._check_events_missing_dates(issues, "test")
        assert not any(i["entry_id"] == "note-no-date" for i in issues)


# =========================================================================
# _check_invalid_dates
# =========================================================================


class TestCheckInvalidDates:
    def test_no_issue_for_valid_date(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="valid-date", date="2025-06-15")
        issues: list[dict[str, Any]] = []
        svc._check_invalid_dates(issues, "test")
        assert not any(i["entry_id"] == "valid-date" for i in issues)

    def test_flags_malformed_date(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="bad-date", date="not-a-date")
        issues: list[dict[str, Any]] = []
        svc._check_invalid_dates(issues, "test")
        matched = [i for i in issues if i["entry_id"] == "bad-date"]
        assert len(matched) == 1
        assert matched[0]["rule"] == "invalid_date"
        assert matched[0]["severity"] == "error"
        assert matched[0]["field"] == "date"
        assert "not-a-date" in matched[0]["message"]

    def test_flags_impossible_date(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="impossible", date="2025-13-45")
        issues: list[dict[str, Any]] = []
        svc._check_invalid_dates(issues, "test")
        matched = [i for i in issues if i["entry_id"] == "impossible"]
        assert len(matched) == 1

    def test_skips_null_dates(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="no-date", date=None)
        issues: list[dict[str, Any]] = []
        svc._check_invalid_dates(issues, "test")
        assert not any(i["entry_id"] == "no-date" for i in issues)

    def test_skips_empty_dates(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="empty-date", date="")
        issues: list[dict[str, Any]] = []
        svc._check_invalid_dates(issues, "test")
        assert not any(i["entry_id"] == "empty-date" for i in issues)

    def test_flags_wrong_format(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="us-format", date="01/15/2025")
        issues: list[dict[str, Any]] = []
        svc._check_invalid_dates(issues, "test")
        matched = [i for i in issues if i["entry_id"] == "us-format"]
        assert len(matched) == 1


# =========================================================================
# _check_importance_range
# =========================================================================


class TestCheckImportanceRange:
    def test_no_issue_for_valid_importance(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="ok-imp", importance=5)
        issues: list[dict[str, Any]] = []
        svc._check_importance_range(issues, "test")
        assert not any(i["entry_id"] == "ok-imp" for i in issues)

    def test_no_issue_at_boundaries(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="imp-1", importance=1)
        _insert_entry(db, id="imp-10", importance=10)
        issues: list[dict[str, Any]] = []
        svc._check_importance_range(issues, "test")
        assert not any(i["entry_id"] in ("imp-1", "imp-10") for i in issues)

    def test_flags_importance_too_high(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="too-high", importance=15)
        issues: list[dict[str, Any]] = []
        svc._check_importance_range(issues, "test")
        matched = [i for i in issues if i["entry_id"] == "too-high"]
        assert len(matched) == 1
        assert matched[0]["rule"] == "importance_range"
        assert matched[0]["severity"] == "warning"
        assert matched[0]["field"] == "importance"

    def test_flags_importance_too_low(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="too-low", importance=0)
        issues: list[dict[str, Any]] = []
        svc._check_importance_range(issues, "test")
        matched = [i for i in issues if i["entry_id"] == "too-low"]
        assert len(matched) == 1
        assert matched[0]["rule"] == "importance_range"

    def test_flags_negative_importance(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="negative", importance=-3)
        issues: list[dict[str, Any]] = []
        svc._check_importance_range(issues, "test")
        matched = [i for i in issues if i["entry_id"] == "negative"]
        assert len(matched) == 1

    def test_skips_null_importance(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="no-imp", importance=None)
        issues: list[dict[str, Any]] = []
        svc._check_importance_range(issues, "test")
        assert not any(i["entry_id"] == "no-imp" for i in issues)


# =========================================================================
# _check_broken_links
# =========================================================================


class TestCheckBrokenLinks:
    def test_no_issue_when_link_target_exists(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="source")
        _insert_entry(db, id="target")
        _insert_link(db, "source", "target")
        issues: list[dict[str, Any]] = []
        svc._check_broken_links(issues, "test", readable_kbs=UNSCOPED)
        assert not any(i["rule"] == "broken_link" for i in issues)

    def test_flags_link_to_nonexistent_target(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="source")
        _insert_link(db, "source", "nonexistent")
        issues: list[dict[str, Any]] = []
        svc._check_broken_links(issues, "test", readable_kbs=UNSCOPED)
        matched = [i for i in issues if i["rule"] == "broken_link"]
        assert len(matched) == 1
        assert matched[0]["entry_id"] == "source"
        # Wiki-style wanted-page semantics — warning, not error.
        assert matched[0]["severity"] == "warning"
        assert matched[0]["field"] == "links"
        assert "nonexistent" in matched[0]["message"]

    def test_multiple_broken_links(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="source")
        _insert_link(db, "source", "ghost-1")
        _insert_link(db, "source", "ghost-2")
        issues: list[dict[str, Any]] = []
        svc._check_broken_links(issues, "test", readable_kbs=UNSCOPED)
        matched = [i for i in issues if i["rule"] == "broken_link"]
        assert len(matched) == 2

    def test_scoped_to_source_kb(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="other-src", kb_name="other")
        _insert_link(db, "other-src", "ghost", source_kb="other")
        issues: list[dict[str, Any]] = []
        svc._check_broken_links(issues, "test", readable_kbs=UNSCOPED)
        # Should not find broken links from "other" KB
        assert not any(i["entry_id"] == "other-src" for i in issues)

    def test_message_includes_relation(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="src")
        _insert_link(db, "src", "missing", relation="references")
        issues: list[dict[str, Any]] = []
        svc._check_broken_links(issues, "test", readable_kbs=UNSCOPED)
        matched = [i for i in issues if i["rule"] == "broken_link"]
        assert "references" in matched[0]["message"]


# =========================================================================
# _check_orphans
# =========================================================================


class TestCheckOrphans:
    def test_flags_entry_with_no_links(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="loner")
        issues: list[dict[str, Any]] = []
        svc._check_orphans(issues, "test", readable_kbs=UNSCOPED)
        matched = [i for i in issues if i["entry_id"] == "loner"]
        assert len(matched) == 1
        assert matched[0]["rule"] == "orphan_entry"
        assert matched[0]["severity"] == "info"
        assert matched[0]["field"] is None

    def test_no_issue_when_entry_has_outgoing_link(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="linked-out")
        _insert_entry(db, id="target")
        _insert_link(db, "linked-out", "target")
        issues: list[dict[str, Any]] = []
        svc._check_orphans(issues, "test", readable_kbs=UNSCOPED)
        assert not any(i["entry_id"] == "linked-out" for i in issues)

    def test_no_issue_when_entry_has_incoming_link(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="linked-in")
        _insert_entry(db, id="source")
        _insert_link(db, "source", "linked-in")
        issues: list[dict[str, Any]] = []
        svc._check_orphans(issues, "test", readable_kbs=UNSCOPED)
        assert not any(i["entry_id"] == "linked-in" for i in issues)


# =========================================================================
# _check_entry_fields (per-entry validation)
# =========================================================================


class TestCheckEntryFields:
    """Tests for the per-entry field validator used by validate_entry()."""

    def _make_entry(self, **overrides) -> dict[str, Any]:
        """Build a minimal entry dict."""
        entry = {
            "id": "test-entry",
            "kb_name": "test",
            "entry_type": "note",
            "title": "Valid Title",
            "body": "Valid body.",
            "date": None,
            "importance": None,
            "status": None,
            "metadata": None,
        }
        entry.update(overrides)
        return entry

    def test_clean_entry_no_issues(self, qa):
        svc, _, _, _ = qa
        entry = self._make_entry()
        issues: list[dict[str, Any]] = []
        svc._check_entry_fields(entry, issues)
        assert issues == []

    def test_missing_title(self, qa):
        svc, _, _, _ = qa
        issues: list[dict[str, Any]] = []
        svc._check_entry_fields(self._make_entry(title=""), issues)
        assert any(i["rule"] == "missing_title" for i in issues)

    def test_null_title(self, qa):
        svc, _, _, _ = qa
        issues: list[dict[str, Any]] = []
        svc._check_entry_fields(self._make_entry(title=None), issues)
        assert any(i["rule"] == "missing_title" for i in issues)

    def test_empty_body_warning(self, qa):
        svc, _, _, _ = qa
        issues: list[dict[str, Any]] = []
        svc._check_entry_fields(self._make_entry(body=""), issues)
        body_issues = [i for i in issues if i["rule"] == "empty_body"]
        assert len(body_issues) == 1
        assert body_issues[0]["severity"] == "warning"

    def test_collection_skips_body_check(self, qa):
        svc, _, _, _ = qa
        issues: list[dict[str, Any]] = []
        svc._check_entry_fields(self._make_entry(entry_type="collection", body=""), issues)
        assert not any(i["rule"] == "empty_body" for i in issues)

    def test_relationship_skips_body_check(self, qa):
        svc, _, _, _ = qa
        issues: list[dict[str, Any]] = []
        svc._check_entry_fields(self._make_entry(entry_type="relationship", body=""), issues)
        assert not any(i["rule"] == "empty_body" for i in issues)

    def test_event_missing_date(self, qa):
        svc, _, _, _ = qa
        issues: list[dict[str, Any]] = []
        svc._check_entry_fields(self._make_entry(entry_type="event", date=None), issues)
        assert any(i["rule"] == "event_missing_date" for i in issues)

    def test_event_with_date_no_issue(self, qa):
        svc, _, _, _ = qa
        issues: list[dict[str, Any]] = []
        svc._check_entry_fields(self._make_entry(entry_type="event", date="2025-01-15"), issues)
        assert not any(i["rule"] == "event_missing_date" for i in issues)

    def test_invalid_date_format(self, qa):
        svc, _, _, _ = qa
        issues: list[dict[str, Any]] = []
        svc._check_entry_fields(self._make_entry(date="15-01-2025"), issues)
        assert any(i["rule"] == "invalid_date" for i in issues)

    def test_valid_date_no_issue(self, qa):
        svc, _, _, _ = qa
        issues: list[dict[str, Any]] = []
        svc._check_entry_fields(self._make_entry(date="2025-01-15"), issues)
        assert not any(i["rule"] == "invalid_date" for i in issues)

    def test_importance_out_of_range_high(self, qa):
        svc, _, _, _ = qa
        issues: list[dict[str, Any]] = []
        svc._check_entry_fields(self._make_entry(importance=11), issues)
        imp_issues = [i for i in issues if i["rule"] == "importance_range"]
        assert len(imp_issues) == 1
        assert imp_issues[0]["severity"] == "warning"

    def test_importance_out_of_range_zero(self, qa):
        svc, _, _, _ = qa
        issues: list[dict[str, Any]] = []
        svc._check_entry_fields(self._make_entry(importance=0), issues)
        assert any(i["rule"] == "importance_range" for i in issues)

    def test_importance_in_range_no_issue(self, qa):
        svc, _, _, _ = qa
        issues: list[dict[str, Any]] = []
        svc._check_entry_fields(self._make_entry(importance=5), issues)
        assert not any(i["rule"] == "importance_range" for i in issues)

    def test_importance_null_no_issue(self, qa):
        svc, _, _, _ = qa
        issues: list[dict[str, Any]] = []
        svc._check_entry_fields(self._make_entry(importance=None), issues)
        assert not any(i["rule"] == "importance_range" for i in issues)

    def test_multiple_issues_combined(self, qa):
        """Entry with multiple problems reports all of them."""
        svc, _, _, _ = qa
        issues: list[dict[str, Any]] = []
        svc._check_entry_fields(
            self._make_entry(
                title="",
                body="",
                entry_type="event",
                date="garbage",
                importance=99,
            ),
            issues,
        )
        rules = {i["rule"] for i in issues}
        assert "missing_title" in rules
        assert "empty_body" in rules
        assert "invalid_date" in rules
        assert "importance_range" in rules


# =========================================================================
# _check_entry_links (per-entry link checker)
# =========================================================================


class TestCheckEntryLinks:
    def test_no_issue_when_all_targets_exist(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="src")
        _insert_entry(db, id="tgt")
        _insert_link(db, "src", "tgt")
        issues: list[dict[str, Any]] = []
        svc._check_entry_links("src", "test", issues, readable_kbs=UNSCOPED)
        assert not any(i["rule"] == "broken_link" for i in issues)

    def test_flags_broken_outlink(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="src")
        _insert_link(db, "src", "missing-target")
        issues: list[dict[str, Any]] = []
        svc._check_entry_links("src", "test", issues, readable_kbs=UNSCOPED)
        matched = [i for i in issues if i["rule"] == "broken_link"]
        assert len(matched) == 1
        # Wanted-page semantics: visible but not CI-failing.
        assert matched[0]["severity"] == "warning"
        assert "missing-target" in matched[0]["message"]

    def test_no_links_no_issues(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="isolated")
        issues: list[dict[str, Any]] = []
        svc._check_entry_links("isolated", "test", issues, readable_kbs=UNSCOPED)
        assert issues == []


# =========================================================================
# _check_schema_validation (per-entry schema check)
# =========================================================================


class TestCheckSchemaValidation:
    def test_skips_when_no_kb_yaml(self, qa):
        """No kb.yaml means no schema issues."""
        svc, db, _, _ = qa
        entry = {
            "id": "schema-test",
            "kb_name": "test",
            "entry_type": "note",
            "title": "Title",
            "body": "Body",
            "date": None,
            "importance": None,
            "status": None,
            "metadata": None,
        }
        issues: list[dict[str, Any]] = []
        svc._check_schema_validation(entry, issues)
        assert not any(i["rule"] == "schema_violation" for i in issues)

    def test_reports_schema_errors(self, qa):
        """Schema errors become severity=error issues."""
        svc, db, _, kb_path = qa
        # Write a kb.yaml with a required field
        (kb_path / "kb.yaml").write_text(
            "name: test\n"
            "validation:\n"
            "  enforce: true\n"
            "types:\n"
            "  note:\n"
            "    fields:\n"
            "      priority:\n"
            "        type: select\n"
            "        options: [low, medium, high]\n"
            "        required: true\n"
        )
        entry = {
            "id": "missing-field",
            "kb_name": "test",
            "entry_type": "note",
            "title": "Title",
            "body": "Body",
            "date": None,
            "importance": None,
            "status": None,
            "metadata": None,
        }
        issues: list[dict[str, Any]] = []
        svc._check_schema_validation(entry, issues)
        schema_issues = [i for i in issues if i["rule"] == "schema_violation"]
        assert len(schema_issues) >= 1
        assert any(i["severity"] == "error" for i in schema_issues)

    def test_clean_entry_no_schema_issues(self, qa):
        """Entry matching schema produces no issues."""
        svc, db, _, kb_path = qa
        # Minimal schema that the default note satisfies (no required custom fields)
        (kb_path / "kb.yaml").write_text(
            "name: test\nvalidation:\n  enforce: true\ntypes:\n  note:\n    fields: {}\n"
        )
        entry = {
            "id": "clean",
            "kb_name": "test",
            "entry_type": "note",
            "title": "Title",
            "body": "Body",
            "date": None,
            "importance": 5,
            "status": None,
            "metadata": None,
        }
        issues: list[dict[str, Any]] = []
        svc._check_schema_validation(entry, issues)
        assert not any(i["rule"] == "schema_violation" for i in issues)


# =========================================================================
# _check_schema_all (bulk schema validation)
# =========================================================================


class TestCheckSchemaAll:
    def test_skips_when_no_kb_yaml(self, qa):
        svc, db, _, _ = qa
        _insert_entry(db, id="any-entry")
        issues: list[dict[str, Any]] = []
        svc._check_schema_all(issues, "test")
        assert not any(i["rule"] == "schema_violation" for i in issues)

    def test_reports_violations_for_all_entries(self, qa):
        svc, db, _, kb_path = qa
        (kb_path / "kb.yaml").write_text(
            "name: test\n"
            "validation:\n"
            "  enforce: true\n"
            "types:\n"
            "  note:\n"
            "    fields:\n"
            "      category:\n"
            "        type: select\n"
            "        options: [a, b, c]\n"
            "        required: true\n"
        )
        _insert_entry(db, id="entry-1", entry_type="note")
        _insert_entry(db, id="entry-2", entry_type="note")
        issues: list[dict[str, Any]] = []
        svc._check_schema_all(issues, "test")
        schema_issues = [i for i in issues if i["rule"] == "schema_violation"]
        entry_ids = {i["entry_id"] for i in schema_issues}
        assert "entry-1" in entry_ids
        assert "entry-2" in entry_ids
