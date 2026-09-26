"""Tests for named rubric checkers — explicit binding between rubric items and checkers."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.services.qa_service import QAService
from pyrite.services.rubric_checkers import (
    NAMED_CHECKERS,
    check_body_has_code_block,
    check_body_has_pattern,
    check_body_has_section,
    check_has_any_field,
    check_has_field,
)
from pyrite.storage.database import PyriteDB
from pyrite.services.access_policy import UNSCOPED


# =========================================================================
# Helpers
# =========================================================================


def _make_entry_dict(**overrides):
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
# TestNamedCheckerRegistry
# =========================================================================


class TestNamedCheckerRegistry:
    """Tests for the NAMED_CHECKERS dict."""

    def test_all_expected_names_registered(self):
        expected = {
            "descriptive_title",
            "has_tags",
            "has_outlinks",
            "status_present",
            "priority_present",
            "has_field",
            "has_any_field",
            "body_has_section",
            "body_has_pattern",
            "body_has_code_block",
            "not_oversized",
            "orphan_backlog_item",
        }
        assert expected == set(NAMED_CHECKERS.keys())

    def test_all_checkers_are_callable(self):
        for name, fn in NAMED_CHECKERS.items():
            assert callable(fn), f"Checker '{name}' is not callable"


# =========================================================================
# TestParameterizedCheckers
# =========================================================================


class TestParameterizedCheckers:
    """Tests for parameterized checkers that use params dict."""

    def test_has_field_with_params(self):
        entry = _make_entry_dict(metadata=json.dumps({"role": "Engineer"}))
        result = check_has_field(entry, None, {"field": "role"})
        assert result is None

    def test_has_field_missing(self):
        entry = _make_entry_dict(metadata="{}")
        result = check_has_field(entry, None, {"field": "role"})
        assert result is not None
        assert result["rule"] == "rubric_violation"
        assert "role" in result["message"]

    def test_has_field_no_params(self):
        entry = _make_entry_dict()
        result = check_has_field(entry, None, None)
        assert result is None  # No params = no check

    def test_has_any_field_first_present(self):
        entry = _make_entry_dict(metadata=json.dumps({"url": "https://example.com"}))
        result = check_has_any_field(entry, None, {"fields": ["url", "author"]})
        assert result is None

    def test_has_any_field_second_present(self):
        entry = _make_entry_dict(metadata=json.dumps({"author": "Jane"}))
        result = check_has_any_field(entry, None, {"fields": ["url", "author"]})
        assert result is None

    def test_has_any_field_none_present(self):
        entry = _make_entry_dict(metadata="{}")
        result = check_has_any_field(entry, None, {"fields": ["url", "author"]})
        assert result is not None
        assert "url" in result["message"]

    def test_body_has_section(self):
        entry = _make_entry_dict(body="## Problem\n\nSomething broke.")
        result = check_body_has_section(entry, None, {"heading": "Problem"})
        assert result is None

    def test_body_has_section_missing(self):
        entry = _make_entry_dict(body="Just some text")
        result = check_body_has_section(entry, None, {"heading": "Problem"})
        assert result is not None
        assert "Problem" in result["message"]

    def test_body_has_pattern(self):
        entry = _make_entry_dict(body="Sources: https://example.com")
        result = check_body_has_pattern(entry, None, {"pattern": r"Sources?:"})
        assert result is None

    def test_body_has_pattern_missing(self):
        entry = _make_entry_dict(body="No references here")
        result = check_body_has_pattern(entry, None, {"pattern": r"Sources?:"})
        assert result is not None

    def test_body_has_code_block_present(self):
        entry = _make_entry_dict(body="```python\nprint('hi')\n```")
        result = check_body_has_code_block(entry, None)
        assert result is None

    def test_body_has_code_block_missing(self):
        entry = _make_entry_dict(body="Just text, no code")
        result = check_body_has_code_block(entry, None)
        assert result is not None

    def test_rubric_text_from_params(self):
        """Checker uses rubric_text from params when available."""
        entry = _make_entry_dict(metadata="{}")
        result = check_has_field(entry, None, {"field": "role", "rubric_text": "Person has a role"})
        assert result is not None
        assert result["rubric_item"] == "Person has a role"


# =========================================================================
# TestExistingCheckersWithParams
# =========================================================================


class TestExistingCheckersWithParams:
    """Verify existing checkers work with params=None (backward compat)."""

    def test_descriptive_title_no_params(self):
        from pyrite.services.rubric_checkers import check_descriptive_title

        entry = _make_entry_dict(title="TODO")
        result = check_descriptive_title(entry, None)
        assert result is not None

    def test_has_tags_no_params(self):
        from pyrite.services.rubric_checkers import check_has_tags

        entry = _make_entry_dict(_tag_count=0)
        result = check_has_tags(entry, None)
        assert result is not None

    def test_descriptive_title_with_params(self):
        from pyrite.services.rubric_checkers import check_descriptive_title

        entry = _make_entry_dict(title="TODO")
        result = check_descriptive_title(entry, None, {"rubric_text": "Custom text"})
        assert result is not None
        assert result["rubric_item"] == "Custom text"


# =========================================================================
# TestPluginRubricCheckers
# =========================================================================


class TestPluginRubricCheckers:
    """Test plugin checker aggregation in registry."""

    def test_plugin_checkers_aggregated(self):
        from pyrite.plugins.registry import PluginRegistry

        def custom_checker(entry, schema, params=None):
            return None

        from pyrite.plugins.capabilities import Capability

        class FakePlugin:
            name = "test_plugin"
            # get_rubric_checkers is a DOMAIN-capability method
            # (Tier A r1500); declared so the registry's dispatch-skip
            # lets it through.
            capabilities = {Capability.DOMAIN}

            def get_rubric_checkers(self):
                return {"test_plugin.custom": custom_checker}

        registry = PluginRegistry.__new__(PluginRegistry)
        registry._plugins = {"test_plugin": FakePlugin()}
        registry._discovered = True
        registry._entry_points_scanned = True

        checkers = registry.get_all_rubric_checkers()
        assert "test_plugin.custom" in checkers
        assert "descriptive_title" in checkers  # core checkers still present


# =========================================================================
# TestMixedFormatRubricItems — QAService integration
# =========================================================================


@pytest.fixture
def named_rubric_setup():
    """Create a QAService for testing named rubric evaluation."""
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
            description="Test KB for named rubric",
        )

        config = PyriteConfig(
            knowledge_bases=[kb_config],
            settings=Settings(index_path=db_path),
        )

        from pyrite.models import PersonEntry
        from pyrite.storage.index import IndexManager
        from pyrite.storage.repository import KBRepository

        repo = KBRepository(kb_config)
        person = PersonEntry.create(name="Jane Doe", importance=5)
        person.body = "A person entry."
        person.tags = ["test"]
        repo.save(person)

        db = PyriteDB(db_path)
        index_mgr = IndexManager(db, config)
        index_mgr.index_all()

        qa = QAService(config, db)

        yield {"qa": qa, "config": config, "db": db, "repo": repo, "index_mgr": index_mgr}

        db.close()


class TestMixedFormatRubricEvaluation:
    """Integration tests for mixed-format rubric items in QA evaluation."""

    def _get_person_id(self, named_rubric_setup):
        """Get the ID of the test person entry."""
        db = named_rubric_setup["db"]
        rows = db.execute_sql(
            "SELECT id FROM entry WHERE kb_name = 'test-kb' AND entry_type = 'person'"
        )
        assert rows, "Expected a person entry in test DB"
        return rows[0]["id"]

    def test_named_checker_lookup(self, named_rubric_setup):
        """Dict rubric items with checker key resolve to correct checker."""
        qa = named_rubric_setup["qa"]
        person_id = self._get_person_id(named_rubric_setup)

        # Person entry without role should get flagged by named checker
        result = qa.validate_entry(person_id, "test-kb", readable_kbs=UNSCOPED)
        rubric_issues = [i for i in result["issues"] if i["rule"] == "rubric_violation"]
        role_issues = [i for i in rubric_issues if "role" in (i.get("field") or "")]
        assert len(role_issues) >= 1

    def test_covered_by_schema_skipped(self, named_rubric_setup):
        """Items with covered_by: schema are skipped in evaluation."""
        qa = named_rubric_setup["qa"]
        person_id = self._get_person_id(named_rubric_setup)

        # "Entry body is non-empty" is now covered_by: schema in SYSTEM_INTENT
        result = qa.validate_entry(person_id, "test-kb", readable_kbs=UNSCOPED)
        body_rubric = [
            i
            for i in result["issues"]
            if i["rule"] == "rubric_violation" and "non-empty" in (i.get("rubric_item") or "")
        ]
        assert len(body_rubric) == 0

    def test_unknown_checker_warning(self, named_rubric_setup):
        """Unknown checker name produces config_error warning."""
        qa = named_rubric_setup["qa"]
        person_id = self._get_person_id(named_rubric_setup)

        # Patch _get_rubric_items to inject an unknown checker
        original = qa._get_rubric_items

        def patched(entry_type, kb_name):
            items = original(entry_type, kb_name)
            items.append({"text": "Test item", "checker": "nonexistent_checker"})
            return items

        qa._get_rubric_items = patched

        result = qa.validate_entry(person_id, "test-kb", readable_kbs=UNSCOPED)
        config_errors = [i for i in result["issues"] if i["rule"] == "config_error"]
        assert len(config_errors) >= 1
        assert "nonexistent_checker" in config_errors[0]["message"]

    def test_judgment_only_collected(self, named_rubric_setup):
        """Plain strings and dicts without checker are collected as judgment items."""
        qa = named_rubric_setup["qa"]

        # Patch _get_rubric_items to add judgment items
        original = qa._get_rubric_items

        def patched(entry_type, kb_name):
            items = original(entry_type, kb_name)
            items.append("This is a judgment-only string")
            items.append({"text": "This is a judgment-only dict"})
            return items

        qa._get_rubric_items = patched

        judgment = qa._collect_judgment_items("person", "test-kb")
        assert "This is a judgment-only string" in judgment
        assert "This is a judgment-only dict" in judgment

    def test_judgment_excludes_checker_bound(self, named_rubric_setup):
        """Items with checker key are not collected as judgment items."""
        qa = named_rubric_setup["qa"]

        judgment = qa._collect_judgment_items("person", "test-kb")
        # SYSTEM_INTENT items have checkers now, so they should NOT be in judgment
        assert not any("descriptive title" in j.lower() for j in judgment)
        assert not any("at least one tag" in j.lower() for j in judgment)

    def test_judgment_excludes_covered_by(self, named_rubric_setup):
        """Items with covered_by are not collected as judgment items."""
        qa = named_rubric_setup["qa"]

        judgment = qa._collect_judgment_items("person", "test-kb")
        assert not any("non-empty" in j.lower() for j in judgment)

    def test_legacy_regex_fallback(self, named_rubric_setup):
        """Plain string rubric items still matched by regex patterns."""
        qa = named_rubric_setup["qa"]

        # Inject a plain string that matches a regex pattern
        original = qa._get_rubric_items

        def patched(entry_type, kb_name):
            items = original(entry_type, kb_name)
            items.append("Entry has a descriptive title")  # matches regex
            return items

        qa._get_rubric_items = patched

        db = named_rubric_setup["db"]
        # Insert an entry with a generic title
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body) VALUES (?, ?, ?, ?, ?)",
            ("generic-title", "test-kb", "note", "TODO", "Some body"),
        )
        db._raw_conn.commit()

        result = qa.validate_entry("generic-title", "test-kb", readable_kbs=UNSCOPED)
        title_issues = [
            i
            for i in result["issues"]
            if i["rule"] == "rubric_violation" and i.get("field") == "title"
        ]
        assert len(title_issues) >= 1


# =========================================================================
# TestGetRubricItems — deduplication
# =========================================================================


class TestGetRubricItemsDedup:
    """Test _get_rubric_items deduplication with mixed formats."""

    def test_dict_overrides_string_with_same_text(self, named_rubric_setup):
        """When same text appears as string and dict, dict wins."""
        qa = named_rubric_setup["qa"]
        items = qa._get_rubric_items("note", "test-kb")

        # SYSTEM_INTENT items are now dicts — check they're present as dicts
        title_items = [
            i for i in items if isinstance(i, dict) and "descriptive title" in i.get("text", "")
        ]
        assert len(title_items) == 1
        assert title_items[0]["checker"] == "descriptive_title"

    def test_no_duplicate_text(self, named_rubric_setup):
        """No two items should have the same text content."""
        qa = named_rubric_setup["qa"]
        items = qa._get_rubric_items("person", "test-kb")

        texts = []
        for item in items:
            if isinstance(item, dict):
                texts.append(item.get("text", ""))
            else:
                texts.append(item)
        # Remove empty strings
        texts = [t for t in texts if t]
        assert len(texts) == len(set(texts)), f"Duplicate rubric items found: {texts}"
