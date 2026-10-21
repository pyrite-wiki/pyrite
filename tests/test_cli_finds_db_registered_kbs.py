"""CLI regressions for knowledge bases registered by `pyrite kb add`.

Issue #363: commands that load only config.yaml must also resolve KBs stored
in the index database. These tests use isolated config and data directories and
invoke the real Typer commands against SQLite.
"""

import json
import shutil

import pytest
from sqlalchemy.exc import SQLAlchemyError
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.storage.database import PyriteDB

runner = CliRunner()


@pytest.fixture
def registered_kb(tmp_path, monkeypatch):
    import pyrite.config as config_module

    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    kb_path = tmp_path / "kb"
    config_dir.mkdir()
    data_dir.mkdir()
    kb_path.mkdir()
    config_file = config_dir / "config.yaml"
    config_file.write_text("knowledge_bases: []\nsettings: {}\n", encoding="utf-8")
    (kb_path / "kb.yaml").write_text(
        "name: registry-only\ntypes:\n  note:\n    required: [title]\n",
        encoding="utf-8",
    )
    entry = kb_path / "entry.md"
    entry.write_text(
        "---\ntype: note\ntitle: Registered entry\n---\n\n"
        "A distinctive pineapple-search-token appears here.\n",
        encoding="utf-8",
    )

    # Config resolves its directory at import time; keep the test's explicit
    # environment and the module constants aligned.
    monkeypatch.setenv("PYRITE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("PYRITE_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)

    db = PyriteDB(data_dir / "index.db")
    db.register_kb(
        name="registry-only",
        kb_type="generic",
        path=str(kb_path),
        description="Registered from the CLI",
        source="user",
    )
    db.close()

    return {
        "config_dir": config_dir,
        "config_file": config_file,
        "data_dir": data_dir,
        "kb_path": kb_path,
        "entry": entry,
    }


def _invoke(args: list[str]):
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return result


def test_search_finds_db_registered_kb(registered_kb):
    result = _invoke(
        ["search", "pineapple-search-token", "-k", "registry-only", "--format", "json"]
    )

    payload = json.loads(result.stdout)
    assert payload["count"] == 1
    assert payload["results"][0]["kb_name"] == "registry-only"


def test_kb_validate_finds_named_db_registered_kb(registered_kb):
    payload = json.loads(_invoke(["kb", "validate", "registry-only", "--format", "json"]).stdout)

    assert [kb["name"] for kb in payload["kbs"]] == ["registry-only"]


def test_kb_validate_without_name_includes_db_registered_kbs(registered_kb):
    payload = json.loads(_invoke(["kb", "validate", "--format", "json"]).stdout)

    assert [kb["name"] for kb in payload["kbs"]] == ["registry-only"]


