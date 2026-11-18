"""Tests for ignored reserved field declarations in KBSchema, kb validate, set_schema, and add_type (#1756)."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import ValidationError
from pyrite.schema.kb_schema import KBSchema
from pyrite.schema.reserved import RESERVED_FIELD_NAMES, reserved_field_error_message
from pyrite.server.mcp_server import PyriteMCPServer
from pyrite.services.qa_service import QAService
from pyrite.services.schema_service import SchemaService
from pyrite.storage.database import PyriteDB
from pyrite.utils.yaml import dump_yaml_file

runner = CliRunner()


@pytest.mark.parametrize(
    ("field_name", "is_reserved"),
    [
        ("tags", True),
        ("title", True),
        ("importance", True),
        ("vendor", False),
    ],
)
def test_kb_schema_records_ignored_declarations_parametrized(field_name, is_reserved):
    """KBSchema.from_dict records ignored declarations for reserved field names,
    while non-reserved fields (control) are parsed normally and not reported."""
    raw = {
        "name": "test-kb",
        "types": {
            "note": {
                "fields": {
                    field_name: {"type": "list" if field_name == "tags" else "text"},
                }
            }
        },
    }
    schema = KBSchema.from_dict(raw)
    if is_reserved:
        assert len(schema.ignored_declarations) == 1
        decl = schema.ignored_declarations[0]
        assert decl["type"] == "note"
        assert decl["field"] == field_name
        assert decl["reserved"] == sorted(RESERVED_FIELD_NAMES)
        expected_msg = reserved_field_error_message("note", field_name)
        assert decl["reason"] == expected_msg
        assert sorted(RESERVED_FIELD_NAMES) == decl["reserved"]
        # The reserved field must be removed from the parsed type fields
        assert field_name not in schema.types["note"].fields
    else:
        assert schema.ignored_declarations == []
        assert field_name in schema.types["note"].fields


@pytest.mark.parametrize(
    ("field_name", "is_reserved"),
    [
        ("tags", True),
        ("title", True),
        ("importance", True),
        ("vendor", False),
    ],
)
def test_kb_validate_reports_ignored_declaration_parametrized(tmp_path, field_name, is_reserved):
    """`kb validate` lists ignored declarations, sets all_valid=False, and exits 1
    when reserved fields are declared, but exits 0 when a non-reserved control is declared."""
    kb_path = tmp_path / f"kb-{field_name}"
    kb_path.mkdir(parents=True)
    kb_yaml = {
        "name": f"kb-{field_name}",
        "types": {
            "note": {
                "fields": {
                    field_name: {"type": "list" if field_name == "tags" else "text"},
                }
            }
        },
    }
    dump_yaml_file(kb_yaml, kb_path / "kb.yaml")

    db_path = tmp_path / f"db-{field_name}.sqlite"
    db = PyriteDB(db_path)
    db.register_kb(f"kb-{field_name}", "generic", str(kb_path), "")
    db.close()

    kb_config = KBConfig(
        name=f"kb-{field_name}",
        path=kb_path,
        kb_type="generic",
    )
    config = PyriteConfig(
        knowledge_bases=[kb_config],
        settings=Settings(index_path=db_path),
    )

    with (
        patch("pyrite.config.load_config", return_value=config),
        patch("pyrite.cli.load_config", return_value=config),
        patch("pyrite.cli.kb_commands.load_config", return_value=config),
    ):
        result = runner.invoke(app, ["kb", "validate", f"kb-{field_name}", "--format", "json"])

    data = json.loads(result.output)
    if is_reserved:
        assert result.exit_code == 1, result.output
        assert data["all_valid"] is False
        assert len(data["kbs"]) == 1
        kb_res = data["kbs"][0]
        assert kb_res["valid"] is False
        assert len(kb_res["ignored_declarations"]) == 1
        decl = kb_res["ignored_declarations"][0]
        assert decl["type"] == "note"
        assert decl["field"] == field_name
        assert decl["reserved"] == sorted(RESERVED_FIELD_NAMES)
        expected_msg = reserved_field_error_message("note", field_name)
        assert decl["reason"] == expected_msg
        assert any(expected_msg in err for err in kb_res["errors"])
    else:
        assert result.exit_code == 0, result.output
        assert data["all_valid"] is True
        assert len(data["kbs"]) == 1
        kb_res = data["kbs"][0]
        assert kb_res["valid"] is True
        assert kb_res["ignored_declarations"] == []
        assert kb_res["errors"] == []


@pytest.mark.parametrize(
    ("field_name", "is_reserved"),
    [
        ("tags", True),
        ("title", True),
        ("importance", True),
        ("vendor", False),
    ],
)
def test_schema_service_set_schema_refuses_reserved_fields(tmp_path, field_name, is_reserved):
    """SchemaService.set_schema refuses field declarations colliding with reserved names."""
    kb_path = tmp_path / f"svc-set-{field_name}"
    kb_path.mkdir(parents=True)
    dump_yaml_file({"name": "test-kb", "types": {}}, kb_path / "kb.yaml")

    kb_config = KBConfig(name="test-kb", path=kb_path)
    config = PyriteConfig(knowledge_bases=[kb_config])

    svc = SchemaService(config)
    new_schema = {
        "types": {
            "note": {
                "fields": {
                    field_name: {"type": "text"},
                }
            }
        }
    }
    if is_reserved:
        with pytest.raises(ValidationError) as excinfo:
            svc.set_schema("test-kb", new_schema)
        assert str(excinfo.value) == reserved_field_error_message("note", field_name)
    else:
        result = svc.set_schema("test-kb", new_schema)
        assert result.get("set") is True
        assert "error" not in result


@pytest.mark.parametrize(
    ("field_name", "is_reserved"),
    [
        ("tags", True),
        ("title", True),
        ("importance", True),
        ("vendor", False),
    ],
)
def test_schema_service_add_type_refuses_reserved_fields_in_fields(
    tmp_path, field_name, is_reserved
):
    """SchemaService.add_type refuses fields with reserved names under `fields:`,
    but permits legitimate reserved keys in `required` or `optional`."""
    kb_path = tmp_path / f"svc-add-{field_name}"
    kb_path.mkdir(parents=True)
    dump_yaml_file({"name": "test-kb", "types": {}}, kb_path / "kb.yaml")

    kb_config = KBConfig(name="test-kb", path=kb_path)
    config = PyriteConfig(knowledge_bases=[kb_config])

    svc = SchemaService(config)
    type_def = {
        "description": "custom type",
        "fields": {
            field_name: {"type": "text"},
        },
    }
    if is_reserved:
        with pytest.raises(ValidationError) as excinfo:
            svc.add_type("test-kb", "custom", type_def)
        assert str(excinfo.value) == reserved_field_error_message("custom", field_name)
    else:
        result = svc.add_type("test-kb", "custom", type_def)
        assert result.get("added") is True
        assert "error" not in result


def test_schema_service_add_type_permits_reserved_name_in_required_and_optional(tmp_path):
    """SchemaService.add_type allows reserved names like 'title' in `required` / `optional` lists."""
    kb_path = tmp_path / "svc-req"
    kb_path.mkdir(parents=True)
    dump_yaml_file({"name": "test-kb", "types": {}}, kb_path / "kb.yaml")

    kb_config = KBConfig(name="test-kb", path=kb_path)
    config = PyriteConfig(knowledge_bases=[kb_config])

    svc = SchemaService(config)
    result = svc.add_type(
        "test-kb",
        "doc",
        {
            "description": "document",
            "required": ["title"],
            "optional": ["tags"],
        },
    )
    assert result.get("added") is True
    assert "error" not in result


def test_qa_validate_reports_ignored_declaration(tmp_path):
    """QAService.validate_kb includes ignored_declaration issue for reserved fields."""
    kb_path = tmp_path / "qa-kb"
    kb_path.mkdir(parents=True)
    dump_yaml_file(
        {
            "name": "qa-kb",
            "types": {
                "note": {
                    "fields": {
                        "tags": {"type": "list"},
                    }
                }
            },
        },
        kb_path / "kb.yaml",
    )

    db_path = tmp_path / "qa.sqlite"
    db = PyriteDB(db_path)
    db.register_kb("qa-kb", "generic", str(kb_path), "")
    db.close()

    kb_config = KBConfig(name="qa-kb", path=kb_path)
    config = PyriteConfig(
        knowledge_bases=[kb_config],
        settings=Settings(index_path=db_path),
    )

    db = PyriteDB(db_path)
    qa_svc = QAService(config, db)
    res = qa_svc.validate_kb("qa-kb", readable_kbs={"qa-kb"})
    db.close()

    ignored_issues = [i for i in res["issues"] if i["rule"] == "ignored_declaration"]
    assert len(ignored_issues) == 1
    assert ignored_issues[0]["field"] == "tags"
    assert "reserved name" in ignored_issues[0]["message"]


def test_mcp_kb_manage_add_type_and_set_schema_refuse_reserved_fields(tmp_path):
    """MCP kb_manage refuses add_type and set_schema with reserved field names."""
    kb_path = tmp_path / "mcp-kb"
    kb_path.mkdir(parents=True)
    dump_yaml_file({"name": "mcp-kb", "types": {}}, kb_path / "kb.yaml")

    db_path = tmp_path / "mcp.sqlite"
    db = PyriteDB(db_path)
    db.register_kb("mcp-kb", "generic", str(kb_path), "")
    db.close()

    kb_config = KBConfig(name="mcp-kb", path=kb_path)
    config = PyriteConfig(
        knowledge_bases=[kb_config],
        settings=Settings(index_path=db_path),
    )

    server = PyriteMCPServer(config=config, tier="admin")

    # add_type refusal
    res_add = server._dispatch_tool(
        "kb_manage",
        {
            "action": "add_type",
            "kb_name": "mcp-kb",
            "type_name": "memo",
            "type_def": {
                "fields": {
                    "tags": {"type": "list"},
                }
            },
        },
    )
    assert "error" in res_add
    assert res_add["error"] == reserved_field_error_message("memo", "tags")

    # set_schema refusal
    res_set = server._dispatch_tool(
        "kb_manage",
        {
            "action": "set_schema",
            "kb_name": "mcp-kb",
            "schema": {
                "types": {
                    "note": {
                        "fields": {
                            "importance": {"type": "number"},
                        }
                    }
                }
            },
        },
    )
    assert "error" in res_set
    assert res_set["error"] == reserved_field_error_message("note", "importance")


def test_cli_schema_set_refuses_reserved_field(tmp_path):
    """CLI `pyrite kb schema set` exits non-zero when given schema file with reserved field name."""
    kb_path = tmp_path / "cli-kb"
    kb_path.mkdir(parents=True)
    dump_yaml_file({"name": "cli-kb", "types": {}}, kb_path / "kb.yaml")

    db_path = tmp_path / "cli.sqlite"
    db = PyriteDB(db_path)
    db.register_kb("cli-kb", "generic", str(kb_path), "")
    db.close()

    kb_config = KBConfig(name="cli-kb", path=kb_path)
    config = PyriteConfig(
        knowledge_bases=[kb_config],
        settings=Settings(index_path=db_path),
    )

    schema_file = tmp_path / "bad_schema.yaml"
    dump_yaml_file(
        {
            "types": {
                "note": {
                    "fields": {
                        "title": {"type": "text"},
                    }
                }
            }
        },
        schema_file,
    )

    with (
        patch("pyrite.config.load_config", return_value=config),
        patch("pyrite.cli.load_config", return_value=config),
        patch("pyrite.cli.kb_commands.load_config", return_value=config),
    ):
        result = runner.invoke(
            app, ["kb", "schema", "set", "cli-kb", "-s", str(schema_file), "--format", "json"]
        )

    assert result.exit_code != 0
    assert "title" in result.output
    assert "reserved name" in result.output
