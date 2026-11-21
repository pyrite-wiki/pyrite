"""
Tests for edge-type schema declarations.

Tests EndpointSpec dataclass, TypeSchema edge_type/endpoints fields,
and KBSchema round-trip parsing of edge type definitions.
"""

from pyrite.schema.field_schema import EndpointSpec, TypeSchema
from pyrite.schema.kb_schema import KBSchema


class TestEndpointSpec:
    """Test EndpointSpec dataclass basics."""

    def test_create_with_defaults(self):
        ep = EndpointSpec(field="source")
        assert ep.field == "source"
        assert ep.accepts == []

    def test_create_with_accepts(self):
        ep = EndpointSpec(field="target", accepts=["note", "person"])
        assert ep.field == "target"
        assert ep.accepts == ["note", "person"]

    def test_round_trip_fields(self):
        ep = EndpointSpec(field="actor", accepts=["person", "organization"])
        assert ep.field == "actor"
        assert ep.accepts == ["person", "organization"]


class TestTypeSchemaEdgeType:
    """Test TypeSchema with edge_type and endpoints fields."""

    def test_edge_type_defaults_false(self):
        ts = TypeSchema(name="note")
        assert ts.edge_type is False
        assert ts.endpoints == {}

    def test_to_dict_excludes_edge_type_when_false(self):
        ts = TypeSchema(name="note")
        d = ts.to_dict()
        assert "edge_type" not in d
        assert "endpoints" not in d

    def test_to_dict_includes_edge_type_when_true(self):
        ts = TypeSchema(
            name="authored_by",
            edge_type=True,
            endpoints={
                "source": EndpointSpec(field="source_id", accepts=["document"]),
                "target": EndpointSpec(field="target_id", accepts=["person"]),
            },
        )
        d = ts.to_dict()
        assert d["edge_type"] is True
        assert "endpoints" in d
        assert d["endpoints"]["source"] == {
            "field": "source_id",
            "accepts": ["document"],
        }
        assert d["endpoints"]["target"] == {
            "field": "target_id",
            "accepts": ["person"],
        }

    def test_to_dict_edge_type_true_no_endpoints(self):
        ts = TypeSchema(name="link", edge_type=True)
        d = ts.to_dict()
        assert d["edge_type"] is True
        assert "endpoints" not in d

    def test_to_dict_preserves_other_fields(self):
        """edge_type fields don't interfere with existing to_dict output."""
        ts = TypeSchema(
            name="authored_by",
            description="Authorship edge",
            edge_type=True,
            endpoints={
                "source": EndpointSpec(field="doc", accepts=["document"]),
            },
        )
        d = ts.to_dict()
        assert d["description"] == "Authorship edge"
        assert d["edge_type"] is True


class TestKBSchemaFromDictEdgeType:
    """Test KBSchema.from_dict() parsing of edge_type type definitions."""

    def test_parses_edge_type_flag(self):
        data = {
            "types": {
                "authored_by": {
                    "description": "Authorship relationship",
                    "edge_type": True,
                }
            }
        }
        schema = KBSchema.from_dict(data)
        ts = schema.types["authored_by"]
        assert ts.edge_type is True
        assert ts.endpoints == {}

    def test_parses_endpoints(self):
        data = {
            "types": {
                "authored_by": {
                    "description": "Authorship",
                    "edge_type": True,
                    "endpoints": {
                        "source": {"field": "document_id", "accepts": ["document", "note"]},
                        "target": {"field": "author_id", "accepts": ["person"]},
                    },
                }
            }
        }
        schema = KBSchema.from_dict(data)
        ts = schema.types["authored_by"]
        assert ts.edge_type is True
        assert len(ts.endpoints) == 2

        src = ts.endpoints["source"]
        assert src.field == "document_id"
        assert src.accepts == ["document", "note"]

        tgt = ts.endpoints["target"]
        assert tgt.field == "author_id"
        assert tgt.accepts == ["person"]

    def test_edge_type_defaults_false_when_absent(self):
        data = {
            "types": {
                "note": {
                    "description": "A plain note",
                }
            }
        }
        schema = KBSchema.from_dict(data)
        ts = schema.types["note"]
        assert ts.edge_type is False
        assert ts.endpoints == {}

    def test_endpoint_with_missing_accepts(self):
        """Endpoint without accepts list should default to empty."""
        data = {
            "types": {
                "link": {
                    "edge_type": True,
                    "endpoints": {
                        "from": {"field": "from_id"},
                    },
                }
            }
        }
        schema = KBSchema.from_dict(data)
        ep = schema.types["link"].endpoints["from"]
        assert ep.field == "from_id"
        assert ep.accepts == []

    def test_full_round_trip(self):
        """from_dict -> to_dict round-trip preserves edge type info."""
        data = {
            "types": {
                "collab": {
                    "description": "Collaboration edge",
                    "edge_type": True,
                    "endpoints": {
                        "left": {"field": "left_id", "accepts": ["person"]},
                        "right": {"field": "right_id", "accepts": ["person"]},
                    },
                }
            }
        }
        schema = KBSchema.from_dict(data)
        d = schema.types["collab"].to_dict()
        assert d["edge_type"] is True
        assert d["endpoints"]["left"]["field"] == "left_id"
        assert d["endpoints"]["right"]["accepts"] == ["person"]


class TestKBSchemaToAgentSchemaEdgeType:
    """Test that to_agent_schema() includes edge type info for custom types."""

    def test_agent_schema_includes_edge_type(self):
        data = {
            "types": {
                "authored_by": {
                    "description": "Authorship",
                    "edge_type": True,
                    "endpoints": {
                        "source": {"field": "doc_id", "accepts": ["document"]},
                        "target": {"field": "author_id", "accepts": ["person"]},
                    },
                }
            }
        }
        schema = KBSchema.from_dict(data)
        agent = schema.to_agent_schema(kb_type="generic")
        authored = agent["types"]["authored_by"]
        assert authored["edge_type"] is True
        assert "endpoints" in authored
        assert authored["endpoints"]["source"]["field"] == "doc_id"
