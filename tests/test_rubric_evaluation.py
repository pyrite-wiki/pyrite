"""Tests for rubric evaluation — Phase 2 of the Intent Layer."""

import json
import tempfile
from pathlib import Path

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models import EventEntry, PersonEntry
from pyrite.services.qa_service import QAService
from pyrite.services.rubric_checkers import (
    GENERIC_TITLES,
    NAMED_CHECKERS,
    check_descriptive_title,
    check_has_outlinks,
    check_has_tags,
    check_priority_present,
    check_status_present,
)
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository
from pyrite.services.access_policy import UNSCOPED


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def rubric_setup():
    """Create a QAService with test entries for rubric evaluation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"

        kb_path = tmpdir / "test-kb"
        kb_path.mkdir()
        (kb_path / "people").mkdir()

        kb_config = KBConfig(
            name="test-kb",
            path=kb_path,
            kb_type=KBType.RESEARCH,
            description="Test KB for rubric evaluation",
        )

        config = PyriteConfig(
            knowledge_bases=[kb_config],
            settings=Settings(index_path=db_path),
        )

        repo = KBRepository(kb_config)

        # Create a well-formed person entry
        person = PersonEntry.create(name="Good Person", role="Researcher", importance=7)
        person.body = "A well-described person entry."
        person.tags = ["research"]
        repo.save(person)

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
        }

        db.close()


def _make_entry_dict(**overrides):
    """Helper to build entry dict for unit tests."""
    base = {
        "id": "test-entry",
        "kb_name": "test-kb",
        "entry_type": "note",
        "title": "A Good Title",
        "body": "Some content here.",
        "status": None,
        "metadata": None,
        "_tag_count": 1,
        "_outlink_count": 1,
    }
    base.update(overrides)
    return base


# =========================================================================
# TestRubricCheckers — unit tests for individual checker functions
# =========================================================================


class TestRubricCheckers:
    """Unit tests for individual rubric checker functions."""

    def test_descriptive_title_flags_generic(self):
        entry = _make_entry_dict(title="TODO")
        result = check_descriptive_title(entry, None)
        assert result is not None
        assert result["rule"] == "rubric_violation"
        assert result["field"] == "title"

    def test_descriptive_title_passes_good_title(self):
        entry = _make_entry_dict(title="Analysis of market trends")
        result = check_descriptive_title(entry, None)
        assert result is None

    def test_descriptive_title_flags_all_generic_titles(self):
        for generic in GENERIC_TITLES:
            entry = _make_entry_dict(title=generic.capitalize())
            result = check_descriptive_title(entry, None)
            assert result is not None, f"Should flag '{generic}' as generic"

    def test_has_tags_flags_missing(self):
        entry = _make_entry_dict(_tag_count=0)
        result = check_has_tags(entry, None)
        assert result is not None
        assert result["rule"] == "rubric_violation"

    def test_has_tags_passes_with_tags(self):
        entry = _make_entry_dict(_tag_count=3)
        result = check_has_tags(entry, None)
        assert result is None

    def test_has_outlinks_flags_missing(self):
        entry = _make_entry_dict(_outlink_count=0)
        result = check_has_outlinks(entry, None)
        assert result is not None
        assert result["rule"] == "rubric_violation"

    def test_has_outlinks_passes_with_links(self):
        entry = _make_entry_dict(_outlink_count=1)
        result = check_has_outlinks(entry, None)
        assert result is None

    def test_has_outlinks_skips_stubs(self):
        entry = _make_entry_dict(_outlink_count=0, body="This is a stub entry.")
        result = check_has_outlinks(entry, None)
        assert result is None

    def test_has_field_checker_person_role(self):
        checker = NAMED_CHECKERS["has_field"]

        # Missing role
        entry = _make_entry_dict(entry_type="person", metadata="{}")
        result = checker(entry, None, {"field": "role"})
        assert result is not None
        assert "role" in result["message"]

        # Has role
        entry = _make_entry_dict(entry_type="person", metadata=json.dumps({"role": "Researcher"}))
        result = checker(entry, None, {"field": "role"})
        assert result is None

    def test_has_any_field_checker_document_url_or_author(self):
        checker = NAMED_CHECKERS["has_any_field"]

        # Missing both
        entry = _make_entry_dict(entry_type="document", metadata="{}")
        result = checker(entry, None, {"fields": ["url", "author"]})
        assert result is not None

        # Has url
        entry = _make_entry_dict(
            entry_type="document", metadata=json.dumps({"url": "https://example.com"})
        )
        result = checker(entry, None, {"fields": ["url", "author"]})
        assert result is None

        # Has author
        entry = _make_entry_dict(entry_type="document", metadata=json.dumps({"author": "John"}))
        result = checker(entry, None, {"fields": ["url", "author"]})
        assert result is None

    def test_has_field_checker_document_type(self):
        checker = NAMED_CHECKERS["has_field"]

        entry = _make_entry_dict(entry_type="document", metadata="{}")
        result = checker(entry, None, {"field": "document_type"})
        assert result is not None

        entry = _make_entry_dict(
            entry_type="document", metadata=json.dumps({"document_type": "report"})
        )
        result = checker(entry, None, {"field": "document_type"})
        assert result is None

    def test_status_present_checker(self):
        result = check_status_present(_make_entry_dict(status=None), None)
        assert result is not None

        result = check_status_present(_make_entry_dict(status="accepted"), None)
        assert result is None

    def test_priority_present_checker(self):
        result = check_priority_present(_make_entry_dict(metadata="{}"), None)
        assert result is not None

        result = check_priority_present(
            _make_entry_dict(metadata=json.dumps({"priority": 5})), None
        )
        assert result is None

    def test_body_has_section_checker(self):
        checker = NAMED_CHECKERS["body_has_section"]

        entry = _make_entry_dict(body="Just some text")
        result = checker(entry, None, {"heading": "Problem"})
        assert result is not None

        entry = _make_entry_dict(body="## Problem\n\nSomething is wrong.")
        result = checker(entry, None, {"heading": "Problem"})
        assert result is None

    def test_body_has_code_block_checker(self):
        checker = NAMED_CHECKERS["body_has_code_block"]

        entry = _make_entry_dict(body="Just text, no code")
        result = checker(entry, None)
        assert result is not None

        entry = _make_entry_dict(body="Here is code:\n```python\nprint('hi')\n```")
        result = checker(entry, None)
        assert result is None


# =========================================================================
# TestRubricEvaluation — integration with QAService
# =========================================================================


class TestRubricEvaluation:
    """Integration tests: rubric checks via QAService.validate_entry()."""

    def test_entry_missing_tags_rubric_violation(self, rubric_setup):
        """Entry without tags gets rubric_violation warning."""
        db = rubric_setup["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body) VALUES (?, ?, ?, ?, ?)",
            ("no-tags-entry", "test-kb", "note", "Good Title", "Some body content"),
        )
        db._raw_conn.commit()

        result = rubric_setup["qa"].validate_entry(
            "no-tags-entry", "test-kb", readable_kbs=UNSCOPED
        )
        rubric_issues = [i for i in result["issues"] if i["rule"] == "rubric_violation"]
        tag_issues = [i for i in rubric_issues if i.get("field") == "tags"]
        assert len(tag_issues) >= 1

    def test_entry_with_generic_title_rubric_violation(self, rubric_setup):
        """Entry with generic title gets rubric_violation warning."""
        db = rubric_setup["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body) VALUES (?, ?, ?, ?, ?)",
            ("generic-title", "test-kb", "note", "TODO", "Some body"),
        )
        db._raw_conn.commit()

        result = rubric_setup["qa"].validate_entry(
            "generic-title", "test-kb", readable_kbs=UNSCOPED
        )
        rubric_issues = [i for i in result["issues"] if i["rule"] == "rubric_violation"]
        title_issues = [i for i in rubric_issues if i.get("field") == "title"]
        assert len(title_issues) >= 1
        assert title_issues[0]["severity"] == "warning"

    def test_person_missing_role_rubric_violation(self, rubric_setup):
        """Person entry without role metadata gets rubric_violation."""
        db = rubric_setup["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("no-role-person", "test-kb", "person", "Jane Doe", "A person", "{}"),
        )
        db._raw_conn.commit()

        result = rubric_setup["qa"].validate_entry(
            "no-role-person", "test-kb", readable_kbs=UNSCOPED
        )
        rubric_issues = [i for i in result["issues"] if i["rule"] == "rubric_violation"]
        role_issues = [i for i in rubric_issues if "role" in (i.get("field") or "")]
        assert len(role_issues) >= 1

    def test_document_missing_url_and_author_violation(self, rubric_setup):
        """Document entry missing both url and author gets rubric_violation."""
        db = rubric_setup["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("no-source-doc", "test-kb", "document", "Some Doc", "Content", "{}"),
        )
        db._raw_conn.commit()

        result = rubric_setup["qa"].validate_entry(
            "no-source-doc", "test-kb", readable_kbs=UNSCOPED
        )
        rubric_issues = [i for i in result["issues"] if i["rule"] == "rubric_violation"]
        source_issues = [
            i
            for i in rubric_issues
            if "url" in (i.get("message") or "") or "author" in (i.get("message") or "")
        ]
        assert len(source_issues) >= 1

    def test_already_covered_items_not_duplicated(self, rubric_setup):
        """Body/date rubric items should not produce rubric_violation issues."""
        db = rubric_setup["db"]
        # Entry with empty body — should get empty_body but NOT rubric_violation for body
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body) VALUES (?, ?, ?, ?, ?)",
            ("empty-body-test", "test-kb", "note", "Good Title", ""),
        )
        db._raw_conn.commit()

        result = rubric_setup["qa"].validate_entry(
            "empty-body-test", "test-kb", readable_kbs=UNSCOPED
        )
        # Should have empty_body rule
        body_rules = [i for i in result["issues"] if i["rule"] == "empty_body"]
        assert len(body_rules) >= 1

        # Should NOT have rubric_violation for "body is non-empty"
        rubric_body = [
            i
            for i in result["issues"]
            if i["rule"] == "rubric_violation" and "non-empty" in (i.get("rubric_item") or "")
        ]
        assert len(rubric_body) == 0


# =========================================================================
# TestRubricBulk — bulk validate_kb path
# =========================================================================


class TestRubricBulk:
    """Tests for bulk rubric validation via validate_kb."""

    def test_bulk_detects_missing_tags(self, rubric_setup):
        """validate_kb detects entries with no tags."""
        db = rubric_setup["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body) VALUES (?, ?, ?, ?, ?)",
            ("bulk-no-tags", "test-kb", "note", "Valid Title", "Valid body"),
        )
        db._raw_conn.commit()

        result = rubric_setup["qa"].validate_kb("test-kb", readable_kbs=UNSCOPED)
        rubric_issues = [i for i in result["issues"] if i["rule"] == "rubric_violation"]
        tag_issues = [
            i for i in rubric_issues if i.get("field") == "tags" and i["entry_id"] == "bulk-no-tags"
        ]
        assert len(tag_issues) >= 1

    def test_bulk_detects_generic_titles(self, rubric_setup):
        """validate_kb detects entries with generic titles."""
        db = rubric_setup["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body) VALUES (?, ?, ?, ?, ?)",
            ("bulk-generic", "test-kb", "note", "Untitled", "Valid body"),
        )
        db._raw_conn.commit()

        result = rubric_setup["qa"].validate_kb("test-kb", readable_kbs=UNSCOPED)
        rubric_issues = [i for i in result["issues"] if i["rule"] == "rubric_violation"]
        title_issues = [
            i
            for i in rubric_issues
            if i.get("field") == "title" and i["entry_id"] == "bulk-generic"
        ]
        assert len(title_issues) >= 1

    def test_bulk_detects_missing_outlinks(self, rubric_setup):
        """validate_kb detects entries with no outgoing links."""
        db = rubric_setup["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body) VALUES (?, ?, ?, ?, ?)",
            ("bulk-no-links", "test-kb", "note", "Valid Title", "Valid body"),
        )
        db._raw_conn.commit()

        result = rubric_setup["qa"].validate_kb("test-kb", readable_kbs=UNSCOPED)
        rubric_issues = [i for i in result["issues"] if i["rule"] == "rubric_violation"]
        link_issues = [
            i
            for i in rubric_issues
            if i.get("field") == "links" and i["entry_id"] == "bulk-no-links"
        ]
        assert len(link_issues) >= 1

    def test_bulk_person_missing_role(self, rubric_setup):
        """validate_kb detects person entries missing role."""
        db = rubric_setup["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("bulk-no-role", "test-kb", "person", "Bob Smith", "A person.", "{}"),
        )
        db._raw_conn.commit()

        result = rubric_setup["qa"].validate_kb("test-kb", readable_kbs=UNSCOPED)
        rubric_issues = [i for i in result["issues"] if i["rule"] == "rubric_violation"]
        role_issues = [
            i
            for i in rubric_issues
            if i["entry_id"] == "bulk-no-role" and "role" in (i.get("field") or "")
        ]
        assert len(role_issues) >= 1

    def test_bulk_document_missing_source(self, rubric_setup):
        """validate_kb detects document entries missing url/author."""
        db = rubric_setup["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("bulk-no-source", "test-kb", "document", "Some Report", "Content.", "{}"),
        )
        db._raw_conn.commit()

        result = rubric_setup["qa"].validate_kb("test-kb", readable_kbs=UNSCOPED)
        rubric_issues = [i for i in result["issues"] if i["rule"] == "rubric_violation"]
        source_issues = [
            i
            for i in rubric_issues
            if i["entry_id"] == "bulk-no-source"
            and ("url" in (i.get("message") or "") or "author" in (i.get("message") or ""))
        ]
        assert len(source_issues) >= 1

    def test_rubric_issues_have_correct_rule(self, rubric_setup):
        """All rubric issues use rule='rubric_violation'."""
        db = rubric_setup["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body) VALUES (?, ?, ?, ?, ?)",
            ("rule-check", "test-kb", "note", "TODO", ""),
        )
        db._raw_conn.commit()

        result = rubric_setup["qa"].validate_kb("test-kb", readable_kbs=UNSCOPED)
        rubric_issues = [i for i in result["issues"] if i["rule"] == "rubric_violation"]
        assert len(rubric_issues) > 0
        for issue in rubric_issues:
            assert issue["rule"] == "rubric_violation"
            assert issue["severity"] == "warning"
