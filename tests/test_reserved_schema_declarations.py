"""Ignored typed declarations are reported and refused at schema writers (#1756)."""

import json

import pytest
import yaml

from pyrite.config import KBConfig
from pyrite.exceptions import ValidationError
from pyrite.schema.reserved import RESERVED_FIELD_NAMES
from pyrite.services.schema_service import SchemaService
from tests._world import build_world


def _schema(field):
    return {"types": {"note": {"fields": {field: {"type": "list"}}}}}


def _world(tmp_path, schema=None):
    root = tmp_path / "kb"
    root.mkdir()
    data = {"name": "demo", **(schema or {})}
    (root / "kb.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return build_world(tmp_path, kbs=[KBConfig(name="demo", path=root)], rest=False)


def _assert_diagnostic(message, field):
    assert f"types.note.fields.{field}" in message
    assert "reserved" in message and "ignored" in message
    assert ", ".join(sorted(RESERVED_FIELD_NAMES)) in message


@pytest.mark.parametrize("field", ["tags", "title", "importance"])
@pytest.mark.parametrize("command", ["kb", "qa"])
def test_validators_report_ignored_declarations(tmp_path, field, command):
    world = _world(tmp_path, _schema(field))
    try:
        path = world.kb_path("demo") / "kb.yaml"
        before = path.read_bytes()
        result = world.cli(command, "validate", "demo", "--format", "json")
        assert result.exit_code == 1, result.output
        report = json.loads(result.stdout)
        if command == "kb":
            assert report["all_valid"] is False
            assert report["kbs"][0]["valid"] is False
            messages = report["kbs"][0]["errors"]
        else:
            findings = [i for i in report["issues"] if i["rule"] == "ignored_schema_declaration"]
            assert len(findings) == 1, report
            assert findings[0]["field"] == field
            assert findings[0]["severity"] == "error"
            messages = [findings[0]["message"]]
        assert len(messages) == 1, messages
        _assert_diagnostic(messages[0], field)
        recorded = world.config.get_kb("demo").kb_schema.ignored_declarations
        assert len(recorded) == 1
        assert recorded[0]["type"] == "note"
        assert recorded[0]["field"] == field
        assert recorded[0]["reason"] == "reserved_field"
        assert recorded[0]["message"] == messages[0]
        assert path.read_bytes() == before
    finally:
        world.close()


WRITERS = ["service.set", "service.add", "cli.set", "mcp.set", "mcp.add"]


def _write(world, surface, schema, tmp_path):
    service = SchemaService(world.config)
    if surface.startswith("service"):
        try:
            if surface == "service.set":
                service.set_schema("demo", schema)
            else:
                service.add_type("demo", "note", schema["types"]["note"])
        except ValidationError as exc:
            return False, str(exc)
        return True, ""
    if surface == "cli.set":
        source = tmp_path / "schema.yaml"
        source.write_text(yaml.safe_dump(schema), encoding="utf-8")
        result = world.cli(
            "kb", "schema", "set", "demo", "--schema-file", str(source), "--format", "json"
        )
        report = json.loads(result.stdout)
        return result.exit_code == 0, report.get("error", "")
    args = {"kb_name": "demo"}
    if surface == "mcp.set":
        args.update(action="set_schema", schema=schema)
    else:
        args.update(action="add_type", type_name="note", type_def=schema["types"]["note"])
    result = world.mcp()._dispatch_tool("kb_manage", args)
    return not result.get("error"), result.get("error", "")


@pytest.mark.parametrize("field", ["tags", "title", "importance"])
@pytest.mark.parametrize("surface", WRITERS)
def test_schema_writers_refuse_reserved_declarations_without_writing(tmp_path, surface, field):
    world = _world(tmp_path)
    try:
        path = world.kb_path("demo") / "kb.yaml"
        before = path.read_bytes()
        ok, message = _write(world, surface, _schema(field), tmp_path)
        assert not ok, message
        _assert_diagnostic(message, field)
        assert path.read_bytes() == before
    finally:
        world.close()


@pytest.mark.control(reason="ordinary typed fields and reserved names in name lists are legitimate")
@pytest.mark.parametrize("surface", WRITERS)
def test_vendor_and_required_optional_name_lists_remain_valid(tmp_path, surface):
    world = _world(tmp_path)
    try:
        schema = _schema("vendor")
        schema["types"]["note"].update(required=["title", "tags"], optional=["importance"])
        ok, message = _write(world, surface, schema, tmp_path)
        assert ok, message
        stored = yaml.safe_load((world.kb_path("demo") / "kb.yaml").read_text(encoding="utf-8"))
        assert stored["types"] == schema["types"]
        for command in ("kb", "qa"):
            result = world.cli(command, "validate", "demo", "--format", "json")
            assert result.exit_code == 0, result.output
            assert "ignored_schema_declaration" not in result.stdout
    finally:
        world.close()
