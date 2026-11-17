"""Tests verifying that fields declared text/string keep their typed values on every surface (#1672).

A value sent to a field declared text (or string) is stored exactly as typed, on
every surface: CLI -f, MCP kb_create/kb_update, and REST POST/PATCH. Numeric-looking
values like 1.10, 02134, 1e5, true, and 2026-11-17 remain strings and are not parsed
into floats, ints, or booleans. Declared number fields continue to store numeric values.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from pyrite.cli import app as cli_app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.server.api import create_app, get_config, get_db
from pyrite.server.mcp_server import PyriteMCPServer
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB

runner = CliRunner()

KB_YAML = """\
name: test-kb
types:
  note:
    fields:
      version:
        type: text
      count:
        type: number
"""

TEST_CASES = [
    "1.10",
    "02134",
    "1e5",
    "true",
    "2026-11-17",
]


@pytest.fixture
def env(tmp_path):
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text(KB_YAML, encoding="utf-8")
    db_path = tmp_path / "index.db"
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="test-kb", path=kb_path)],
        settings=Settings(index_path=db_path),
    )
    db = PyriteDB(db_path)
    svc = KBService(config, db)
    return {
        "kb_path": kb_path,
        "config": config,
        "db": db,
        "svc": svc,
    }


def _read_entry_file(kb_path: Path, entry_id: str) -> str:
    path = kb_path / "notes" / f"{entry_id}.md"
    if not path.exists():
        path = kb_path / f"{entry_id}.md"
    assert path.exists(), f"Entry file {path} does not exist"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("surface", ["cli", "mcp", "rest"])
@pytest.mark.parametrize(("i", "val"), list(enumerate(TEST_CASES)))
def test_declared_text_field_create_preserves_value(env, surface, i, val):
    kb_path = env["kb_path"]
    cfg = env["config"]
    entry_id = f"c-{surface}-{i}"

    if surface == "cli":
        with (
            patch("pyrite.cli.load_config", return_value=cfg),
            patch("pyrite.cli.context.load_config", return_value=cfg),
        ):
            res = runner.invoke(
                cli_app,
                [
                    "create",
                    "-k",
                    "test-kb",
                    "-t",
                    "note",
                    "--title",
                    entry_id,
                    "-f",
                    f"version={val}",
                    "-f",
                    "count=7",
                ],
            )
            assert res.exit_code == 0, res.output
    elif surface == "mcp":
        server = PyriteMCPServer(cfg, tier="admin")
        try:
            res = server._dispatch_tool(
                "kb_create",
                {
                    "kb_name": "test-kb",
                    "entry_type": "note",
                    "title": entry_id,
                    "version": val,
                    "count": 7,
                },
            )
            assert "error" not in res, res
        finally:
            server.close()
    elif surface == "rest":
        app = create_app(config=cfg)
        app.dependency_overrides[get_config] = lambda: cfg
        app.dependency_overrides[get_db] = lambda: env["db"]
        with TestClient(app, base_url="http://localhost") as client:
            resp = client.post(
                "/api/entries",
                json={
                    "kb": "test-kb",
                    "entry_type": "note",
                    "title": entry_id,
                    "metadata": {
                        "version": val,
                        "count": 7,
                    },
                },
            )
            assert resp.status_code == 200, resp.text

    content = _read_entry_file(kb_path, entry_id)
    assert f'version: "{val}"' in content, f'Expected version: "{val}" in:\n{content}'
    assert "count: 7" in content, f"Expected count: 7 in:\n{content}"


@pytest.mark.parametrize("surface", ["cli", "mcp", "rest"])
@pytest.mark.parametrize(("i", "val"), list(enumerate(TEST_CASES)))
def test_declared_text_field_update_preserves_value(env, surface, i, val):
    kb_path = env["kb_path"]
    cfg = env["config"]
    entry_id = f"u-{surface}-{i}"

    # Initial create with baseline values
    env["svc"].create(
        "test-kb",
        {
            "id": entry_id,
            "entry_type": "note",
            "title": entry_id,
            "version": "initial",
            "count": 1,
        },
    )

    if surface == "cli":
        with (
            patch("pyrite.cli.load_config", return_value=cfg),
            patch("pyrite.cli.context.load_config", return_value=cfg),
        ):
            res = runner.invoke(
                cli_app,
                [
                    "update",
                    "-k",
                    "test-kb",
                    entry_id,
                    "-f",
                    f"version={val}",
                    "-f",
                    "count=7",
                ],
            )
            assert res.exit_code == 0, res.output
    elif surface == "mcp":
        server = PyriteMCPServer(cfg, tier="admin")
        try:
            res = server._dispatch_tool(
                "kb_update",
                {
                    "kb_name": "test-kb",
                    "entry_id": entry_id,
                    "version": val,
                    "count": 7,
                },
            )
            assert "error" not in res, res
        finally:
            server.close()
    elif surface == "rest":
        app = create_app(config=cfg)
        app.dependency_overrides[get_config] = lambda: cfg
        app.dependency_overrides[get_db] = lambda: env["db"]
        with TestClient(app, base_url="http://localhost") as client:
            resp1 = client.patch(
                f"/api/entries/{entry_id}",
                json={
                    "kb": "test-kb",
                    "field": "version",
                    "value": val,
                },
            )
            assert resp1.status_code == 200, resp1.text
            resp2 = client.patch(
                f"/api/entries/{entry_id}",
                json={
                    "kb": "test-kb",
                    "field": "count",
                    "value": 7,
                },
            )
            assert resp2.status_code == 200, resp2.text

    content = _read_entry_file(kb_path, entry_id)
    assert f'version: "{val}"' in content, f'Expected version: "{val}" in:\n{content}'
    assert "count: 7" in content, f"Expected count: 7 in:\n{content}"


def test_undeclared_field_keeps_original_behavior(tmp_path):
    kb_path = tmp_path / "undeclared-kb"
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text("name: undeclared-kb\n", encoding="utf-8")
    db_path = tmp_path / "index.db"
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="undeclared-kb", path=kb_path)],
        settings=Settings(index_path=db_path),
    )

    with (
        patch("pyrite.cli.load_config", return_value=config),
        patch("pyrite.cli.context.load_config", return_value=config),
    ):
        # Without quotes, 1.10 parses as float 1.1 on undeclared field
        res1 = runner.invoke(
            cli_app,
            [
                "create",
                "-k",
                "undeclared-kb",
                "-t",
                "note",
                "--title",
                "u-unquoted",
                "-f",
                "version=1.10",
            ],
        )
        assert res1.exit_code == 0, res1.output
        text1 = (kb_path / "notes" / "u-unquoted.md").read_text(encoding="utf-8")
        assert "version: 1.1\n" in text1

        # With quotes, version="1.10" preserves the string
        res2 = runner.invoke(
            cli_app,
            [
                "create",
                "-k",
                "undeclared-kb",
                "-t",
                "note",
                "--title",
                "u-quoted",
                "-f",
                'version="1.10"',
            ],
        )
        assert res2.exit_code == 0, res2.output
        text2 = (kb_path / "notes" / "u-quoted.md").read_text(encoding="utf-8")
        assert 'version: "1.10"\n' in text2