def test_kb_validate_refuses_when_index_database_is_corrupt(registered_kb):
    """A check command that cannot make its check exits 1 (json-contracts, "Check
    commands"; #922): a corrupt index is a refusal, not a clean verdict from
    the YAML alone. The file is left as it was."""
    _write_yaml_kb_config(registered_kb)
    index_db = registered_kb["data_dir"] / "index.db"
    corrupt_bytes = b"not a sqlite database"
    index_db.write_bytes(corrupt_bytes)

    result = runner.invoke(app, ["kb", "validate", "--format", "json"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "STORAGE_ERROR"
    assert "pyrite index build" in payload["suggestion"]
    assert "kbs" not in payload
    assert index_db.read_bytes() == corrupt_bytes

    rich_result = runner.invoke(app, ["kb", "validate", "--format", "rich"])
    assert rich_result.exit_code == 1, rich_result.output
    assert "STORAGE_ERROR" in rich_result.output


def test_kb_validate_does_not_report_file_read_errors_as_database_errors(
    registered_kb, monkeypatch, caplog
):
    from pyrite.storage.index import IndexManager

    def fail_check_health(self, kb_name=None):
        raise OSError("could not read a KB entry")

    monkeypatch.setattr(IndexManager, "check_health", fail_check_health)

    with caplog.at_level("WARNING", logger="pyrite.cli.context"):
        result = runner.invoke(app, ["kb", "validate", "--format", "json"])

    assert isinstance(result.exception, OSError)
    assert "could not read a KB entry" in str(result.exception)
    assert "Could not read index database" not in caplog.text


def test_kb_validate_uses_yaml_config_when_health_check_has_database_error(
    registered_kb, monkeypatch, caplog
):
    from pyrite.storage.index import IndexManager

    _write_yaml_kb_config(registered_kb)
    closed = []
    original_close = PyriteDB.close

    def fail_check_health(self, kb_name=None):
        raise SQLAlchemyError("could not query the index")

    def record_close(db):
        closed.append(True)
        original_close(db)

    monkeypatch.setattr(IndexManager, "check_health", fail_check_health)
    monkeypatch.setattr(PyriteDB, "close", record_close)

    with caplog.at_level("WARNING", logger="pyrite.cli.context"):
        result = runner.invoke(app, ["kb", "validate", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [kb["name"] for kb in payload["kbs"]] == ["yaml-only"]
    assert payload["drift_checked"] is False
    assert "using YAML config" in caplog.text
    assert "without content-drift checks" in caplog.text
    assert closed == [True]


def test_kb_schema_show_finds_db_registered_kb(registered_kb):
    payload = json.loads(
        _invoke(["kb", "schema", "show", "registry-only", "--format", "json"]).stdout
    )

    assert payload["kb_name"] == "registry-only"
    assert "note" in payload["types"]


def test_schema_validate_finds_db_registered_kb(registered_kb):
    result = _invoke(["schema", "validate", "--kb", "registry-only"])

    assert "1 files" in result.output
    assert "0 errors" in result.output


def test_kb_schema_add_type_finds_db_registered_kb(registered_kb):
    payload = json.loads(
        _invoke(
            [
                "kb",
                "schema",
                "add-type",
                "registry-only",
                "--type",
                "project",
                "--format",
                "json",
            ]
        ).stdout
    )

    assert payload["added"] is True
    assert payload["type_name"] == "project"


def test_kb_schema_remove_type_finds_db_registered_kb(registered_kb):
    payload = json.loads(
        _invoke(
            [
                "kb",
                "schema",
                "remove-type",
                "registry-only",
                "--type",
                "note",
                "--format",
                "json",
            ]
        ).stdout
    )

    assert payload["removed"] is True


def test_kb_schema_set_finds_db_registered_kb(registered_kb, tmp_path):
    schema_file = tmp_path / "schema.yaml"
    schema_file.write_text(
        "types:\n  project:\n    required: [title]\n",
        encoding="utf-8",
    )

    payload = json.loads(
        _invoke(
            [
                "kb",
                "schema",
                "set",
                "registry-only",
                "--schema-file",
                str(schema_file),
                "--format",
                "json",
            ]
        ).stdout
    )

    assert payload["set"] is True
    assert payload["type_count"] == 1


@pytest.mark.parametrize("operation", ["add-type", "remove-type", "set"])
def test_schema_writes_refuse_missing_db_registered_kb_directory(
    registered_kb, tmp_path, operation
):
    shutil.rmtree(registered_kb["kb_path"])
    args = ["kb", "schema", operation, "registry-only"]
    if operation == "add-type":
        args.extend(["--type", "project"])
    elif operation == "remove-type":
        args.extend(["--type", "note"])
    else:
        schema_file = tmp_path / "schema.yaml"
        schema_file.write_text("types: {}\n", encoding="utf-8")
        args.extend(["--schema-file", str(schema_file)])
    args.extend(["--format", "json"])

    result = runner.invoke(app, args)

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "KB_NOT_FOUND"
    assert payload["error"] == (
        "KB 'registry-only' directory is missing; re-register or remove the KB"
    )
    assert str(registered_kb["kb_path"]) not in payload["error"]
    assert not registered_kb["kb_path"].exists()


@pytest.mark.control(
    reason="YAML-configured KBs keep precedence over same-named database registrations."
)
def test_yaml_config_kb_keeps_precedence_over_db_registration(registered_kb, tmp_path):
    import json as json_module

    from pyrite.cli.context import get_config_and_db

    yaml_kb_path = tmp_path / "yaml-kb"
    yaml_kb_path.mkdir()
    registered_kb["config_file"].write_text(
        json_module.dumps(
            {
                "knowledge_bases": [
                    {
                        "name": "registry-only",
                        "path": str(yaml_kb_path),
                        "kb_type": "generic",
                    }
                ],
                "settings": {},
            }
        ),
        encoding="utf-8",
    )

    config, db = get_config_and_db()
    try:
        assert config.get_kb("registry-only").path == yaml_kb_path.resolve()
        assert [kb.name for kb in config.all_kbs()] == ["registry-only"]
    finally:
        db.close()


def _write_yaml_kb_config(registered_kb):
    registered_kb["config_file"].write_text(
        json.dumps(
            {
                "knowledge_bases": [
                    {
                        "name": "yaml-only",
                        "path": str(registered_kb["kb_path"]),
                        "kb_type": "generic",
                    }
                ],
                "settings": {},
            }
        ),
        encoding="utf-8",
    )


def test_named_yaml_kb_works_with_unreadable_index_db(registered_kb, capsys):
    _write_yaml_kb_config(registered_kb)
    index_db = registered_kb["data_dir"] / "index.db"
    index_db.write_bytes(b"not a sqlite database")

    from pyrite.cli.schema_commands import schema_validate

    schema_validate(files=None, kb_name="yaml-only", changed=False)
    output = capsys.readouterr().out

    assert "1 files" in output
    assert "0 errors" in output
    assert index_db.read_bytes() == b"not a sqlite database"


def test_failed_registry_lookup_warns_and_keeps_yaml_config(registered_kb, caplog):
    from pyrite.cli.context import get_config_with_registered_kbs
    from pyrite.config import load_config

    _write_yaml_kb_config(registered_kb)
    index_db = registered_kb["data_dir"] / "index.db"
    index_db.write_bytes(b"not a sqlite database")
    config = load_config()

    with caplog.at_level("WARNING", logger="pyrite.cli.context"):
        result = get_config_with_registered_kbs(config, name="database-only")

    assert result is config
    assert config.get_kb("yaml-only") is not None
    assert config.get_kb("database-only") is None
    assert "using YAML config" in caplog.text
