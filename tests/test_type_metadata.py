"""Tests for type metadata: CORE_TYPE_METADATA, resolve_type_metadata, TypeSchema extensions."""

import pytest

from pyrite.schema import (
    CORE_TYPE_METADATA,
    CORE_TYPES,
    KBSchema,
    TypeSchema,
    resolve_type_metadata,
)


class TestCoreTypeMetadata:
    """Tests for the CORE_TYPE_METADATA dict."""

    def test_has_entries_for_all_core_types(self):
        """CORE_TYPE_METADATA should have entries for all 8 core types."""
        for type_name in CORE_TYPES:
            assert type_name in CORE_TYPE_METADATA, f"Missing metadata for core type '{type_name}'"

    def test_each_core_type_has_required_keys(self):
        """Each core type metadata should have ai_instructions, field_descriptions, protocols, display."""
        for type_name, meta in CORE_TYPE_METADATA.items():
            assert "ai_instructions" in meta, f"{type_name}: missing ai_instructions"
            assert isinstance(meta["ai_instructions"], str)
            assert len(meta["ai_instructions"]) > 0, f"{type_name}: empty ai_instructions"

            assert "field_descriptions" in meta, f"{type_name}: missing field_descriptions"
            assert isinstance(meta["field_descriptions"], dict)

            assert "protocols" in meta, f"{type_name}: missing protocols"
            assert isinstance(meta["protocols"], list)

            assert "display" in meta, f"{type_name}: missing display"
            assert isinstance(meta["display"], dict)
            assert "icon" in meta["display"], f"{type_name}: display missing icon"

    def test_exactly_nine_core_types(self):
        """There should be exactly 9 core types with metadata."""
        assert len(CORE_TYPE_METADATA) == 9

    def test_protocol_declarations_match_classes(self):
        """Protocol declarations in metadata should match actual mixin inheritance."""
        from pyrite.models.core_types import (
            EventEntry,
            OrganizationEntry,
            PersonEntry,
        )
        from pyrite.models.protocols import PROTOCOL_REGISTRY

        # Map core types to their entry classes
        type_classes = {
            "event": EventEntry,
            "person": PersonEntry,
            "organization": OrganizationEntry,
        }
        for type_name, cls in type_classes.items():
            declared = set(CORE_TYPE_METADATA[type_name]["protocols"])
            actual = {
                name for name, mixin_cls in PROTOCOL_REGISTRY.items() if issubclass(cls, mixin_cls)
            }
            assert declared == actual, f"{type_name}: declared={declared}, actual={actual}"


class TestResolveTypeMetadata:
    """Tests for resolve_type_metadata()."""

    def test_core_defaults_returned(self):
        """Core defaults should be returned for a known core type with no overrides."""
        result = resolve_type_metadata("note")
        assert result["ai_instructions"] == CORE_TYPE_METADATA["note"]["ai_instructions"]
        assert result["field_descriptions"] == CORE_TYPE_METADATA["note"]["field_descriptions"]
        assert result["protocols"] == CORE_TYPE_METADATA["note"]["protocols"]
        assert result["display"] == CORE_TYPE_METADATA["note"]["display"]

    def test_event_protocols_resolved(self):
        """Event type should resolve with temporal, locatable, statusable protocols."""
        result = resolve_type_metadata("event")
        assert set(result["protocols"]) == {"temporal", "locatable", "statusable"}

    def test_unknown_type_returns_empty(self):
        """Unknown type should return empty defaults."""
        result = resolve_type_metadata("totally_unknown_type_xyz")
        assert result["ai_instructions"] == ""
        assert result["field_descriptions"] == {}
        assert result["protocols"] == []
        assert result["display"] == {}

    def test_kb_overrides_core_defaults(self):
        """KB-level ai_instructions should override core defaults."""
        schema = KBSchema.from_dict(
            {
                "types": {
                    "note": {
                        "ai_instructions": "Custom note instructions from kb.yaml",
                        "field_descriptions": {"custom_field": "A custom field"},
                        "display": {"icon": "custom-icon"},
                    }
                }
            }
        )
        result = resolve_type_metadata("note", schema)
        assert result["ai_instructions"] == "Custom note instructions from kb.yaml"
        # custom_field should be merged with core field_descriptions
        assert "custom_field" in result["field_descriptions"]
        # Core field_descriptions should still be present (merged)
        assert "title" in result["field_descriptions"]
        # Display override
        assert result["display"]["icon"] == "custom-icon"

    def test_kb_partial_override(self):
        """KB override of only ai_instructions should keep other core defaults."""
        schema = KBSchema.from_dict(
            {
                "types": {
                    "event": {
                        "ai_instructions": "Only override instructions",
                    }
                }
            }
        )
        result = resolve_type_metadata("event", schema)
        assert result["ai_instructions"] == "Only override instructions"
        # field_descriptions and display should come from core
        assert result["field_descriptions"] == CORE_TYPE_METADATA["event"]["field_descriptions"]
        assert result["display"] == CORE_TYPE_METADATA["event"]["display"]

    def test_empty_kb_override_does_not_replace(self):
        """Empty string ai_instructions in KB should not override core default."""
        schema = KBSchema.from_dict(
            {
                "types": {
                    "note": {
                        "ai_instructions": "",
                    }
                }
            }
        )
        result = resolve_type_metadata("note", schema)
        # Empty string is falsy, so core default should remain
        assert result["ai_instructions"] == CORE_TYPE_METADATA["note"]["ai_instructions"]


