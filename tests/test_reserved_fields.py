"""Tests for reserved field name validation."""

import dataclasses
import logging

import pytest


# ── Section 1: RESERVED_FIELD_NAMES completeness ──


class TestReservedFieldNamesCompleteness:
    """Verify RESERVED_FIELD_NAMES contains all expected field names."""

    def test_contains_entry_base_fields(self):
        """All Entry dataclass fields must be reserved."""
        from pyrite.models.base import Entry
        from pyrite.schema.reserved import RESERVED_FIELD_NAMES

        entry_fields = {f.name for f in dataclasses.fields(Entry)}
        # kb_name is runtime-only, not a frontmatter field, but still reserved
        missing = entry_fields - RESERVED_FIELD_NAMES
        assert not missing, f"Entry fields missing from RESERVED_FIELD_NAMES: {missing}"

    def test_does_not_contain_protocol_fields(self):
        """Protocol fields should NOT be reserved — users define constraints on them in kb.yaml."""
        from pyrite.models.protocols import PROTOCOL_FIELDS
        from pyrite.schema.reserved import RESERVED_FIELD_NAMES

        overlap = set(PROTOCOL_FIELDS.keys()) & RESERVED_FIELD_NAMES
        assert not overlap, f"Protocol fields should not be in RESERVED_FIELD_NAMES: {overlap}"

    def test_contains_type_key(self):
        """The 'type' frontmatter key must be reserved."""
        from pyrite.schema.reserved import RESERVED_FIELD_NAMES

        assert "type" in RESERVED_FIELD_NAMES


# ── Section 2: KBSchema collision validation ──


class TestKBSchemaCollisionValidation:
    """KBSchema.from_dict() should strip colliding field names."""

    def test_strips_entry_warnings_answer_key_and_warns(self, caplog):
        from pyrite.schema.kb_schema import KBSchema

        fields = {"entry_warnings": {"type": "text"}, "reviewer": {"type": "text"}}
        with caplog.at_level(logging.WARNING):
            schema = KBSchema.from_dict({"types": {"report": {"fields": fields}}})

        assert set(schema.types["report"].fields) == {"reviewer"}
        assert any(
            record.levelno == logging.WARNING
            and "entry_warnings" in record.getMessage()
            and "reserved" in record.getMessage()
            for record in caplog.records
        )

    def test_strips_colliding_field_and_warns(self, caplog):
        """A custom field named 'summary' collides with Entry.summary."""
        from pyrite.schema.kb_schema import KBSchema

        data = {
            "types": {
                "my_type": {
                    "fields": {
                        "summary": {"type": "text", "required": True},
                        "custom_field": {"type": "text"},
                    }
                }
            }
        }
        with caplog.at_level(logging.WARNING):
            schema = KBSchema.from_dict(data)

        ts = schema.types["my_type"]
        assert "summary" not in ts.fields, "Colliding field should be stripped"
        assert "custom_field" in ts.fields, "Non-colliding field should remain"
        assert "collide" in caplog.text.lower() or "reserved" in caplog.text.lower()

    def test_allows_non_colliding_fields(self):
        """Custom fields that don't collide should pass through."""
        from pyrite.schema.kb_schema import KBSchema

        data = {
            "types": {
                "my_type": {
                    "fields": {
                        "difficulty": {"type": "select", "options": ["easy", "hard"]},
                        "reviewer": {"type": "text"},
                    }
                }
            }
        }
        schema = KBSchema.from_dict(data)
        ts = schema.types["my_type"]
        assert "difficulty" in ts.fields
        assert "reviewer" in ts.fields

    def test_allows_protocol_fields_with_constraints(self):
        """Protocol fields (status, priority) are allowed — users add validation constraints."""
        from pyrite.schema.kb_schema import KBSchema

        data = {
            "types": {
                "task": {
                    "fields": {
                        "status": {"type": "select", "options": ["open", "closed"]},
                        "priority": {"type": "select", "options": ["low", "medium", "high"]},
                        "effort": {"type": "number"},
                    }
                }
            }
        }
        schema = KBSchema.from_dict(data)
        ts = schema.types["task"]
        assert "status" in ts.fields
        assert "priority" in ts.fields
        assert "effort" in ts.fields

    def test_strips_base_entry_field_collision(self, caplog):
        """A custom field named 'body' collides with Entry.body."""
        from pyrite.schema.kb_schema import KBSchema

        data = {
            "types": {
                "my_type": {
                    "fields": {
                        "body": {"type": "text"},
                        "custom_field": {"type": "text"},
                    }
                }
            }
        }
        with caplog.at_level(logging.WARNING):
            schema = KBSchema.from_dict(data)

        ts = schema.types["my_type"]
        assert "body" not in ts.fields
        assert "custom_field" in ts.fields


# ── Section 3: _KNOWN_KEYS fix ──


class TestKnownKeysCompleteness:
    """_KNOWN_KEYS must include importance and lifecycle."""

    def test_importance_not_in_metadata(self):
        """importance=3 in frontmatter should become entry.importance, not metadata."""
        from pyrite.models.generic import GenericEntry

        meta = {
            "id": "test-1",
            "title": "Test",
            "type": "custom",
            "importance": 3,
        }
        entry = GenericEntry.from_frontmatter(meta, "body")
        assert entry.importance == 3
        assert "importance" not in entry.metadata

    def test_lifecycle_not_in_metadata(self):
        """lifecycle in frontmatter should become entry.lifecycle, not metadata."""
        from pyrite.models.generic import GenericEntry

        meta = {
            "id": "test-2",
            "title": "Test",
            "type": "custom",
            "lifecycle": "archived",
        }
        entry = GenericEntry.from_frontmatter(meta, "body")
        assert entry.lifecycle == "archived"
        assert "lifecycle" not in entry.metadata


# ── Section 4: PROTOCOL_COLUMN_KEYS ──


class TestProtocolColumnKeys:
    """PROTOCOL_COLUMN_KEYS should match PROTOCOL_FIELDS keys."""

    def test_matches_protocol_fields(self):
        from pyrite.models.protocols import PROTOCOL_COLUMN_KEYS, PROTOCOL_FIELDS

        assert PROTOCOL_COLUMN_KEYS == frozenset(PROTOCOL_FIELDS.keys())

    def test_used_in_index_manager(self):
        """IndexManager should use PROTOCOL_COLUMN_KEYS, not a hardcoded set."""
        import inspect

        from pyrite.storage.index import IndexManager

        source = inspect.getsource(IndexManager._entry_to_dict)
        assert "PROTOCOL_COLUMN_KEYS" in source, (
            "IndexManager._entry_to_row should use PROTOCOL_COLUMN_KEYS"
        )
