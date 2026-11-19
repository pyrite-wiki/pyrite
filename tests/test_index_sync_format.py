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


def test_index_embed_format_unknown(tmp_path):
    _, config = _world(tmp_path)
    runner = CliRunner()
    with cli_world(config):
        result = runner.invoke(app, ["index", "embed", "-k", "kb", "--format", "csv"])
    assert result.exit_code == 2
    assert "unknown format 'csv'; choose one of: rich, json, yaml" in result.output


def test_index_embed_format_json_success(tmp_path, monkeypatch):
    import zlib
    import pyrite.services.embedding_service as es

    class _Vec(list):
        def tolist(self):
            return list(self)

    class _StubModel:
        def encode(self, text, **kwargs):
            if isinstance(text, list):
                return [self.encode(t) for t in text]
            vec = [0.05] * 384
            vec[zlib.crc32(text.encode()) % 384] += 0.001
            return _Vec(vec)

    kb_path, config = _world(tmp_path)
    from pyrite.storage.database import PyriteDB

    db = PyriteDB(config.settings.index_path)
    if not db.vec_available:
        db.close()
        pytest.skip("sqlite-vec not available")
    db.close()

    monkeypatch.setattr(es, "is_available", lambda: False)
    runner = CliRunner()
    with cli_world(config):
        # Build index with is_available=False, ensuring entries are indexed but not embedded
        build_res = runner.invoke(app, ["index", "build", "-k", "kb"])
        assert build_res.exit_code == 0, build_res.output

    monkeypatch.setattr(es, "is_available", lambda: True)
    monkeypatch.setattr(es, "_load_model", lambda name: _StubModel())
    monkeypatch.setattr(es, "_MODEL_CACHE", {})

    with cli_world(config):
        result = runner.invoke(app, ["index", "embed", "-k", "kb", "--format", "json"])
    assert result.exit_code == 0, result.output
    # Must be valid pure JSON with no progress bar text before it
    data = json.loads(result.stdout)
    assert data["embedded"] == 1
    assert data["skipped"] == 0
    assert data["errors"] == 0
