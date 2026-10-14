"""Regression tests for bulk-create duplicate IDs (#359)."""

import contextlib
import json
from pathlib import Path
from unittest.mock import patch

import jsonschema
import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")
pytest.importorskip("typer", reason="typer not installed")
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.server.mcp_server import PyriteMCPServer
from pyrite.server.tool_schemas import WRITE_TOOLS
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository


runner = CliRunner()


@pytest.fixture
def bulk_server(tmp_path):
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="bulk", path=kb_path, kb_type=KBType.GENERIC)],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    server = PyriteMCPServer(config, tier="write")
    server.index_mgr.index_all()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def cli_env(tmp_path):
    db_path = tmp_path / "index.db"
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="test-kb", path=kb_path, kb_type=KBType.GENERIC)],
        settings=Settings(index_path=db_path, auto_embed=False),
    )
    db = PyriteDB(db_path)
    IndexManager(db, config).index_all()
    db.close()
    return {"config": config, "kb_path": kb_path}


def call_bulk(server, entries):
    args = {"kb_name": "bulk", "entries": entries}
    jsonschema.validate(args, WRITE_TOOLS["kb_bulk_create"]["inputSchema"])
    return server._dispatch_tool("kb_bulk_create", args)


@contextlib.contextmanager
def patched_cli_config(config):
    with patch("pyrite.cli.load_config", return_value=config):
        with patch("pyrite.cli.context.load_config", return_value=config):
            yield


def test_existing_id_is_rejected_without_overwriting_siblings(bulk_server):
    first = call_bulk(bulk_server, [{"title": "Existing Entry", "body": "ORIGINAL"}])
    assert first["created"] == 1

    entry_id = first["results"][0]["entry_id"]
    entry_path = KBRepository(bulk_server.config.get_kb("bulk")).find_file(entry_id)
    assert entry_path is not None
    original_bytes = entry_path.read_bytes()

    result = call_bulk(
        bulk_server,
        [
            {"title": "Existing Entry", "body": "REPLACED"},
            {"title": "New Sibling", "body": "SIBLING"},
        ],
    )

    assert (result["total"], result["created"], result["failed"]) == (2, 1, 1)
    assert result["results"][0]["created"] is False
    assert "already exists" in result["results"][0]["error"]
    assert result["results"][1]["created"] is True
    assert entry_path.read_bytes() == original_bytes


def test_duplicate_ids_in_one_batch_create_first_only(bulk_server):
    result = call_bulk(
        bulk_server,
        [
            {"title": "Repeated Entry", "body": "FIRST"},
            {"title": "Repeated Entry", "body": "SECOND"},
        ],
    )

    assert (result["total"], result["created"], result["failed"]) == (2, 1, 1)
    assert result["results"][0]["created"] is True
    assert result["results"][1]["created"] is False
    assert "already exists" in result["results"][1]["error"]

    entry_id = result["results"][0]["entry_id"]
    entry_path = KBRepository(bulk_server.config.get_kb("bulk")).find_file(entry_id)
    assert entry_path is not None
    text = entry_path.read_text(encoding="utf-8")
    assert "FIRST" in text
    assert "SECOND" not in text


def test_cli_import_rejects_duplicate_id_without_overwriting(cli_env, tmp_path):
    with patched_cli_config(cli_env["config"]):
        created = runner.invoke(
            app,
            [
                "create",
                "--kb",
                "test-kb",
                "--type",
                "note",
                "--title",
                "Hello world",
                "--body",
                "ORIGINAL",
            ],
        )
        assert created.exit_code == 0, created.output

        entry_path = KBRepository(cli_env["config"].get_kb("test-kb")).find_file("hello-world")
        assert entry_path is not None
        original_bytes = entry_path.read_bytes()

        import_file = tmp_path / "import.json"
        import_file.write_text(
            json.dumps(
                {
                    "entries": [
                        {"title": "Hello world", "entry_type": "note", "body": "REPLACED"},
                        {"title": "CLI sibling", "entry_type": "note", "body": "SIBLING"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        result = runner.invoke(app, ["import", str(import_file), "--kb", "test-kb"])

    # Since #378 `pyrite import` exits non-zero when any record is refused; since
    # #526 that is 3 when other records were written (1 when none were). The
    # refusal itself is this test's point and is unchanged.
    assert result.exit_code == 3, result.output
    assert "Imported 1 entries" in result.output
    assert "(1 failed)" in result.output
    assert "already exists" in result.output
    assert entry_path.read_bytes() == original_bytes

    sibling_path = KBRepository(cli_env["config"].get_kb("test-kb")).find_file("cli-sibling")
    assert sibling_path is not None
    assert "SIBLING" in sibling_path.read_text(encoding="utf-8")
