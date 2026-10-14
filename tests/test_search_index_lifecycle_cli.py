"""`pyrite search` builds an empty index and warns on a stale one (#380).

Both paths used to be reached through ``IndexManager`` in the command; they now
go through ``KBService``. These tests drive the CLI against a real KB and a real
index, with nothing mocked, so a regression in either shows in the output.
"""

import json
import os
import time

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings
from tests.search_verdict_seed import cli_world


def _world(tmp_path):
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    (kb_path / "a.md").write_text("---\nid: a\ntitle: Needle\ntype: note\n---\nneedle body\n")
    return kb_path, PyriteConfig(
        knowledge_bases=[KBConfig(name="kb", path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )


@pytest.mark.control(
    reason="characterization: a refactor keeps this behaviour; the layer-boundary ratchet is the red"
)
def test_search_on_an_empty_index_builds_it_and_finds_the_entry(tmp_path):
    _, config = _world(tmp_path)
    with cli_world(config):
        result = CliRunner().invoke(app, ["search", "needle", "--format", "json"])
    assert result.exit_code == 0, result.exception or result.output
    assert "Index is empty. Building index..." in result.stderr
    assert "Index is empty" not in result.stdout
    assert [r["id"] for r in json.loads(result.stdout)["results"]] == ["a"]


@pytest.mark.control(
    reason="characterization: a refactor keeps this behaviour; the layer-boundary ratchet is the red"
)
def test_search_warns_on_stderr_when_a_file_is_newer_than_the_index(tmp_path):
    kb_path, config = _world(tmp_path)
    runner = CliRunner()
    with cli_world(config):
        first = runner.invoke(app, ["search", "needle", "--format", "json"])
        assert first.exit_code == 0, first.exception or first.output
        assert "stale" not in first.stderr
        later = time.time() + 60
        os.utime(kb_path / "a.md", (later, later))
        result = runner.invoke(app, ["search", "needle", "--format", "json"])
    assert result.exit_code == 0, result.exception or result.output
    assert "index may be stale for: kb" in result.stderr
    assert "stale" not in result.stdout
    assert json.loads(result.stdout)["count"] == 1
