"""``orient`` publishes the field merge create already uses (#232).

``KBSchema.to_agent_schema`` is the schema block of ``pyrite orient``, MCP
``kb_orient`` and ``kb_schema``, REST ``/orient`` and ``/schema``, and
``pyrite-admin schema``. Its ``fields`` come from ``KBSchema.type_fields``:
core, then a plugin's ``get_field_schemas()``, then ``kb.yaml``. A ``kb.yaml``
field declaration replaces the plugin's or core's declaration of that field,
while untouched core fields keep their ``CORE_TYPES`` spellings. A plugin's
fields for one type do not appear on another.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from pyrite.plugins import get_registry
from pyrite.schema import KBSchema
from pyrite.schema.core_types import CORE_TYPES
from pyrite.services.kb_service import KBService
from tests._world import build_world

WIDGET = {
    "hue": {"type": "select", "options": ["red", "blue"]},
    "edges": {"type": "number"},
}
GADGET = {"size": {"type": "text"}}
REPO = Path(__file__).resolve().parents[1]


class _FieldPlugin:
    def __init__(self, name: str, schemas: dict | None = None, *, raises: bool = False):
        self.name = name
        self._schemas = schemas or {}
        self._raises = raises

    def get_field_schemas(self) -> dict:
        if self._raises:
            raise RuntimeError("field schemas unavailable")
        return self._schemas


@pytest.fixture
def field_plugins():
    """Register field-schema plugins for one test, then drop them."""
    reg = get_registry()
    reg.discover()
    names: list[str] = []

    def register(name: str, schemas: dict | None = None, *, raises: bool = False) -> None:
        reg.register(_FieldPlugin(name, schemas, raises=raises))
        names.append(name)

    yield register
    for name in names:
        reg._plugins.pop(name, None)
        reg._conformance_cache.pop(name, None)
    reg._aliases_changed()


def _published(schema: KBSchema, kb_type: str = "generic") -> dict:
    return schema.to_agent_schema(kb_type=kb_type)["types"]


def _merged(schema: KBSchema, entry_type: str) -> dict:
    return {name: field.to_dict() for name, field in schema.type_fields(entry_type).items()}


@pytest.mark.control(
    reason="untouched core fields keep CORE_TYPES spellings for core compatibility"
)
def test_untouched_core_fields_keep_core_spellings():
    """Core fields without plugin or kb.yaml overrides keep ``CORE_TYPES`` strings."""
    schema = KBSchema.from_dict({"name": "core-only"})
    types = _published(schema)
    person = types["person"]["fields"]
    assert person["role"] == CORE_TYPES["person"]["fields"]["role"] == "str"
    assert person["affiliations"] == CORE_TYPES["person"]["fields"]["affiliations"] == "list[str]"
    event = types["event"]["fields"]
    assert event["status"] == CORE_TYPES["event"]["fields"]["status"] == "EventStatus"
    assert event["importance"] == CORE_TYPES["event"]["fields"]["importance"] == "int"
    assert types["collection"]["fields"]["view_config"] == "dict"
    assert types["note"]["fields"] == CORE_TYPES["note"]["fields"]


def test_core_type_kb_yaml_override_and_addition_follow_the_merge():
    """A core type with kb.yaml ``fields:`` or ``optional:`` uses ``type_fields``."""
    schema = KBSchema.from_dict(
        {
            "name": "core-override",
            "types": {
                "person": {
                    "optional": ["nickname"],
                    "fields": {"role": {"type": "date"}},
                },
                "organization": {"description": "org"},
            },
        }
    )
    types = _published(schema)
    person = types["person"]["fields"]
    assert person["role"] == {"type": "date"}
    assert person["nickname"] == {"type": "text"}
    assert person["affiliations"] == "list[str]"
    assert types["organization"]["fields"] == CORE_TYPES["organization"]["fields"]
    assert "nickname" not in types["organization"]["fields"]


def test_plugin_fields_do_not_cross_types(field_plugins):
    field_plugins("domain_a", {"widget": WIDGET, "gadget": GADGET})
    schema = KBSchema.from_dict(
        {
            "name": "two-types",
            "types": {
                "note": {"description": "a note"},
                "widget": {"description": "widget prose"},
                "gadget": {"description": "gadget prose"},
            },
        }
    )
    types = _published(schema)
    assert types["widget"]["fields"] == _merged(schema, "widget")
    assert types["widget"]["fields"]["hue"]["options"] == ["red", "blue"]
    assert types["gadget"]["fields"] == _merged(schema, "gadget")
    assert "hue" not in types["gadget"]["fields"]
    assert "edges" not in types["gadget"]["fields"]
    assert "size" not in types["widget"]["fields"]
    assert "hue" not in types["note"]["fields"]
    assert "size" not in types["note"]["fields"]


def test_kb_yaml_replaces_one_plugin_field_and_leaves_the_rest(field_plugins):
    field_plugins("domain_a", {"widget": WIDGET, "gadget": GADGET})
    schema = KBSchema.from_dict(
        {
            "name": "override",
            "types": {
                "widget": {"fields": {"hue": {"type": "date"}}},
                "gadget": {"description": "gadget prose"},
            },
        }
    )
    types = _published(schema)
    assert types["widget"]["fields"] == _merged(schema, "widget")
    assert types["widget"]["fields"]["hue"] == {"type": "date"}
    assert types["widget"]["fields"]["edges"] == {"type": "number"}
    assert types["gadget"]["fields"] == {"size": {"type": "text"}}
    assert "hue" not in types["gadget"]["fields"]


def test_a_plugin_override_of_a_core_field_does_not_reach_another_core_type(field_plugins):
    field_plugins(
        "domain_a",
        {"person": {"role": {"type": "select", "options": ["a"]}, "badge": {"type": "text"}}},
    )
    schema = KBSchema.from_dict(
        {
            "name": "person-kb",
            "types": {"person": {"fields": {"role": {"type": "date"}}}},
        }
    )
    person = _published(schema)["person"]["fields"]
    assert person["role"] == {"type": "date"}
    assert person["badge"] == {"type": "text"}
    assert person["affiliations"] == "list[str]"
    assert "badge" not in schema.type_fields("organization")
    assert "badge" not in schema.type_fields("note")

    untouched = KBSchema.from_dict({"name": "all-core"})
    org = _published(untouched)["organization"]["fields"]
    assert "badge" not in org
    assert "role" not in org
    assert _published(untouched)["person"]["fields"]["badge"] == {"type": "text"}


def test_a_bare_kb_yaml_name_is_a_text_field():
    schema = KBSchema.from_dict(
        {"name": "bare", "types": {"widget": {"optional": ["kind"], "required": ["title"]}}}
    )
    fields = _published(schema)["widget"]["fields"]
    assert fields["kind"] == {"type": "text"}
    assert "title" not in fields
    assert fields == _merged(schema, "widget")


def test_a_raising_plugin_keeps_the_kb_yaml_field(field_plugins, caplog):
    field_plugins("broken_fields", raises=True)
    schema = KBSchema.from_dict(
        {"name": "kept", "types": {"widget": {"fields": {"hue": {"type": "text"}}}}}
    )
    with caplog.at_level(logging.WARNING, logger="pyrite.plugins.registry"):
        fields = _published(schema)["widget"]["fields"]
    assert fields["hue"] == {"type": "text"}
    assert any("get_field_schemas failed" in record.message for record in caplog.records)


def test_two_plugins_one_type_warns_and_does_not_mix_types(field_plugins, caplog):
    field_plugins("domain_a", {"widget": {"hue": {"type": "select", "options": ["red"]}}})
    field_plugins(
        "domain_b",
        {
            "widget": {
                "hue": {"type": "select", "options": ["blue"]},
                "only_b": {"type": "number"},
            },
            "gadget": {"size": {"type": "text"}},
        },
    )
    schema = KBSchema.from_dict(
        {
            "name": "both",
            "types": {
                "widget": {"description": "widget prose"},
                "gadget": {"description": "gadget prose"},
            },
        }
    )
    with caplog.at_level(logging.WARNING, logger="pyrite.plugins.registry"):
        types = _published(schema)
    widget = types["widget"]["fields"]
    assert widget["hue"]["options"] == ["blue"]
    assert widget["only_b"] == {"type": "number"}
    assert "size" not in widget
    assert types["gadget"]["fields"] == {"size": {"type": "text"}}
    assert "hue" not in types["gadget"]["fields"]
    warnings = [record.message for record in caplog.records if "field schema" in record.message]
    assert warnings
    assert any("domain_a" in message and "domain_b" in message for message in warnings)


def test_malformed_plugin_field_schemas_do_not_clobber_valid_fields(field_plugins):
    """Non-dict plugin/type/field returns are ignored and never erase valid fields."""
    field_plugins("valid_plugin", {"widget": WIDGET})
    field_plugins("non_dict_top", ["not-a-dict"])  # type: ignore[arg-type]
    field_plugins("non_dict_type", {"widget": "not-a-dict"})  # type: ignore[arg-type]
    field_plugins(
        "non_dict_field",
        {"widget": {"hue": "bad-spec", "extra": {"type": "text"}}},  # type: ignore[dict-item]
    )
    schema = KBSchema.from_dict({"name": "safe", "types": {"widget": {"description": "w"}}})
    fields = _published(schema)["widget"]["fields"]
    assert fields["hue"] == WIDGET["hue"]
    assert fields["edges"] == WIDGET["edges"]
    assert fields["extra"] == {"type": "text"}


def test_software_kb_backlog_fields_stay_on_backlog_item():
    """The shipped hook: prose-only ``backlog_item`` gains the preset options."""
    from pyrite_software_kb.entry_types import (
        BACKLOG_EFFORTS,
        BACKLOG_KINDS,
        BACKLOG_PRIORITIES,
        BACKLOG_STATUSES,
    )

    get_registry().discover()
    schema = KBSchema.from_dict(
        {
            "name": "prose",
            "kb_type": "software",
            "types": {
                "note": {"description": "a note"},
                "backlog_item": {
                    "description": "work",
                    "guidelines": "prose only, no fields",
                },
            },
        }
    )
    types = _published(schema, kb_type="software")
    fields = types["backlog_item"]["fields"]
    assert fields == _merged(schema, "backlog_item")
    assert fields["kind"]["options"] == list(BACKLOG_KINDS)
    assert fields["status"]["options"] == list(BACKLOG_STATUSES)
    assert fields["priority"]["options"] == list(BACKLOG_PRIORITIES)
    assert fields["effort"]["options"] == list(BACKLOG_EFFORTS)
    note = types["note"]["fields"]
    assert "kind" not in note
    assert "status" not in note
    assert "priority" not in note
    assert "effort" not in note


@pytest.mark.control(
    reason="an undeclared KB already excludes plugin types; pins that get_field_schemas() adds no types"
)
def test_undeclared_kb_does_not_leak_plugin_types():
    """A generic KB with no declared types lists only core types, never plugin types."""
    get_registry().discover()
    schema = KBSchema.from_dict({"name": "plain"})
    types = _published(schema, kb_type="generic")
    assert set(types) == set(CORE_TYPES)
    assert "backlog_item" not in types
    assert "adr" not in types


def test_orient_surfaces_publish_plugin_fields_for_declared_types(tmp_path: Path):
    """CLI orient, MCP kb_orient/kb_schema, and REST orient/schema agree (#232)."""
    kb_dir = tmp_path / "pyrite-kb"
    kb_dir.mkdir()
    (kb_dir / "kb.yaml").write_text((REPO / "kb" / "kb.yaml").read_text(encoding="utf-8"))
    world = build_world(
        tmp_path,
        kbs=[KBConfig(name="pyrite-copy", path=kb_dir, kb_type="software")],
    )
    try:
        svc_orient = KBService(world.config, world.db).orient("pyrite-copy")
        mcp = world.mcp(tier="read")
        mcp_orient = mcp._dispatch_tool("kb_orient", {"kb_name": "pyrite-copy"})
        mcp_schema = mcp._dispatch_tool("kb_schema", {"kb_name": "pyrite-copy"})
        rest_orient = world.client.get("/api/kbs/pyrite-copy/orient").json()
        rest_schema = world.client.get("/api/kbs/pyrite-copy/schema").json()
        cli_res = world.cli("orient", "-k", "pyrite-copy", "--format", "json")
        assert cli_res.exit_code == 0, cli_res.output
        cli_orient = json.loads(cli_res.output)

        schemas = [
            svc_orient["schema"],
            mcp_orient["schema"],
            mcp_schema,
            rest_orient["schema"],
            rest_schema,
            cli_orient["schema"],
        ]
        for s in schemas:
            types = s["types"]
            assert set(types) == {"component", "adr", "backlog_item", "standard"}
            for name, type_def in types.items():
                assert type_def.get("fields"), f"{name} missing fields"
            assert set(types["backlog_item"]["fields"]) == {"kind", "status", "priority", "effort"}
            assert types["backlog_item"]["fields"]["status"]["type"] == "select"
            assert types["backlog_item"]["fields"]["status"]["options"]
            assert set(types["adr"]["fields"]) == {"status"}
            assert set(types["component"]["fields"]) == {"kind"}
            assert set(types["standard"]["fields"]) == {"category"}
    finally:
        world.close()
