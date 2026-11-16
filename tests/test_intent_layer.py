"""Tests for the intent layer: guidelines, goals, and evaluation rubrics."""

import pytest

from pyrite.schema.core_types import SYSTEM_INTENT, resolve_type_metadata
from pyrite.schema.field_schema import TypeSchema
from pyrite.schema.kb_schema import KBSchema


class TestTypeSchemaIntent:
    """Intent fields on TypeSchema."""

    def test_default_intent_fields_empty(self):
        ts = TypeSchema(name="note")
        assert ts.guidelines == ""
        assert ts.goals == ""
        assert ts.evaluation_rubric == []

    def test_to_dict_omits_empty_intent(self):
        ts = TypeSchema(name="note")
        d = ts.to_dict()
        assert "guidelines" not in d
        assert "goals" not in d
        assert "evaluation_rubric" not in d

    def test_to_dict_includes_nonempty_intent(self):
        ts = TypeSchema(
            name="note",
            guidelines="Write clearly.",
            goals="Capture knowledge.",
            evaluation_rubric=["Has title", "Has body"],
        )
        d = ts.to_dict()
        assert d["guidelines"] == "Write clearly."
        assert d["goals"] == "Capture knowledge."
        assert d["evaluation_rubric"] == ["Has title", "Has body"]


class TestKBSchemaIntentParsing:
    """Parsing intent fields from kb.yaml data."""

    def test_parse_kb_level_intent(self):
        data = {
            "name": "test",
            "guidelines": {"quality": "Be thorough"},
            "goals": {"primary": "Document everything"},
            "evaluation_rubric": ["Entry has title"],
        }
        schema = KBSchema.from_dict(data)
        assert schema.guidelines == {"quality": "Be thorough"}
        assert schema.goals == {"primary": "Document everything"}
        assert schema.evaluation_rubric == ["Entry has title"]

    def test_parse_type_level_intent(self):
        data = {
            "name": "test",
            "types": {
                "note": {
                    "description": "A note",
                    "guidelines": "Keep it focused.",
                    "goals": "Single-topic notes.",
                    "evaluation_rubric": ["Note has tags"],
                }
            },
        }
        schema = KBSchema.from_dict(data)
        note_type = schema.types["note"]
        assert note_type.guidelines == "Keep it focused."
        assert note_type.goals == "Single-topic notes."
        assert note_type.evaluation_rubric == ["Note has tags"]

    def test_missing_intent_defaults_to_empty(self):
        data = {"name": "test"}
        schema = KBSchema.from_dict(data)
        assert schema.guidelines == {}
        assert schema.goals == {}
        assert schema.evaluation_rubric == []


class TestAgentSchemaIntent:
    """to_agent_schema() includes intent fields."""

    def test_system_defaults_present_when_no_kb_intent(self):
        schema = KBSchema.from_dict({"name": "test"})
        agent = schema.to_agent_schema(kb_type="generic")
        # System default guidelines should be present
        assert "guidelines" in agent
        assert "sourcing" in agent["guidelines"]
        assert "cross_linking" in agent["guidelines"]
        assert "completeness" in agent["guidelines"]
        # System default rubric should be present
        assert "evaluation_rubric" in agent
        assert len(agent["evaluation_rubric"]) == len(SYSTEM_INTENT["evaluation_rubric"])

    def test_kb_guidelines_override_system(self):
        data = {
            "name": "test",
            "guidelines": {"sourcing": "Use only primary sources."},
        }
        schema = KBSchema.from_dict(data)
        agent = schema.to_agent_schema(kb_type="generic")
        # KB override wins
        assert agent["guidelines"]["sourcing"] == "Use only primary sources."
        # Other system defaults still present
        assert "cross_linking" in agent["guidelines"]

    def test_kb_rubric_merges_with_system(self):
        data = {
            "name": "test",
            "evaluation_rubric": ["Custom rubric item"],
        }
        schema = KBSchema.from_dict(data)
        agent = schema.to_agent_schema(kb_type="generic")
        rubric = agent["evaluation_rubric"]
        # System items present (now dicts with text/checker keys)
        rubric_texts = [i.get("text", "") if isinstance(i, dict) else i for i in rubric]
        assert "Entry has a descriptive title" in rubric_texts
        # Custom item appended
        assert "Custom rubric item" in rubric

    def test_kb_goals_in_output(self):
        data = {
            "name": "test",
            "goals": {"primary": "Be the best KB"},
        }
        schema = KBSchema.from_dict(data)
        agent = schema.to_agent_schema(kb_type="generic")
        assert agent["goals"] == {"primary": "Be the best KB"}

    def test_no_goals_key_when_empty(self):
        schema = KBSchema.from_dict({"name": "test"})
        agent = schema.to_agent_schema(kb_type="generic")
        assert "goals" not in agent

    def test_type_level_intent_in_agent_schema(self):
        data = {
            "name": "test",
            "types": {
                "note": {
                    "description": "A note",
                    "guidelines": "Stay focused.",
                    "goals": "Single topic.",
                    "evaluation_rubric": ["Has tags"],
                }
            },
        }
        schema = KBSchema.from_dict(data)
        agent = schema.to_agent_schema(kb_type="generic")
        note = agent["types"]["note"]
        assert note["guidelines"] == "Stay focused."
        assert note["goals"] == "Single topic."
        assert note["evaluation_rubric"] == ["Has tags"]

    def test_core_type_rubric_in_agent_schema(self):
        """Core types with evaluation_rubric in CORE_TYPE_METADATA appear in output."""
        schema = KBSchema.from_dict({"name": "test"})
        agent = schema.to_agent_schema(kb_type="generic")
        # Event has core rubric items (now dicts with text/covered_by)
        event = agent["types"]["event"]
        assert "evaluation_rubric" in event
        rubric_texts = [
            i.get("text", "") if isinstance(i, dict) else i for i in event["evaluation_rubric"]
        ]
        assert "Event has a date field" in rubric_texts


class TestResolveTypeMetadataIntent:
    """resolve_type_metadata() includes intent fields."""

    def test_core_type_rubric_resolved(self):
        meta = resolve_type_metadata("event")
        rubric_texts = [
            i.get("text", "") if isinstance(i, dict) else i for i in meta["evaluation_rubric"]
        ]
        assert "Event has a date field" in rubric_texts

    def test_kb_override_intent(self):
        schema = KBSchema.from_dict(
            {
                "name": "test",
                "types": {
                    "event": {
                        "description": "An event",
                        "guidelines": "Always include location.",
                        "goals": "Track what happened.",
                        "evaluation_rubric": ["Custom event rubric"],
                    }
                },
            }
        )
        meta = resolve_type_metadata("event", schema)
        assert meta["guidelines"] == "Always include location."
        assert meta["goals"] == "Track what happened."
        assert meta["evaluation_rubric"] == ["Custom event rubric"]

    def test_empty_intent_for_unknown_type(self):
        meta = resolve_type_metadata("nonexistent_type_xyz")
        assert meta["guidelines"] == ""
        assert meta["goals"] == ""
        assert meta["evaluation_rubric"] == []
