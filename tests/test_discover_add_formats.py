"""Discovery writes are independent of presentation, using a real isolated registry."""

import csv
import io
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import load_config
from pyrite.exceptions import ConfigError
from pyrite.services.kb_registry_service import KBRegistryService
from pyrite.storage.database import PyriteDB

FORMATS = [None, "json", "yaml", "csv", "markdown", "rich"]
ADD_FORMATS = [
    *FORMATS[:-1],
    pytest.param("rich", marks=pytest.mark.control(reason="Rich already performed registration")),
]
runner = CliRunner()


@pytest.fixture
def discovery_env(tmp_path, monkeypatch):
    import pyrite.config as config_module

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("PYRITE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("PYRITE_DATA_DIR", str(config_dir))
    # Match the suite's CONFIG_* isolation hook to the explicit CLI environment.
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_dir / "config.yaml")
    monkeypatch.chdir(tmp_path)
    (config_dir / "config.yaml").write_text("knowledge_bases: []\n", encoding="utf-8")
    search_path = tmp_path / "search"
    search_path.mkdir()
    return search_path


def make_kb(search_path: Path, dirname="notes", name="discovered-notes"):
    path = search_path / dirname
    path.mkdir()
    (path / "kb.yaml").write_text(
        f"name: {name}\nkb_type: generic\ndescription: Discovered notes\n", encoding="utf-8"
    )
    return path


def discover(search_path, fmt, *, add=True):
    args = ["kb", "discover", str(search_path)]
    if add:
        args.append("--add")
    if fmt is not None:
        args.extend(["--format", fmt])
    return runner.invoke(app, args)


def registrations():
    db = PyriteDB(load_config().settings.index_path)
    try:
        return db.execute_sql("SELECT name, path, description FROM kb ORDER BY name")
    finally:
        db.close()


def assert_discovery_output(output, fmt, path):
    if fmt in (None, "json"):
        payload = json.loads(output)
        assert set(payload) == {"discovered"}
        assert payload["discovered"][0]["path"] == str(path)
    elif fmt == "yaml":
        payload = yaml.safe_load(output)
        assert set(payload) == {"discovered"}
        assert payload["discovered"][0]["path"] == str(path)
    elif fmt == "csv":
        rows = [row for row in csv.reader(io.StringIO(output)) if row]
        assert len(rows) == 2
        assert rows[0] == ["discovered"]
        assert "discovered-notes" in rows[1][0]
    else:
        assert "discovered-notes" in output
    if fmt != "rich":
        assert "Added " not in output
        assert "Discovered Knowledge Bases" not in output


@pytest.mark.parametrize("fmt", ADD_FORMATS)
def test_add_persists_and_can_be_repeated_in_every_format(discovery_env, fmt):
    path = make_kb(discovery_env)
    result = discover(discovery_env, fmt)
    assert result.exit_code == 0, result.output
    assert_discovery_output(result.output, fmt, path)
    assert registrations() == [
        {"name": "discovered-notes", "path": str(path), "description": "Discovered notes"}
    ]
    repeat = discover(discovery_env, fmt)
    assert repeat.exit_code == 0, repeat.output
    assert len(registrations()) == 1
    if fmt == "rich":
        assert "Added 1 KB(s)" in result.output
        assert "Added " not in repeat.output


@pytest.mark.control(reason="Discovery without --add remains a read-only operation")
@pytest.mark.parametrize("fmt", FORMATS)
def test_discovery_without_add_does_not_register(discovery_env, fmt):
    path = make_kb(discovery_env)
    result = discover(discovery_env, fmt, add=False)
    assert result.exit_code == 0, result.output
    assert_discovery_output(result.output, fmt, path)
    assert registrations() == []


@pytest.mark.control(reason="An existing registration must never be replaced")
@pytest.mark.parametrize("fmt", FORMATS)
def test_existing_registration_is_preserved(discovery_env, fmt):
    make_kb(discovery_env)
    old_path = discovery_env.parent / "original"
    old_path.mkdir()
    result = runner.invoke(app, ["kb", "add", str(old_path), "--name", "discovered-notes"])
    assert result.exit_code == 0, result.output
    before = registrations()
    result = discover(discovery_env, fmt)
    assert result.exit_code == 0, result.output
    assert registrations() == before


@pytest.mark.parametrize("fmt", ADD_FORMATS)
def test_duplicate_discovered_names_register_once(discovery_env, fmt):
    first = make_kb(discovery_env, "one")
    second = make_kb(discovery_env, "two")
    result = discover(discovery_env, fmt)
    assert result.exit_code == 0, result.output
    rows = registrations()
    assert len(rows) == 1
    assert rows[0]["path"] in {str(first), str(second)}
    if fmt == "rich":
        assert "Added 1 KB(s)" in result.output


@pytest.mark.control(reason="No discovered KBs keep the existing no-results behavior")
@pytest.mark.parametrize("fmt", FORMATS)
def test_no_discovered_kbs_does_not_register(discovery_env, fmt):
    result = discover(discovery_env, fmt)
    assert result.exit_code == 0, result.output
    assert "No KB configurations found." in result.output
    assert registrations() == []


@pytest.mark.parametrize("fmt", FORMATS)
def test_registration_failure_is_not_success(discovery_env, fmt, monkeypatch):
    make_kb(discovery_env)

    def refuse_add(self, **kwargs):
        raise ConfigError("Registry write refused")

    monkeypatch.setattr(KBRegistryService, "add_kb", refuse_add)
    result = discover(discovery_env, fmt)
    assert result.exit_code == 1, result.output
    assert "Registry write refused" in result.output
    assert "Added " not in result.output
    assert registrations() == []
    if fmt != "rich":
        assert json.loads(result.output)["error"] == "Registry write refused"


@pytest.mark.parametrize("has_kb", [False, True])
def test_unknown_format_is_rejected_before_any_registration(discovery_env, has_kb):
    if has_kb:
        make_kb(discovery_env)
    result = discover(discovery_env, "unsupported")
    assert result.exit_code == 2, result.output
    assert "unknown format" in result.output
    assert registrations() == []