class TestTypeSchemaNewFields:
    """Tests for TypeSchema's new fields: ai_instructions, field_descriptions, display."""

    def test_parse_from_dict(self):
        """TypeSchema should parse ai_instructions, field_descriptions, display from dict."""
        schema = KBSchema.from_dict(
            {
                "types": {
                    "custom_type": {
                        "description": "A custom type",
                        "ai_instructions": "Use this for custom things",
                        "field_descriptions": {"foo": "The foo field"},
                        "display": {"icon": "star", "color": "blue"},
                    }
                }
            }
        )
        ts = schema.types["custom_type"]
        assert ts.ai_instructions == "Use this for custom things"
        assert ts.field_descriptions == {"foo": "The foo field"}
        assert ts.display == {"icon": "star", "color": "blue"}

    def test_defaults_are_empty(self):
        """New fields should default to empty when not specified."""
        schema = KBSchema.from_dict(
            {
                "types": {
                    "minimal": {
                        "description": "Minimal type",
                    }
                }
            }
        )
        ts = schema.types["minimal"]
        assert ts.ai_instructions == ""
        assert ts.field_descriptions == {}
        assert ts.display == {}

    def test_to_dict_includes_new_fields(self):
        """TypeSchema.to_dict() should include new fields when set."""
        ts = TypeSchema(
            name="test",
            description="Test type",
            ai_instructions="Test instructions",
            field_descriptions={"f1": "Field one"},
            display={"icon": "test"},
        )
        d = ts.to_dict()
        assert d["ai_instructions"] == "Test instructions"
        assert d["field_descriptions"] == {"f1": "Field one"}
        assert d["display"] == {"icon": "test"}

    def test_to_dict_omits_empty_new_fields(self):
        """TypeSchema.to_dict() should omit new fields when empty."""
        ts = TypeSchema(name="test", description="Test type")
        d = ts.to_dict()
        assert "ai_instructions" not in d
        assert "field_descriptions" not in d
        assert "display" not in d


class TestToAgentSchema:
    """Tests for KBSchema.to_agent_schema() metadata inclusion."""

    def test_core_types_include_metadata(self):
        """to_agent_schema() should include metadata for core types."""
        schema = KBSchema()
        agent = schema.to_agent_schema()
        for type_name in CORE_TYPES:
            type_info = agent["types"][type_name]
            assert "ai_instructions" in type_info, f"{type_name}: missing ai_instructions"
            assert "field_descriptions" in type_info, f"{type_name}: missing field_descriptions"
            assert "display" in type_info, f"{type_name}: missing display"

    def test_custom_type_metadata_in_agent_schema(self):
        """Custom types with metadata should include it in agent schema."""
        schema = KBSchema.from_dict(
            {
                "types": {
                    "custom": {
                        "description": "Custom type",
                        "ai_instructions": "Custom instructions",
                        "field_descriptions": {"x": "The x field"},
                        "display": {"icon": "zap"},
                    }
                }
            }
        )
        agent = schema.to_agent_schema()
        custom = agent["types"]["custom"]
        assert custom["ai_instructions"] == "Custom instructions"
        assert custom["field_descriptions"] == {"x": "The x field"}
        assert custom["display"] == {"icon": "zap"}


class TestPluginTypeMetadata:
    """Tests for plugin type metadata integration."""

    @pytest.mark.control(
        reason="passes on dev, where no installed plugin returns type metadata; "
        "the test only breaks once cascade and journalism do (#697), and isolates itself now"
    )
    def test_registry_get_all_type_metadata_empty(self):
        """Registry with no plugins should return empty metadata."""
        from pyrite.plugins.registry import PluginRegistry

        registry = PluginRegistry()
        # "No plugins" means none discovered: a bare registry would discover the
        # installed ones (cascade and journalism declare field_aliases, #697).
        registry._discovered = True
        assert registry.get_all_type_metadata() == {}

    def test_registry_get_all_type_metadata_with_plugin(self):
        """Registry should aggregate type metadata from plugins."""
        from pyrite.plugins.registry import PluginRegistry

        class FakePlugin:
            name = "fake"

            def get_type_metadata(self):
                return {
                    "custom_type": {
                        "ai_instructions": "Plugin instructions",
                        "field_descriptions": {"pf": "Plugin field"},
                        "display": {"icon": "plug"},
                    }
                }

        registry = PluginRegistry()
        registry.register(FakePlugin())
        meta = registry.get_all_type_metadata()
        assert "custom_type" in meta
        assert meta["custom_type"]["ai_instructions"] == "Plugin instructions"
        assert meta["custom_type"]["field_descriptions"] == {"pf": "Plugin field"}
        assert meta["custom_type"]["display"] == {"icon": "plug"}


class TestSchemaAPIEndpoint:
    """Tests for the GET /api/kbs/{kb_name}/schema endpoint."""

    def test_schema_endpoint_returns_types(self, rest_api_env):
        """Schema endpoint should return type metadata."""
        client = rest_api_env["client"]
        kb_name = rest_api_env["events_kb"].name
        resp = client.get(f"/api/kbs/{kb_name}/schema")
        assert resp.status_code == 200
        data = resp.json()
        assert "types" in data
        # Core types should always be present
        assert "note" in data["types"]
        assert "event" in data["types"]
        # Metadata should be included
        assert "ai_instructions" in data["types"]["note"]

    def test_schema_endpoint_404_unknown_kb(self, rest_api_env):
        """Schema endpoint should 404 for unknown KB."""
        client = rest_api_env["client"]
        resp = client.get("/api/kbs/nonexistent_kb_xyz/schema")
        assert resp.status_code == 404
