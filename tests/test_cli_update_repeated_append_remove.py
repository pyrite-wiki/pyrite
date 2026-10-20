"""Every ``--append`` / ``--remove`` pair on ``pyrite update`` is applied, in order (#1035).

The property: the user's pairs are all applied. Before the fix the CLI built
a dict keyed by field, so a later pair overwrote an earlier one and
``--append tags=a --append tags=b`` appended only ``b``.

The expected values are written by hand from the property, and read back with
``pyrite get``, never from the code's own bookkeeping.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

KB = "pairs"
ID = "pair-entry"

START = f"---\nid: {ID}\ntitle: Pair\ntype: note\ntags: [t0, t1, t2]\naliases: [a0]\n---\n\nText.\n"


@pytest.fixture
def config(tmp_path: Path) -> PyriteConfig:
    kb = tmp_path / "kb"
    (kb / "notes").mkdir(parents=True)
    (kb / "notes" / f"{ID}.md").write_text(START, encoding="utf-8")
    cfg = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(cfg.settings.index_path)
    try:
        IndexManager(db, cfg).index_all()
    finally:
        db.close()
    return cfg


def _run(config: PyriteConfig, args: list[str]):
    with (
        patch("pyrite.cli.load_config", return_value=config),
        patch("pyrite.cli.context.load_config", return_value=config),
    ):
        return CliRunner().invoke(app, args)


def _frontmatter(config: PyriteConfig, field: str):
    result = _run(config, ["get", ID, "-k", KB, "--format", "json"])
    assert result.exit_code == 0, result.output
    text = result.output.strip()
    data = json.loads(text[text.index("{") :])
    return data.get("data", data).get(field) if "data" in data else data.get(field)


def _update(config: PyriteConfig, *pairs: str):
    result = _run(config, ["update", ID, "-k", KB, "--format", "json", *pairs])
    assert result.exit_code == 0, result.output


def test_two_appends_on_one_field_are_both_applied(config):
    _update(config, "--append", "tags=a", "--append", "tags=b")
    assert _frontmatter(config, "tags") == ["t0", "t1", "t2", "a", "b"]


def test_three_appends_on_one_field_are_applied_in_the_order_given(config):
    _update(config, "--append", "tags=c", "--append", "tags=a", "--append", "tags=b")
    assert _frontmatter(config, "tags") == ["t0", "t1", "t2", "c", "a", "b"]


def test_appends_on_two_fields_are_all_applied(config):
    _update(
        config,
        "--append", "tags=a",
        "--append", "aliases=x",
        "--append", "tags=b",
        "--append", "aliases=y",
    )  # fmt: skip
    assert _frontmatter(config, "tags") == ["t0", "t1", "t2", "a", "b"]
    assert _frontmatter(config, "aliases") == ["a0", "x", "y"]


def test_two_removes_on_one_field_are_both_applied(config):
    _update(config, "--remove", "tags=t0", "--remove", "tags=t2")
    assert _frontmatter(config, "tags") == ["t1"]


def test_three_removes_on_one_field_are_all_applied(config):
    _update(config, "--remove", "tags=t0", "--remove", "tags=t1", "--remove", "tags=t2")
    assert _frontmatter(config, "tags") in ([], None)


def test_removes_on_two_fields_are_all_applied(config):
    _update(
        config,
        "--remove", "tags=t1",
        "--remove", "aliases=a0",
        "--remove", "tags=t2",
    )  # fmt: skip
    assert _frontmatter(config, "tags") == ["t0"]
    assert _frontmatter(config, "aliases") in ([], None)


def test_appends_and_removes_together_are_all_applied(config):
    _update(
        config,
        "--append", "tags=a",
        "--append", "tags=b",
        "--remove", "tags=t0",
        "--remove", "tags=t1",
    )  # fmt: skip
    assert _frontmatter(config, "tags") == ["t2", "a", "b"]


def test_the_json_list_form_still_appends_each_item(config):
    _update(config, "--append", 'tags=["a", "b"]', "--append", "tags=c")
    assert _frontmatter(config, "tags") == ["t0", "t1", "t2", "a", "b", "c"]
