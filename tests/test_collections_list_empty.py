"""`pyrite collections list` on a KB with no collections (#1577).

An empty result is an ordinary result: every machine format prints the same
shape as a non-empty answer, with an empty list. Only ``--format rich`` prints
the "No collections found." sentence.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings

runner = CliRunner()


@pytest.fixture
def config(tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    return PyriteConfig(
        knowledge_bases=[KBConfig(name="notes", path=root, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db"),
    )


def _run(config: PyriteConfig, *args: str):
    with (
        patch("pyrite.cli.context.load_config", return_value=config),
        patch("pyrite.config.load_config", return_value=config),
    ):
        return runner.invoke(app, list(args))


def test_empty_kb_json_prints_empty_envelope(config):
    result = _run(config, "collections", "list", "-k", "notes", "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"collections": [], "total": 0}


def test_empty_kb_default_format_is_json(config):
    result = _run(config, "collections", "list", "-k", "notes")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"collections": [], "total": 0}


def test_empty_kb_rich_keeps_sentence(config):
    result = _run(config, "collections", "list", "-k", "notes", "--format", "rich")
    assert result.exit_code == 0, result.output
    assert "No collections found." in result.output
