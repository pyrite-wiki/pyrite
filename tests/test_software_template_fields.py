"""Shipped software templates enforce type-specific vocabularies (#572)."""

from unittest.mock import patch
import pytest
from typer.testing import CliRunner
from pyrite.cli import app
from pyrite.config import PyriteConfig, Settings
from pyrite.exceptions import SchemaViolationError
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite_software_kb.preset import SOFTWARE_KB_PRESET

CASES = [
    ("adr", "status"),
    ("design_doc", "status"),
    ("backlog_item", "status"),
    ("backlog_item", "kind"),
    ("backlog_item", "priority"),
    ("backlog_item", "effort"),
    ("component", "kind"),
    ("runbook", "runbook_kind"),
    ("standard", "category"),
]


@pytest.fixture(params=["builtin", "preset"])
def software(request, tmp_path):
    config = PyriteConfig(
        knowledge_bases=[], settings=Settings(index_path=tmp_path / "index.db", auto_embed=False)
    )
    presets = {} if request.param == "builtin" else {"software": SOFTWARE_KB_PRESET}
    with (
        patch("pyrite.config.load_config", return_value=config),
        patch("pyrite.config.save_config"),
        patch("pyrite.plugins.registry.PluginRegistry.get_all_kb_presets", return_value=presets),
    ):
        result = CliRunner().invoke(
            app, ["init", "-t", "software", "--path", str(tmp_path / "software")]
        )
    assert result.exit_code == 0, result.output
    kb = config.get_kb("software")
    assert kb.kb_type == "generic"
    db = PyriteDB(config.settings.index_path)
    try:
        yield KBService(config, db), kb
    finally:
        db.close()


@pytest.mark.parametrize(("entry_type", "field"), CASES)
def test_create_refuses_off_list_values_on_fresh_software(software, entry_type, field):
    svc, kb = software
    with pytest.raises(SchemaViolationError, match=field):
        svc.create("software", {"entry_type": entry_type, "title": "Bad value", field: "bogus"})
    assert list(kb.path.rglob("*.md")) == []


@pytest.mark.parametrize(("entry_type", "field"), CASES)
def test_orient_exposes_typed_options(software, entry_type, field):
    _, kb = software
    fields = kb.kb_schema.types[entry_type].to_dict().get("fields", {})
    assert fields[field]["type"] == "select"
    assert fields[field]["options"]


@pytest.mark.control(reason="a type with no status vocabulary remains writable")
def test_component_without_status_is_writable(software):
    svc, kb = software
    entry = svc.create(
        "software", {"entry_type": "component", "title": "Valid component", "kind": "module"}
    ).entry
    assert entry.file_path.exists()


@pytest.mark.parametrize(("entry_type", "field"), CASES)
def test_update_refuses_off_list_value_without_changing_file(software, entry_type, field):
    svc, kb = software
    options = kb.kb_schema.types[entry_type].fields[field].options
    values = {field: options[0]}
    if entry_type == "backlog_item":
        values.setdefault("kind", "bug")
    entry = svc.create(
        "software", {"entry_type": entry_type, "title": "Valid value", **values}
    ).entry
    before = entry.file_path.read_bytes()
    with pytest.raises(SchemaViolationError, match=field):
        svc.update(entry.id, "software", {field: "bogus"})
    assert entry.file_path.read_bytes() == before


@pytest.mark.parametrize(("entry_type", "field"), CASES)
def test_enforcement_disabled_preserves_warning(software, entry_type, field):
    svc, kb = software
    kb.kb_schema.validation["enforce_enums"] = False
    values = {field: "bogus"}
    if entry_type == "backlog_item":
        values.setdefault("kind", "bug")
    result = svc.create("software", {"entry_type": entry_type, "title": "Warning value", **values})
    assert result.entry.file_path.exists()
    assert any(w["field"] == field for w in result.warnings)
