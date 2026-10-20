"""A create refuses a field the caller may not set, at any spelling (#1010).

The property: a field Pyrite or the type manages (``_MANAGED_FIELDS`` plus
``Entry.managed_fields``) is refused on create at the top level and in the
``metadata`` envelope alike, on every surface that takes a caller's spec
(MCP ``kb_create`` and ``kb_bulk_create``, CLI ``create -f`` and ``import``,
REST bulk import), with the field named, and no file is written. Every key
a create accepts is written, so ``file_path`` / ``provenance`` /
``extra_frontmatter`` are refused rather than silently dropped. In-process
owners (``sw new-adr``) set their own fields through ``create_entry``.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from typer.testing import CliRunner

from pyrite.cli import app as cli_app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import ValidationError
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

runner = CliRunner()
KB = "mf"

#: (entry type, field, a value of the field's own shape)
MANAGED = [
    ("task", "status_change_log", [{"from": "open", "to": "done", "at": "2020-01-01"}]),
    ("task", "evidence", [{"kind": "note", "ref": "x"}]),
    ("task", "agent_context", "forged"),
    ("task", "assigned_at", "2000-01-01"),
    ("adr", "adr_number", 7),
    ("note", "file_path", "/tmp/elsewhere.md"),
    ("note", "provenance", {"source": "forged"}),
    ("note", "extra_frontmatter", {"x": 1}),
]
IDS = [f"{t}.{f}" for t, f, _ in MANAGED]


@pytest.fixture
def env(tmp_path):
    kb_path = tmp_path / KB
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text(f"name: {KB}\n")
    db_path = tmp_path / "index.db"
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=db_path, auto_embed=False),
    )
    db = PyriteDB(db_path)
    IndexManager(db, config).index_all()
    db.close()
    return {"config": config, "kb_path": kb_path, "tmp_path": tmp_path, "db_path": db_path}


@pytest.fixture
def mcp(env):
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(env["config"], tier="admin")
    yield server
    server.close()


def _md_files(env):
    return sorted(p.name for p in env["kb_path"].rglob("*.md"))


def _cli(env, args):
    with (
        patch("pyrite.cli.load_config", return_value=env["config"]),
        patch("pyrite.cli.context.load_config", return_value=env["config"]),
    ):
        return runner.invoke(cli_app, args)


ENVELOPE_CONTROL = pytest.mark.control(
    reason="the metadata envelope was already refused (#1001 decision 6); only the top level was open"
)
WHERE = [
    "top level",
    pytest.param("envelope", marks=ENVELOPE_CONTROL),
]


def _spellings(field, value):
    return {"top level": {field: value}, "envelope": {"metadata": {field: value}}}


@pytest.mark.parametrize("where", WHERE)
@pytest.mark.parametrize(("etype", "field", "value"), MANAGED, ids=IDS)
def test_mcp_kb_create_refuses_a_managed_field(env, mcp, etype, field, value, where):
    spec = {"kb_name": KB, "entry_type": etype, "title": "T1", **_spellings(field, value)[where]}
    res = mcp._dispatch_tool("kb_create", spec)
    assert not res.get("created"), res
    assert field in json.dumps(res), res
    assert _md_files(env) == []


@pytest.mark.parametrize("where", WHERE)
@pytest.mark.parametrize(("etype", "field", "value"), MANAGED, ids=IDS)
def test_mcp_kb_bulk_create_refuses_the_item_and_creates_its_sibling(
    env, mcp, etype, field, value, where
):
    bad = {"entry_type": etype, "title": "Bad", **_spellings(field, value)[where]}
    good = {"entry_type": "note", "title": "Good"}
    res = mcp._dispatch_tool("kb_bulk_create", {"kb_name": KB, "entries": [bad, good]})
    first, second = res["results"]
    assert first["created"] is False and field in first["error"], first
    assert second["created"] is True, second
    assert _md_files(env) == ["good.md"]


@pytest.mark.parametrize("where", WHERE)
@pytest.mark.parametrize(("etype", "field", "value"), MANAGED, ids=IDS)
def test_cli_import_refuses_a_managed_field(env, etype, field, value, where):
    path = env["tmp_path"] / "in.json"
    path.write_text(
        json.dumps([{"entry_type": etype, "title": "T1", **_spellings(field, value)[where]}])
    )
    result = _cli(env, ["import", str(path), "-k", KB])
    assert result.exit_code == 1, result.output
    assert field in result.output, result.output
    assert _md_files(env) == []


@pytest.mark.parametrize(("etype", "field", "value"), MANAGED, ids=IDS)
def test_cli_create_f_refuses_a_managed_field(env, etype, field, value):
    raw = value if isinstance(value, str) else json.dumps(value)
    result = _cli(env, ["create", "-k", KB, "-t", etype, "--title", "T1", "-f", f"{field}={raw}"])
    assert result.exit_code == 1, result.output
    assert field in result.output, result.output
    assert _md_files(env) == []


@pytest.mark.parametrize(("etype", "field", "value"), MANAGED, ids=IDS)
def test_rest_bulk_import_refuses_a_managed_field(env, etype, field, value):
    from starlette.testclient import TestClient

    from pyrite.server.api import create_app, get_config, get_db, get_index_worker
    from pyrite.services.index_worker import IndexWorker

    config = env["config"]
    application = create_app(config=config)
    db = PyriteDB(env["db_path"])
    application.dependency_overrides[get_config] = lambda: config
    application.dependency_overrides[get_db] = lambda: db
    worker = IndexWorker(db, config)
    application.dependency_overrides[get_index_worker] = lambda: worker
    try:
        client = TestClient(application)
        body = json.dumps([{"entry_type": etype, "title": "T1", field: value}])
        resp = client.post(
            "/api/entries/import",
            params={"kb": KB},
            files={"file": ("in.json", body, "application/json")},
        )
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["imported"] == 0 and out["errors"] == 1, out
        assert field in out["error_details"][0]["error"], out
        assert _md_files(env) == []
    finally:
        worker.wait_for_idle(timeout=10)
        db.close()


@pytest.mark.control(
    reason="no alias of a managed field is declared today, so this guards the day one is"
)
def test_an_alias_of_a_managed_field_is_refused(env, mcp):
    from pyrite.models.core_types import get_entry_class
    from pyrite.schema.field_aliases import field_at

    task = get_entry_class("task")
    aliased = [
        k
        for k in ("assignee", "agent_context", "status_log", "evidence")
        if k not in task.managed_fields and field_at(task, "task", (k,))[0] in task.managed_fields
    ]
    # Whatever aliases the registry declares for a managed field must be refused
    # too; with none declared the metadata spelling is the alias.
    for key in aliased:
        res = mcp._dispatch_tool(
            "kb_create", {"kb_name": KB, "entry_type": "task", "title": "A", key: "x"}
        )
        assert not res.get("created"), (key, res)
    assert _md_files(env) == []


def test_create_entry_lets_an_in_process_owner_set_its_field(env):
    from pyrite.services.kb_service import KBService

    db = PyriteDB(env["db_path"])
    try:
        svc = KBService(env["config"], db)
        with pytest.raises(ValidationError, match="adr_number"):
            svc.create(KB, {"entry_type": "adr", "title": "Surface", "adr_number": 3})
        entry = svc.create_entry(KB, "adr-0003", "Owner", "adr", "b", adr_number=3)
        assert entry.adr_number == 3
    finally:
        db.close()


def test_task_commands_keeps_no_managed_field_list():
    """The rule lives in the service (principle 8): the CLI keeps only its own collisions."""
    from pathlib import Path

    src = (Path(__file__).parent.parent / "pyrite/cli/task_commands.py").read_text()
    assert "TaskEntry.managed_fields" not in src
    assert "_MANAGED_FIELDS" not in src


@pytest.mark.control(
    reason="task create refused this before, by its own list; now by the service rule"
)
def test_cli_task_create_still_refuses_a_managed_field(env):
    result = _cli(env, ["task", "create", "-k", KB, "T1", "--field", "evidence=forged"])
    assert result.exit_code != 0, result.output
    assert "evidence" in result.output
    assert _md_files(env) == []
