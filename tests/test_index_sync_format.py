"""Tests for pyrite index sync and embed --format support (#1286)."""

import json
import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings
from tests.search_verdict_seed import cli_world


def _world(tmp_path):
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    (kb_path / "a.md").write_text("---\nid: a\ntitle: Doc A\ntype: note\n---\nBody text.\n")
    return kb_path, PyriteConfig(
        knowledge_bases=[KBConfig(name="kb", path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )


def test_index_sync_format_rich(tmp_path):
    _, config = _world(tmp_path)
    runner = CliRunner()
    with cli_world(config):
        result = runner.invoke(app, ["index", "sync", "-k", "kb", "--format", "rich"])
    assert result.exit_code == 0, result.output
    assert "Sync complete:" in result.stdout
    assert "Added:" in result.stdout


def test_index_sync_format_json(tmp_path):
    _, config = _world(tmp_path)
    runner = CliRunner()
    with cli_world(config):
        result = runner.invoke(app, ["index", "sync", "-k", "kb", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["added"] == 1
    assert data["updated"] == 0
    assert data["removed"] == 0


def test_index_sync_format_yaml(tmp_path):
    import yaml

    _, config = _world(tmp_path)
    runner = CliRunner()
    with cli_world(config):
        result = runner.invoke(app, ["index", "sync", "-k", "kb", "--format", "yaml"])
    assert result.exit_code == 0, result.output
    data = yaml.safe_load(result.stdout)
    assert data["added"] == 1


def test_index_sync_nonexistent_kb_format_json(tmp_path):
    _, config = _world(tmp_path)
    runner = CliRunner()
    with cli_world(config):
        result = runner.invoke(app, ["index", "sync", "-k", "nonexistent", "--format", "json"])
    assert result.exit_code != 0
    data = json.loads(result.stdout)
    assert data["error_code"] == "KB_NOT_FOUND"


def test_index_embed_format_json_empty_index(tmp_path):
    _, config = _world(tmp_path)
    runner = CliRunner()
    with cli_world(config):
        result = runner.invoke(app, ["index", "embed", "-k", "kb", "--format", "json"])
    assert result.exit_code != 0
    data = json.loads(result.stdout)
    assert "error" in data
