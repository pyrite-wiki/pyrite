"""One write pipeline: every create surface refuses the same spec the same way (#378).

The same entry spec is sent through each of the six create surfaces -- REST
`POST /api/entries`, REST `POST /api/entries/import`, MCP `kb_create`, MCP
`kb_bulk_create`, CLI `pyrite create` and CLI `pyrite import` -- and each must
refuse it with the same error code, and write nothing.

Before #378 each surface made its own decisions: MCP exempted core types from
the undeclared-type refusal and the CLI did not (#197); REST had no such check;
bulk create skipped the exists check (#359) and schema validation (#366); and
every surface re-implemented the ADR-0034 truncated-body refusal. The refusal
codes differed per surface too (`CREATE_FAILED` for everything on REST and
MCP). One private pipeline in `KBService` now makes every one of these
decisions, and the surfaces only map arguments and errors.

These run on the real surfaces: a FastAPI `TestClient`, the MCP dispatcher,
and Typer's `CliRunner`, all over one KB on disk.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from typer.testing import CliRunner

from pyrite.cli import app as cli_app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

runner = CliRunner()

KB = "parity"

#: A KB with a declared schema. `person` is declared (a core type, declared on
#: purpose so a core type is not exempt merely by being core); `note` is a core
#: type it does NOT declare; `role` is an enum. `finding` is declared only in
#: kb.yaml (no model class), with an enum `severity` (#386).
KB_YAML = """\
name: parity
validation:
  enforce: true
types:
  person:
    fields:
      role:
        type: select
        options: [author, editor]
  finding:
    fields:
      severity:
        type: select
        options: [low, high]
"""

EXISTING_ID = "existing-person"
EXISTING_FILE = f"""\
---
id: {EXISTING_ID}
title: Existing Person
type: person
role: author
---

