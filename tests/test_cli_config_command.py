"""Tests for pyrite, pyrite-read, and pyrite-admin config commands (#1100)."""

from typer.testing import CliRunner

from pyrite.admin_cli import app as admin_app
from pyrite.cli import app as write_app
from pyrite.read_cli import app as read_app


def test_cli_config_resolves_path_at_call_time(tmp_path, monkeypatch):
    """Under cli_world or redirected config, config commands print the active path."""
    custom_dir = tmp_path / "custom_config"
    custom_dir.mkdir()
    custom_file = custom_dir / "config.yaml"
    custom_file.write_text("knowledge_bases: []\n", encoding="utf-8")

    monkeypatch.setattr("pyrite.config.CONFIG_DIR", custom_dir)
    monkeypatch.setattr("pyrite.config.CONFIG_FILE", custom_file)

    runner = CliRunner()

    write_result = runner.invoke(write_app, ["config"])
    assert write_result.exit_code == 0, write_result.output
    normalized_write = write_result.output.replace("\n", "")
    assert str(custom_file) in normalized_write

    read_result = runner.invoke(read_app, ["config"])
    assert read_result.exit_code == 0, read_result.output
    normalized_read = read_result.output.replace("\n", "")
    assert str(custom_file) in normalized_read

    admin_result = runner.invoke(admin_app, ["config", "show"])
    assert admin_result.exit_code == 0, admin_result.output
    normalized_admin = admin_result.output.replace("\n", "")
    assert str(custom_file) in normalized_admin
