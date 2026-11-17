"""Tests for wiki stubs declaring min=1 on --limit (#1560)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from pyrite_encyclopedia.cli import wiki_app
from typer.testing import CliRunner

import pyrite.cli  # noqa: F401
from pyrite.config import KBConfig
from tests._world import build_world

# Windows compatibility polyfills (on Linux in CI these already exist natively)
if not hasattr(os, "getuid"):
    os.getuid = lambda: 0
if not hasattr(os, "O_NONBLOCK"):
    os.O_NONBLOCK = 0


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.mark.parametrize("limit", [0, -1, -5])
def test_wiki_stubs_rejects_non_positive_limit(runner: CliRunner, limit: int) -> None:
    result = runner.invoke(wiki_app, ["stubs", "--limit", str(limit)])
    assert result.exit_code == 2
    normalized = " ".join(re.sub(r"[│|┌┐└┘─]", "", result.output).split())
    assert "Invalid value for '--limit'" in normalized
    assert "is not in the range x>=1" in normalized


def test_wiki_stubs_help_declares_range(runner: CliRunner) -> None:
    result = runner.invoke(wiki_app, ["stubs", "--help"])
    assert result.exit_code == 0
    normalized = " ".join(re.sub(r"[│|┌┐└┘─]", "", result.output).split())
    assert "x>=1" in normalized


def test_wiki_stubs_accepts_positive_limit_and_pages(tmp_path: Path, runner: CliRunner) -> None:
    kb_path = tmp_path / "wiki-kb"
    world = build_world(
        tmp_path,
        kbs=[KBConfig(name="wiki-kb", path=kb_path, kb_type="encyclopedia")],
        rest=False,
    )
    try:
        world.add_entry(
            "wiki-kb",
            "---\nid: article-1\ntype: article\ntitle: Article One\nquality: stub\n---\nStub 1",
        )
        world.add_entry(
            "wiki-kb",
            "---\nid: article-2\ntype: article\ntitle: Article Two\nquality: stub\n---\nStub 2",
        )
        world.add_entry(
            "wiki-kb",
            "---\nid: article-3\ntype: article\ntitle: Article Three\nquality: featured\n---\nNon-stub",
        )

        with world.pointed():
            # --limit 1 returns 1 stub
            res1 = runner.invoke(wiki_app, ["stubs", "-k", "wiki-kb", "--limit", "1"])
            assert res1.exit_code == 0, res1.output
            assert "Article One" in res1.output
            assert "Article Two" not in res1.output
            assert "1 stub(s) found" in res1.output

            # --limit 2 returns both stubs
            res2 = runner.invoke(wiki_app, ["stubs", "-k", "wiki-kb", "--limit", "2"])
            assert res2.exit_code == 0, res2.output
            assert "Article One" in res2.output
            assert "Article Two" in res2.output
            assert "2 stub(s) found" in res2.output
    finally:
        world.close()