The original body.
"""

#: (case id, entry spec, the code every surface must refuse it with, the id
#: the refused entry would have had).
CASES = [
    (
        "undeclared_type",
        {"entry_type": "widget", "title": "Parity Widget", "body": "b"},
        "UNDECLARED_TYPE",
        "parity-widget",
    ),
    (
        "core_type_not_declared",
        {"entry_type": "note", "title": "Parity Note", "body": "b"},
        "UNDECLARED_TYPE",
        "parity-note",
    ),
    (
        "duplicate_id",
        {"entry_type": "person", "title": "Existing Person", "body": "REPLACED", "role": "editor"},
        "ENTRY_EXISTS",
        EXISTING_ID,
    ),
    (
        "truncated_body",
        {
            "entry_type": "person",
            "title": "Parity Truncated",
            "body": "the first chunk only",
            "role": "author",
            "body_truncated": True,
        },
        "VALIDATION_FAILED",
        "parity-truncated",
    ),
    (
        "enum_violation",
        {"entry_type": "person", "title": "Parity Enum", "body": "b", "role": "bogus"},
        "SCHEMA_VIOLATION",
        "parity-enum",
    ),
    (
        "enum_violation_kb_yaml_only_type",
        {"entry_type": "finding", "title": "Parity Finding", "body": "b", "severity": "bogus"},
        "SCHEMA_VIOLATION",
        "parity-finding",
    ),
]


@pytest.fixture
def env(tmp_path):
    """One KB with a declared schema and one existing entry, on disk and indexed."""
    kb_path = tmp_path / KB
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text(KB_YAML)
    (kb_path / f"{EXISTING_ID}.md").write_text(EXISTING_FILE)
    db_path = tmp_path / "index.db"
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=db_path, auto_embed=False),
    )
    db = PyriteDB(db_path)
    IndexManager(db, config).index_all()
    db.close()
    return {"config": config, "kb_path": kb_path, "tmp_path": tmp_path, "db_path": db_path}


def _md_files(kb_path: Path) -> dict[str, bytes]:
    return {str(p.relative_to(kb_path)): p.read_bytes() for p in sorted(kb_path.rglob("*.md"))}


# ---------------------------------------------------------------------------
# One adapter per surface: send `spec`, return the refusal code it reported.
# ---------------------------------------------------------------------------


def _rest_client(env):
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
    closers = [db]
    state_db = getattr(application.state, "pyrite_db", None)
    if state_db is not None and state_db is not db:
        closers.append(state_db)
    return TestClient(application), worker, closers


def _with_rest(env, fn):
    client, worker, closers = _rest_client(env)
    try:
        return fn(client)
    finally:
        worker.wait_for_idle(timeout=10)
        for d in closers:
            d.close()


#: The fields POST /api/entries declares. Any other field travels in
#: `metadata`, as the web form sends a type's custom fields.
_REST_CREATE_FIELDS = {
    "entry_type",
    "title",
    "body",
    "date",
    "importance",
    "tags",
    "participants",
    "role",
    "metadata",
    "allow_undeclared",
    "body_truncated",
}


def rest_create(env, spec):
    extra = {k: spec.pop(k) for k in list(spec) if k not in _REST_CREATE_FIELDS}
    if extra:
        spec["metadata"] = {**spec.get("metadata", {}), **extra}

    def go(client):
        resp = client.post("/api/entries", json={"kb": KB, **spec})
        assert resp.status_code >= 400, resp.text
        return resp.json()["detail"]["code"]

    return _with_rest(env, go)


def rest_import(env, spec):
    def go(client):
        payload = json.dumps([spec]).encode()
        resp = client.post(
            f"/api/entries/import?kb={KB}&format=json",
            files={"file": ("import.json", payload, "application/json")},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["imported"] == 0, data
        return data["error_details"][0]["error_code"]

    return _with_rest(env, go)


def _with_mcp(env, fn):
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(env["config"], tier="admin")
    try:
        return fn(server)
    finally:
        server.close()


def mcp_create(env, spec):
    def go(server):
        res = server._dispatch_tool("kb_create", {"kb_name": KB, **spec})
        assert "error" in res, res
        return res["error_code"]

    return _with_mcp(env, go)


def mcp_bulk_create(env, spec):
    def go(server):
        res = server._dispatch_tool("kb_bulk_create", {"kb_name": KB, "entries": [spec]})
        assert res.get("created") == 0, res
        return res["results"][0]["error_code"]

    return _with_mcp(env, go)


def _cli(env, args, stdin=None):
    with (
        patch("pyrite.cli.load_config", return_value=env["config"]),
        patch("pyrite.cli.context.load_config", return_value=env["config"]),
    ):
        return runner.invoke(cli_app, args, input=stdin)


_CODE = re.compile(r"\[([A-Z_]+)\]")


def cli_create(env, spec):
    spec = dict(spec)
    args = ["create", "-k", KB, "-t", spec.pop("entry_type"), "--title", spec.pop("title")]
    args += ["--body", spec.pop("body"), "--format", "rich"]
    for k, v in spec.items():
        args += ["-f", f"{k}={json.dumps(v) if isinstance(v, bool) else v}"]
    result = _cli(env, args)
    assert result.exit_code == 1, result.output
    m = _CODE.search(result.output)
    assert m, result.output
    return m.group(1)


def cli_import(env, spec):
    path = env["tmp_path"] / "import.json"
    path.write_text(json.dumps([spec]))
    result = _cli(env, ["import", str(path), "-k", KB])
    assert result.exit_code == 1, result.output
    failed = [line for line in result.output.splitlines() if "Failed" in line or "Refused" in line]
    assert failed, result.output
    m = _CODE.search(failed[0])
    assert m, result.output
    return m.group(1)


SURFACES = {
    "rest_create": rest_create,
    "rest_import": rest_import,
    "mcp_kb_create": mcp_create,
    "mcp_kb_bulk_create": mcp_bulk_create,
    "cli_create": cli_create,
    "cli_import": cli_import,
}


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize(("case", "spec", "code", "entry_id"), CASES, ids=[c[0] for c in CASES])
def test_every_surface_refuses_with_the_same_code_and_writes_nothing(
    env, surface, case, spec, code, entry_id
):
    before = _md_files(env["kb_path"])

    got = SURFACES[surface](env, dict(spec))

    assert got == code, f"{surface} refused {case} with {got}, expected {code}"
    # Nothing written: the refused entry does not exist, and a duplicate left
    # the original byte-identical.
    assert _md_files(env["kb_path"]) == before


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_every_surface_creates_a_valid_spec(env, surface):
    """The negative control: the pipeline refuses the bad specs, not every spec."""
    spec = {"entry_type": "person", "title": "Parity Valid", "body": "b", "role": "editor"}
    fn = {
        "rest_create": lambda: _with_rest(
            env, lambda c: c.post("/api/entries", json={"kb": KB, **spec}).status_code
        ),
        "rest_import": lambda: _with_rest(
            env,
            lambda c: c.post(
                f"/api/entries/import?kb={KB}&format=json",
                files={"file": ("i.json", json.dumps([spec]).encode(), "application/json")},
            ).json()["imported"],
        ),
        "mcp_kb_create": lambda: _with_mcp(
            env, lambda s: s._dispatch_tool("kb_create", {"kb_name": KB, **spec}).get("created")
        ),
        "mcp_kb_bulk_create": lambda: _with_mcp(
            env,
            lambda s: s._dispatch_tool("kb_bulk_create", {"kb_name": KB, "entries": [spec]})[
                "created"
            ],
        ),
        "cli_create": lambda: (
            _cli(
                env,
                ["create", "-k", KB, "-t", "person", "--title", "Parity Valid", "-b", "b"]
                + ["-f", "role=editor"],
            ).exit_code
        ),
        "cli_import": lambda: (
            (env["tmp_path"] / "ok.json").write_text(json.dumps([spec])),
            _cli(env, ["import", str(env["tmp_path"] / "ok.json"), "-k", KB]).exit_code,
        )[1],
    }[surface]
    fn()
    written = list(env["kb_path"].rglob("parity-valid.md"))
    assert len(written) == 1, surface
    assert "role: editor" in written[0].read_text()
