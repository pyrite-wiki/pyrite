"""An import drops what an export adds and names every dropped key (#1010).

The maintainer's decision (2026-10-20): the JSON an export writes carries keys
about the index and the location (``kb_name``, ``file_path``, ``indexed_at``,
``content_hash``, ``outlinks``, ``backlinks``) and a type's managed fields (a
task's ``assigned_at``, ``status_change_log``). An import (REST
``POST /api/entries/import``, CLI ``pyrite import``) drops them and reports
each one under ``ignored`` for its entry; nothing is dropped silently. Direct
create surfaces (``kb_create``, ``kb_bulk_create``, REST ``POST``, ``create
-f``) keep refusing them (``test_create_refuses_managed_fields.py``).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from typer.testing import CliRunner

from pyrite.cli import app as cli_app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

runner = CliRunner()

DROPPED = {
    "kb_name",
    "file_path",
    "indexed_at",
    "content_hash",
    "outlinks",
    "backlinks",
    "created_at",
    "updated_at",
}
TASK_MANAGED = {"assigned_at", "status_change_log"}
TASK_MANAGED_ALL = TASK_MANAGED | {"evidence", "agent_context"}


@pytest.fixture
def env(tmp_path):
    kbs = []
    for name in ("src", "dst"):
        path = tmp_path / name
        path.mkdir()
        (path / "kb.yaml").write_text(f"name: {name}\n")
        kbs.append(KBConfig(name=name, path=path, kb_type="generic"))
    db_path = tmp_path / "index.db"
    config = PyriteConfig(
        knowledge_bases=kbs, settings=Settings(index_path=db_path, auto_embed=False)
    )
    db = PyriteDB(db_path)
    IndexManager(db, config).index_all()
    svc = KBService(config, db)
    svc.create("src", {"entry_type": "note", "title": "A Note", "body": "b", "tags": ["t"]})
    svc.create("src", {"entry_type": "person", "title": "A Person", "body": "p"})
    svc.create("src", {"entry_type": "task", "title": "A Task", "body": "t", "status": "open"})
    db.close()
    return {"config": config, "tmp_path": tmp_path, "db_path": db_path, "dst": tmp_path / "dst"}


def _rest(env):
    from starlette.testclient import TestClient

    from pyrite.server.api import create_app, get_config, get_db, get_index_worker
    from pyrite.services.index_worker import IndexWorker

    app = create_app(config=env["config"])
    db = PyriteDB(env["db_path"])
    worker = IndexWorker(db, env["config"])
    app.dependency_overrides[get_config] = lambda: env["config"]
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_index_worker] = lambda: worker
    return TestClient(app), worker, db


def _export(env) -> str:
    client, worker, db = _rest(env)
    try:
        resp = client.get("/api/entries/export", params={"kb": "src", "format": "json"})
        assert resp.status_code == 200, resp.text
        return resp.text
    finally:
        worker.wait_for_idle(timeout=10)
        db.close()


def _frontmatter_keys(env) -> dict[str, set[str]]:
    out = {}
    for f in sorted(env["dst"].rglob("*.md")):
        head = f.read_text().split("---")[1]
        out[f.stem] = {ln.split(":")[0] for ln in head.splitlines() if ln and ln[0] not in " -"}
    return out


def _cli_import(env, path):
    """`pyrite import <path>` into ``dst`` against this env's config.

    The CLI app reads its config at the module and the context, so the one
    seam both CLI tests below need lives here: the production-wiring guard
    counts each `load_config` patch site, and the file's baseline holds two.
    """
    with (
        patch("pyrite.cli.load_config", return_value=env["config"]),
        patch("pyrite.cli.context.load_config", return_value=env["config"]),
    ):
        return runner.invoke(cli_app, ["import", str(path), "-k", "dst"])


@pytest.mark.control(reason="guards the fixture: the export carries the keys the decision names")
def test_the_export_really_carries_the_keys_the_decision_names(env):
    records = json.loads(_export(env))["entries"]
    carried = set().union(*(r.keys() for r in records))
    assert {"kb_name", "file_path", "created_at", "updated_at"} <= carried, carried


def test_an_export_imports_into_another_kb_through_rest(env):
    exported = _export(env)
    count = len(json.loads(exported)["entries"])
    client, worker, db = _rest(env)
    try:
        resp = client.post(
            "/api/entries/import",
            params={"kb": "dst"},
            files={"file": ("export.json", exported, "application/json")},
        )
    finally:
        worker.wait_for_idle(timeout=10)
        db.close()
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["imported"] == count and out["errors"] == 0, out
    keys = _frontmatter_keys(env)
    assert len(keys) == count
    for stem, present in keys.items():
        assert not (present & (DROPPED | TASK_MANAGED)), (stem, present)
    ignored = {e["title"]: set(e.get("ignored", [])) for e in out["entries"]}
    for title, names in ignored.items():
        assert {"kb_name", "file_path"} <= names, (title, names)
    assert TASK_MANAGED & ignored["A Task"], ignored["A Task"]
    # `ignored` is exactly the keys dropped, whatever their value (a null index
    # column is listed too). Written by hand from the property: the index and
    # location keys, and a task's managed fields.
    index_keys = {
        "kb_name", "file_path", "indexed_at", "content_hash", "outlinks", "backlinks",
        "created_by", "modified_by", "created_at", "updated_at",
    }  # fmt: skip
    for rec in json.loads(exported)["entries"]:
        expected = set(rec) & index_keys
        if rec["entry_type"] == "task":
            expected |= set(rec) & TASK_MANAGED_ALL
        assert ignored[rec["title"]] == expected, (rec["title"], ignored[rec["title"]], expected)


def test_an_export_imports_through_the_cli_and_names_what_it_dropped(env):
    path = env["tmp_path"] / "export.json"
    path.write_text(_export(env))
    res = _cli_import(env, path)
    assert res.exit_code == 0, res.output
    assert "Imported 3 entries" in res.output
    assert "Ignored" in res.output and "file_path" in res.output
    for present in _frontmatter_keys(env).values():
        assert not (present & (DROPPED | TASK_MANAGED))


def test_the_cli_does_not_print_its_own_rich_markup(env):
    """#1096: the label is markup, the key list is data; neither may print tags."""
    path = env["tmp_path"] / "export.json"
    path.write_text(_export(env))
    res = _cli_import(env, path)
    assert res.exit_code == 0, res.output
    assert "[yellow]" not in res.output and "[/yellow]" not in res.output, res.output
    assert "Ignored:" in res.output, res.output


def test_a_direct_bulk_create_still_refuses_the_same_record(env):
    """Only import drops: MCP kb_bulk_create is a direct create and refuses."""
    from pyrite.server.mcp_server import PyriteMCPServer

    record = {"entry_type": "task", "title": "Direct", "assigned_at": "2000-01-01"}
    server = PyriteMCPServer(env["config"], tier="admin")
    try:
        res = server._dispatch_tool("kb_bulk_create", {"kb_name": "dst", "entries": [record]})
    finally:
        server.close()
    assert res["results"][0]["created"] is False and "assigned_at" in res["results"][0]["error"]
    assert not list(env["dst"].rglob("*.md"))
