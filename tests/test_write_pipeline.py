"""The KBService write pipeline, through its surfaces (#378, #359, #366).

`tests/test_write_surface_parity.py` pins that every create surface refuses the
same spec with the same code. This file pins the rest of the theme:

- bulk create refuses an existing id per item, including a duplicate inside
  one batch, and leaves the existing file byte-identical (#359);
- bulk create applies schema *and plugin* validation per item, with the
  message `kb_create` gives, siblings still created in order (#366);
- MCP `kb_update` takes the entry type's own field set from the registry and
  the KB schema, not a hand-kept allowlist (#378 acceptance 5);
- REST create and update return the pipeline's warnings;
- `pyrite add` goes through the same pipeline (undeclared types refused).
"""

from __future__ import annotations

import json
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

KB = "pipe"

KB_YAML = """\
name: pipe
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
  note:
    fields:
      mood:
        type: select
        options: [calm, busy]
        allow_other: true
"""


@pytest.fixture
def env(tmp_path):
    kb_path = tmp_path / KB
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text(KB_YAML)
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


def _cli(env, args):
    with (
        patch("pyrite.cli.load_config", return_value=env["config"]),
        patch("pyrite.cli.context.load_config", return_value=env["config"]),
    ):
        return runner.invoke(cli_app, args)


def _stop_enforcing(env):
    """Drop `validation.enforce`: with it on, an unknown type is a schema error
    even when the undeclared-type refusal is overridden (that is what enforce
    means), so the override is only observable in a non-enforcing KB."""
    (env["kb_path"] / "kb.yaml").write_text(KB_YAML.replace("validation:\n  enforce: true\n", ""))
    env["config"].get_kb(KB).invalidate_schema_cache()


def _file(env, entry_id):
    found = list(env["kb_path"].rglob(f"{entry_id}.md"))
    assert len(found) <= 1, found
    return found[0] if found else None


# ---------------------------------------------------------------------------
# #359: bulk create never replaces
# ---------------------------------------------------------------------------


def test_bulk_create_refuses_an_existing_id_and_leaves_the_file_byte_identical(env, mcp):
    first = mcp._dispatch_tool(
        "kb_bulk_create",
        {"kb_name": KB, "entries": [{"entry_type": "person", "title": "Ok", "body": "ORIGINAL"}]},
    )
    assert first["created"] == 1, first
    original = _file(env, "ok").read_bytes()

    res = mcp._dispatch_tool(
        "kb_bulk_create",
        {
            "kb_name": KB,
            "entries": [
                {"entry_type": "person", "title": "Before", "body": "b"},
                {"entry_type": "person", "title": "Ok", "body": "REPLACED"},
                {"entry_type": "person", "title": "After", "body": "b"},
            ],
        },
    )
    assert [r["created"] for r in res["results"]] == [True, False, True], res
    assert res["results"][1]["error_code"] == "ENTRY_EXISTS"
    single = mcp._dispatch_tool(
        "kb_create", {"kb_name": KB, "entry_type": "person", "title": "Ok", "body": "x"}
    )
    assert res["results"][1]["error"] == single["error"]
    assert _file(env, "ok").read_bytes() == original


def test_bulk_create_two_items_with_one_id_creates_the_first_and_refuses_the_second(env, mcp):
    res = mcp._dispatch_tool(
        "kb_bulk_create",
        {
            "kb_name": KB,
            "entries": [
                {"entry_type": "person", "title": "Twin", "body": "FIRST"},
                {"entry_type": "person", "title": "Twin", "body": "SECOND"},
            ],
        },
    )
    assert res["results"][0] == {"created": True, "entry_id": "twin"}, res
    assert res["results"][1]["error_code"] == "ENTRY_EXISTS", res
    assert "FIRST" in _file(env, "twin").read_text()


def test_cli_import_refuses_an_existing_id(env):
    path = env["tmp_path"] / "a.json"
    path.write_text(json.dumps([{"entry_type": "person", "title": "Dup", "body": "ORIGINAL"}]))
    assert _cli(env, ["import", str(path), "-k", KB]).exit_code == 0
    original = _file(env, "dup").read_bytes()

    path.write_text(json.dumps([{"entry_type": "person", "title": "Dup", "body": "REPLACED"}]))
    result = _cli(env, ["import", str(path), "-k", KB])
    assert result.exit_code == 1, result.output
    assert "[ENTRY_EXISTS]" in result.output, result.output
    assert _file(env, "dup").read_bytes() == original


# ---------------------------------------------------------------------------
# #366: bulk create validates each item
# ---------------------------------------------------------------------------


def test_bulk_create_refuses_a_schema_violation_per_item_with_the_create_message(env, mcp):
    bad = {"entry_type": "person", "title": "Bad Role", "body": "b", "role": "chore"}
    res = mcp._dispatch_tool(
        "kb_bulk_create",
        {
            "kb_name": KB,
            "entries": [
                {"entry_type": "person", "title": "Good One", "body": "b", "role": "author"},
                bad,
                {"entry_type": "person", "title": "Good Two", "body": "b"},
            ],
        },
    )
    assert [r["created"] for r in res["results"]] == [True, False, True], res
    assert res["results"][1]["error_code"] == "SCHEMA_VIOLATION"
    single = mcp._dispatch_tool("kb_create", {"kb_name": KB, **bad})
    assert res["results"][1]["error"] == single["error"], (res, single)
    assert _file(env, "bad-role") is None


def test_bulk_create_runs_plugin_validators(env, mcp, monkeypatch):
    """#366 names plugin validation too: a plugin validator's error refuses the item."""
    from pyrite.plugins import get_registry

    def no_blue(entry_type, fields, ctx):
        if fields.get("title", "").startswith("Blue"):
            return [{"field": "title", "rule": "no_blue", "expected": "not blue", "got": "blue"}]
        return []

    monkeypatch.setattr(get_registry(), "get_validators_for_kb", lambda kb_type: [no_blue])
    res = mcp._dispatch_tool(
        "kb_bulk_create",
        {
            "kb_name": KB,
            "entries": [
                {"entry_type": "person", "title": "Blue Person", "body": "b"},
                {"entry_type": "person", "title": "Red Person", "body": "b"},
            ],
        },
    )
    assert [r["created"] for r in res["results"]] == [False, True], res
    assert res["results"][0]["error_code"] == "SCHEMA_VIOLATION"
    assert "no_blue" in res["results"][0]["error"]


def test_cli_import_refuses_a_schema_violation(env):
    path = env["tmp_path"] / "v.json"
    path.write_text(
        json.dumps(
            [
                {"type": "person", "title": "Probe Bulk", "role": "bogus"},
                {"type": "person", "title": "Probe Fine", "role": "editor"},
            ]
        )
    )
    result = _cli(env, ["import", str(path), "-k", KB])
    assert result.exit_code == 1, result.output
    assert "[SCHEMA_VIOLATION]" in result.output
    assert _file(env, "probe-bulk") is None
    assert "role: editor" in _file(env, "probe-fine").read_text()


def test_cli_import_dry_run_reports_refusals_without_writing(env):
    path = env["tmp_path"] / "d.json"
    path.write_text(json.dumps([{"type": "person", "title": "Dry Bad", "role": "bogus"}]))
    result = _cli(env, ["import", str(path), "-k", KB, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Would refuse [SCHEMA_VIOLATION]" in result.output, result.output
    assert _file(env, "dry-bad") is None


# ---------------------------------------------------------------------------
# #378 acceptance 5: kb_update's field set comes from the type
# ---------------------------------------------------------------------------


def _create(mcp, **spec):
    res = mcp._dispatch_tool("kb_create", {"kb_name": KB, **spec})
    assert res.get("created"), res
    return res["entry_id"]


def test_kb_update_writes_a_model_field_the_old_allowlist_dropped(env, mcp):
    """A document's `url` is its model's field; the hand-kept allowlist omitted it."""
    eid = _create(mcp, entry_type="document", title="Doc", body="b", allow_undeclared=True)
    res = mcp._dispatch_tool(
        "kb_update", {"kb_name": KB, "entry_id": eid, "url": "https://example.org/d"}
    )
    assert res.get("updated"), res
    assert "url: https://example.org/d" in _file(env, eid).read_text()


def test_kb_update_writes_a_kb_yaml_declared_field_of_a_custom_type(env, mcp):
    """A kb.yaml-only type's declared field is updatable, and validated."""
    path = env["kb_path"] / "f1.md"
    path.write_text("---\nid: f1\ntitle: F1\ntype: finding\nseverity: low\n---\n\nb\n")
    mcp.index_mgr.index_all()

    res = mcp._dispatch_tool("kb_update", {"kb_name": KB, "entry_id": "f1", "severity": "high"})
    assert res.get("updated"), res
    assert "severity: high" in path.read_text()

    bad = mcp._dispatch_tool("kb_update", {"kb_name": KB, "entry_id": "f1", "severity": "odd"})
    assert bad.get("error_code") == "SCHEMA_VIOLATION", bad
    assert "severity: high" in path.read_text()


def test_kb_update_ignores_identity_and_bookkeeping_fields(env, mcp):
    """An agent echoing a read result back cannot rewrite id, path or created_at."""
    eid = _create(mcp, entry_type="person", title="Keep Me", body="b")
    got = mcp._dispatch_tool("kb_get", {"kb_name": KB, "entry_id": eid})
    before = _file(env, eid)
    created_line = [ln for ln in before.read_text().splitlines() if ln.startswith("created_at")]
    res = mcp._dispatch_tool(
        "kb_update",
        {
            **got,
            "kb_name": KB,
            "entry_id": eid,
            "id": "hijacked",
            "file_path": "/tmp/elsewhere.md",
            "created_at": "1999-01-01T00:00:00Z",
            "links": [{"target": "nowhere"}],
            "body": "edited",
        },
    )
    assert res.get("updated"), res
    after = _file(env, eid)
    assert after == before, "the entry moved"
    text = after.read_text()
    assert "id: keep-me" in text and "hijacked" not in text
    assert "1999" not in text and "nowhere" not in text
    assert [ln for ln in text.splitlines() if ln.startswith("created_at")] == created_line
    assert "edited" in text


def test_kb_update_field_set_names_no_extension_vocabulary(env, mcp):
    """`funder` was in the core allowlist; a person has no such field."""
    eid = _create(mcp, entry_type="person", title="No Funder", body="b")
    assert "funder" not in mcp.svc.updatable_fields(eid, KB)
    assert {"role", "title", "body", "lifecycle"} <= mcp.svc.updatable_fields(eid, KB)


# ---------------------------------------------------------------------------
# Warnings reach REST; `pyrite add` shares the pipeline
# ---------------------------------------------------------------------------


@pytest.fixture
def rest(env):
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
    yield TestClient(application)
    worker.wait_for_idle(timeout=10)
    db.close()
    state_db = getattr(application.state, "pyrite_db", None)
    if state_db is not None and state_db is not db:
        state_db.close()


def test_rest_create_and_update_return_the_pipeline_warnings(env, rest):
    resp = rest.post(
        "/api/entries",
        json={"kb": KB, "entry_type": "note", "title": "Moody", "metadata": {"mood": "wild"}},
    )
    assert resp.status_code == 200, resp.text
    warnings = resp.json()["warnings"]
    assert any(w.get("field") == "mood" for w in warnings), warnings

    resp = rest.put("/api/entries/moody", json={"kb": KB, "metadata": {"mood": "wilder"}})
    assert resp.status_code == 200, resp.text
    assert any(w.get("got") == "wilder" for w in resp.json()["warnings"]), resp.json()


def test_rest_create_duplicate_is_a_conflict(env, rest):
    body = {"kb": KB, "entry_type": "person", "title": "Once", "body": "b"}
    assert rest.post("/api/entries", json=body).status_code == 200
    resp = rest.post("/api/entries", json=body)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "ENTRY_EXISTS"


def test_rest_create_allow_undeclared_overrides_the_refusal(env, rest):
    _stop_enforcing(env)
    body = {"kb": KB, "entry_type": "widget", "title": "Allowed Widget", "body": "b"}
    refused = rest.post("/api/entries", json=body)
    assert refused.status_code == 400
    assert refused.json()["detail"]["declared_types"] == ["finding", "note", "person"]
    resp = rest.post("/api/entries", json={**body, "allow_undeclared": True})
    assert resp.status_code == 200, resp.text
    assert _file(env, "allowed-widget") is not None


def test_cli_add_refuses_an_undeclared_type_unless_allowed(env):
    _stop_enforcing(env)
    src = env["tmp_path"] / "w.md"
    src.write_text("---\ntitle: Added Widget\ntype: widget\n---\n\nbody\n")
    result = _cli(env, ["add", str(src), "-k", KB])
    assert result.exit_code == 1, result.output
    assert "[UNDECLARED_TYPE]" in result.output
    assert _file(env, "added-widget") is None

    result = _cli(env, ["add", str(src), "-k", KB, "--allow-undeclared"])
    assert result.exit_code == 0, result.output
    assert _file(env, "added-widget") is not None


def test_cli_add_refuses_a_truncated_body(env):
    src = env["tmp_path"] / "t.md"
    src.write_text("---\ntitle: Added Chunk\ntype: person\nbody_truncated: true\n---\n\npartial\n")
    result = _cli(env, ["add", str(src), "-k", KB])
    assert result.exit_code == 1, result.output
    assert "[VALIDATION_FAILED]" in result.output
    assert _file(env, "added-chunk") is None


def test_rest_import_allow_undeclared_overrides_the_refusal(env, rest):
    _stop_enforcing(env)
    spec = [{"entry_type": "widget", "title": "Imported Widget", "body": "b"}]
    payload = json.dumps(spec).encode()
    files = {"file": ("i.json", payload, "application/json")}
    refused = rest.post(f"/api/entries/import?kb={KB}&format=json", files=files).json()
    assert refused["error_details"][0]["error_code"] == "UNDECLARED_TYPE", refused
    files = {"file": ("i.json", payload, "application/json")}
    resp = rest.post(f"/api/entries/import?kb={KB}&format=json&allow_undeclared=true", files=files)
    assert resp.json()["imported"] == 1, resp.json()


def test_cli_import_dry_run_works_on_a_read_only_kb(env):
    """A dry run writes nothing, so a read-only KB can still be checked."""
    env["config"].get_kb(KB).read_only = True
    path = env["tmp_path"] / "ro.json"
    path.write_text(json.dumps([{"type": "person", "title": "Ro Check", "role": "bogus"}]))
    result = _cli(env, ["import", str(path), "-k", KB, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Would refuse [SCHEMA_VIOLATION]" in result.output, result.output


def test_bulk_create_missing_title_is_a_validation_refusal(env, mcp):
    """#358's per-item title failure now carries the pipeline's code too."""
    res = mcp._dispatch_tool(
        "kb_bulk_create",
        {"kb_name": KB, "entries": [{"entry_type": "person", "body": "untitled"}]},
    )
    assert res["results"][0] == {
        "created": False,
        "error": "title is required",
        "error_code": "VALIDATION_FAILED",
    }, res


def test_bulk_create_a_storage_error_reports_its_public_message_not_the_raw_detail(
    env, mcp, monkeypatch
):
    """ADR-0037 theme 2 round 2 (conductor cold read of 5d65caa7, item 1):
    a StorageError's str(exc) can carry server-side detail (a real path, a
    driver's own text) -- _refusal_result (pyrite/services/kb_service.py)
    put that raw text straight into the bulk per-item result's "error"
    field, bypassing the public_message REST's central handler and MCP's
    _refusal already respect. The real detail belongs in the server log
    only.
    """
    from pyrite.exceptions import StorageError
    from pyrite.services import kb_service as kb_service_module

    def _boom(self, kb_name, kb_config, spec, **kwargs):
        raise StorageError("Failed to write /real/secret/server/path/entry.md: disk quota exceeded")

    monkeypatch.setattr(kb_service_module.KBService, "_prepare_and_save", _boom)
    res = mcp._dispatch_tool(
        "kb_bulk_create",
        {"kb_name": KB, "entries": [{"entry_type": "person", "title": "X", "body": "b"}]},
    )
    error_text = res["results"][0]["error"]
    assert "/real/secret/server/path" not in error_text, res
    assert error_text == (
        "A storage operation failed. An administrator needs to check the "
        "server log for the underlying error."
    ), res


def test_cli_add_validate_only_reports_a_refusal_without_writing(env):
    src = env["tmp_path"] / "v.md"
    src.write_text("---\ntitle: Checked Person\ntype: person\nrole: bogus\n---\n\nbody\n")
    result = _cli(env, ["add", str(src), "-k", KB, "--validate-only"])
    assert result.exit_code == 1, result.output
    assert "Validation failed" in result.output and "role" in result.output, result.output
    assert _file(env, "checked-person") is None


# ---------------------------------------------------------------------------
# Cold-read fix round (#381 review)
# ---------------------------------------------------------------------------


def _kb_env(tmp_path, name, kb_type, kb_yaml):
    kb_path = tmp_path / name
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text(kb_yaml)
    db_path = tmp_path / f"{name}.db"
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=name, path=kb_path, kb_type=kb_type)],
        settings=Settings(index_path=db_path, auto_embed=False),
    )
    db = PyriteDB(db_path)
    IndexManager(db, config).index_all()
    db.close()
    return {"config": config, "kb_path": kb_path, "tmp_path": tmp_path, "db_path": db_path}


#: Pyrite's own kb/kb.yaml, the KB a web user of this repo creates entries in.
PYRITE_KB_YAML = (Path(__file__).resolve().parents[1] / "kb" / "kb.yaml").read_text()


@pytest.fixture
def software_env(tmp_path):
    return _kb_env(tmp_path, "sw", "software", PYRITE_KB_YAML)


def _rest_for(env):
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

    def close():
        worker.wait_for_idle(timeout=10)
        for d in closers:
            d.close()

    return TestClient(application), close


def test_cli_add_keeps_the_files_declared_type_in_a_plugin_kb(software_env):
    """Review blocker 1: `pyrite add` must not rewrite `type: note` to `adr` (#197)."""
    src = software_env["tmp_path"] / "meeting.md"
    src.write_text("---\ntype: note\ntitle: Meeting notes\n---\nhello\n")
    result = _cli(software_env, ["add", str(src), "-k", "sw", "--allow-undeclared"])
    assert result.exit_code == 0, result.output
    saved = list(software_env["kb_path"].rglob("meeting-notes.md"))
    assert len(saved) == 1, saved
    text = saved[0].read_text()
    assert "type: note" in text and "adr_number" not in text, text


def test_rest_create_accepts_the_web_forms_default_payload_in_pyrites_own_kb(software_env):
    """Review blocker 2. The New-entry form defaults to `note` and offers every
    core and plugin type; the web client sends `allow_undeclared: true` on every
    create (web/src/lib/api/client.ts), so the form keeps dev's behaviour in a
    KB that declares types. API callers that do not send it are refused.

    The exact-path assertion below is red without #391's fix for an incidental
    reason worth naming: this KB's `note` -> most-derived-NoteEntry-subtype
    resolution silently resolves the entry to an ADREntry (#468, filed,
    out of scope for #391), so the file only lands under `adrs/` with the
    `0000-` prefix once the `adr` type's `file_pattern` entry-field
    placeholder support (this PR) exists at all; on dev, `{adr_number:04d}`
    is unrecognized and `resolve_filename` falls back to the default
    `<id>.md`, landing at `adrs/from-the-form.md` instead."""
    client, close = _rest_for(software_env)
    try:
        form = {"kb": "sw", "entry_type": "note", "title": "From the form", "body": ""}
        refused = client.post("/api/entries", json=form)
        assert refused.status_code == 400, refused.text
        assert refused.json()["detail"]["code"] == "UNDECLARED_TYPE"

        resp = client.post("/api/entries", json={**form, "allow_undeclared": True})
        assert resp.status_code == 200, resp.text
        # Exact path, not a substring match: #468 (filed, out of scope for
        # #391) means this KB's `note` -> most-derived-NoteEntry-subtype
        # resolution silently resolves this to an ADREntry, so the file
        # lands under adrs/ with the adr type's file_pattern
        # (adr_number defaults to 0 since this create never sets it).
        assert resp.json()["id"] == "from-the-form"
        expected = software_env["kb_path"] / "adrs" / "0000-from-the-form.md"
        assert expected.exists(), (
            f"expected {expected} (see #468: `note` resolves to ADREntry here); "
            f"found: {sorted(p.relative_to(software_env['kb_path']) for p in software_env['kb_path'].rglob('*.md'))}"
        )
    finally:
        close()


def test_mcp_kb_update_refuses_a_marker_nested_under_any_key(env, mcp):
    """Review blocker 3: ADR-0034's any-depth rule on the raw kb_update args."""
    eid = _create(mcp, entry_type="person", title="Deep Marker", body="WHOLE BODY")
    res = mcp._dispatch_tool(
        "kb_update",
        {"kb_name": KB, "entry_id": eid, "body": "frag", "extra": {"body_truncated": True}},
    )
    assert res.get("error_code") == "VALIDATION_FAILED", res
    assert "legacy_error_code" not in res, res
    assert "WHOLE BODY" in _file(env, eid).read_text()


@pytest.mark.parametrize("method", ["put", "patch"])
def test_rest_update_refuses_a_marker_nested_under_any_key(env, rest, method):
    """Review blocker 3, REST half: the raw request body is checked, not the model."""
    resp = rest.post(
        "/api/entries",
        json={"kb": KB, "entry_type": "person", "title": "Rest Deep", "body": "WHOLE"},
    )
    assert resp.status_code == 200, resp.text
    if method == "put":
        payload = {"kb": KB, "body": "frag", "extra": {"body_truncated": True}}
    else:
        payload = {"kb": KB, "field": "body", "value": "frag", "extra": {"body_truncated": True}}
    resp = getattr(rest, method)("/api/entries/rest-deep", json=payload)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "VALIDATION_FAILED"
    assert "WHOLE" in _file(env, "rest-deep").read_text()


@pytest.mark.parametrize("method", ["post", "put"])
def test_rest_a_stray_field_key_does_not_skip_the_truncation_check(env, rest, method):
    """Delta review on 9d2d3d47: PATCH was detected by a `field` key in the payload,
    so a POST or PUT carrying a stray `field` skipped the ADR-0034 check and wrote
    the fragment. Only the method decides that a request is a PATCH."""
    resp = rest.post(
        "/api/entries",
        json={"kb": KB, "entry_type": "person", "title": "Rest Stray", "body": "WHOLE"},
    )
    assert resp.status_code == 200, resp.text
    marked = {"kb": KB, "body": "frag", "body_truncated": True, "field": "title"}
    if method == "post":
        resp = rest.post("/api/entries", json={**marked, "entry_type": "person", "title": "Other"})
    else:
        resp = rest.put("/api/entries/rest-stray", json=marked)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "VALIDATION_FAILED"
    assert "WHOLE" in _file(env, "rest-stray").read_text()


@pytest.fixture
def task_env(tmp_path):
    return _kb_env(tmp_path, "tk", "generic", "name: tk\ntypes:\n  task: {}\n")


def test_task_audit_fields_are_not_updatable_through_mcp(task_env):
    """Review blocker 4: a task's audit trail is the task service's, not the caller's."""
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(task_env["config"], tier="admin")
    try:
        assert server._dispatch_tool("task_create", {"kb_name": "tk", "title": "T"}).get("created")
        fields = server.svc.updatable_fields("t", "tk")
        assert not fields & {"status_change_log", "evidence", "agent_context", "assigned_at"}
        path = next(task_env["kb_path"].rglob("t.md"))
        before = path.read_text()
        res = server._dispatch_tool(
            "kb_update",
            {
                "kb_name": "tk",
                "entry_id": "t",
                "status_change_log": [{"from": "x", "to": "y", "by": "forged"}],
                "evidence": ["forged"],
                "agent_context": {"forged": True},
            },
        )
        assert res.get("updated"), res
        assert "forged" not in path.read_text()
        assert path.read_text().count("---") == before.count("---")
    finally:
        server.close()


def test_rest_patch_refuses_a_managed_field_and_leaves_one_file(env, rest, task_env):
    rest.post(
        "/api/entries", json={"kb": KB, "entry_type": "person", "title": "Pinned", "body": "b"}
    )
    resp = rest.patch("/api/entries/pinned", json={"kb": KB, "field": "id", "value": "moved"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "VALIDATION_FAILED"
    assert sorted(p.name for p in env["kb_path"].rglob("*.md")) == ["pinned.md"]


def test_rest_patch_stores_an_undeclared_field(env, rest):
    """#407: PATCH with `field: foo` (not a model attribute, not schema-declared)
    used to return 200 and write nothing. It now stores the key the way
    `create` would have."""
    resp = rest.post(
        "/api/entries", json={"kb": KB, "entry_type": "note", "title": "Undeclared", "body": "b"}
    )
    assert resp.status_code == 200, resp.text
    entry_id = resp.json()["id"]

    resp = rest.patch(f"/api/entries/{entry_id}", json={"kb": KB, "field": "foo", "value": "bar"})
    assert resp.status_code == 200, resp.text

    text = next(env["kb_path"].rglob(f"{entry_id}.md")).read_text()
    assert "foo: bar" in text, text


def test_rest_patch_type_refusal_on_a_missing_entry_is_still_404(env, rest):
    """Round-2 cold read: `type`/`entry_type`/empty-key refusal used to run
    before the KB and entry lookups in `_update`, so a PATCH naming both a
    bogus field AND a nonexistent entry answered 400 `VALIDATION_FAILED`
    instead of the 404 `NOT_FOUND` a missing entry should always give,
    whatever else is wrong with the request."""
    resp = rest.patch("/api/entries/does-not-exist", json={"kb": KB, "field": "type", "value": "x"})
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "NOT_FOUND"


def test_rest_patch_entry_type_is_refused_cleanly_not_a_500(env, rest):
    """`entry_type` is `type`'s sibling reserved-but-unsettable name -- no
    entry has a settable `entry_type` attribute (it's a read-only property).
    Before this fix `PATCH field=entry_type` raised a raw AttributeError,
    which FastAPI would turn into an unhandled 500 rather than the 400
    `VALIDATION_FAILED` every other refusal produces."""
    resp = rest.post(
        "/api/entries", json={"kb": KB, "entry_type": "note", "title": "ET", "body": "b"}
    )
    assert resp.status_code == 200, resp.text
    entry_id = resp.json()["id"]

    resp = rest.patch(
        f"/api/entries/{entry_id}", json={"kb": KB, "field": "entry_type", "value": "hacked"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "VALIDATION_FAILED"


def test_rest_patch_refuses_a_task_audit_field(task_env):
    from pyrite.services.kb_service import KBService

    db = PyriteDB(task_env["db_path"])
    try:
        from pyrite.services.task_service import TaskService

        TaskService(task_env["config"], db).create_task(kb_name="tk", title="Audited")
    finally:
        db.close()
    client, close = _rest_for(task_env)
    try:
        resp = client.patch(
            "/api/entries/audited",
            json={"kb": "tk", "field": "status_change_log", "value": "forged"},
        )
        assert resp.status_code == 400, resp.text
        assert "forged" not in next(task_env["kb_path"].rglob("audited.md")).read_text()
    finally:
        close()
    assert KBService  # imported for the service contract under test


def test_cli_update_refuses_an_identity_field_and_leaves_one_file(env):
    assert (
        _cli(env, ["create", "-k", KB, "-t", "person", "--title", "Stay", "-b", "b"]).exit_code == 0
    )
    result = _cli(env, ["update", "stay", "-k", KB, "--field", "id=b"])
    assert result.exit_code == 1, result.output
    assert "VALIDATION_FAILED" in result.output
    assert sorted(p.name for p in env["kb_path"].rglob("*.md")) == ["stay.md"]


def test_adr_number_is_managed_by_the_software_plugin(software_env):
    from pyrite.server.mcp_server import PyriteMCPServer

    adr = software_env["kb_path"] / "adrs"
    adr.mkdir(exist_ok=True)
    (adr / "adr-0001.md").write_text(
        "---\nid: adr-0001\ntitle: First\ntype: adr\nadr_number: 1\nstatus: accepted\n---\nb\n"
    )
    server = PyriteMCPServer(software_env["config"], tier="admin")
    try:
        server.index_mgr.index_all()
        fields = server.svc.updatable_fields("adr-0001", "sw")
        assert "adr_number" not in fields
        assert "status" in fields
    finally:
        server.close()


def test_create_refuses_a_resolved_path_collision_between_different_ids(software_env):
    """#391 cold read blocker 1. `_prepare`'s exists check only looked at the
    id, but a `file_pattern` type's filename comes from FIELDS
    (`{adr_number:04d}-{title}.md`), not the id -- two different ids can
    resolve to the SAME path. Before the fix, `create_entry('adr-a', ...)`
    then `create_entry('adr-b', ...)`, both with `adr_number=5`, both
    reported `created` and the second silently overwrote the first's file."""
    from pyrite.exceptions import EntryExistsError
    from pyrite.services.kb_service import KBService
    from pyrite.storage.database import PyriteDB

    db = PyriteDB(software_env["db_path"])
    try:
        svc = KBService(software_env["config"], db)
        first = svc.create_entry(
            "sw", "adr-a", "Same Title", "adr", "ORIGINAL", adr_number=5, status="proposed"
        )
        expected_file = software_env["kb_path"] / "adrs" / "0005-same-title.md"
        assert expected_file.exists(), expected_file
        original = expected_file.read_bytes()
        assert first.id == "adr-a"

        with pytest.raises(EntryExistsError):
            svc.create_entry(
                "sw", "adr-b", "Same Title", "adr", "REPLACED", adr_number=5, status="proposed"
            )

        assert expected_file.read_bytes() == original, "the first ADR's file must be untouched"
        # And the second id must never have been written under ANY name.
        assert not list(software_env["kb_path"].rglob("*adr-b*"))
    finally:
        db.close()


def test_create_refuses_a_resolved_path_collision_from_unnumbered_titles(software_env):
    """Two ADRs without an explicit adr_number both default to 0 and slug the
    same title identically -- both would resolve to `0000-same-idea.md`."""
    from pyrite.exceptions import EntryExistsError
    from pyrite.services.kb_service import KBService
    from pyrite.storage.database import PyriteDB

    db = PyriteDB(software_env["db_path"])
    try:
        svc = KBService(software_env["config"], db)
        svc.create_entry("sw", "adr-first", "Same Idea", "adr", "ORIGINAL", status="proposed")
        expected_file = software_env["kb_path"] / "adrs" / "0000-same-idea.md"
        assert expected_file.exists(), expected_file
        original = expected_file.read_bytes()

        with pytest.raises(EntryExistsError):
            svc.create_entry("sw", "adr-second", "Same Idea", "adr", "REPLACED", status="proposed")

        assert expected_file.read_bytes() == original
    finally:
        db.close()


def test_create_write_is_exclusive_against_a_true_toctou_race(software_env):
    """#391 cold read round 2 item 2. `_prepare`'s exists() check and the
    write that follows it are two different moments: two truly concurrent
    creates can both pass the check before either publishes. This test
    simulates the race window itself -- a `before_save` hook (which runs
    strictly after `_prepare`'s check and strictly before the write) writes
    the "other process's" file to disk as its side effect, then the create
    continues into `save_entry`. Without an exclusive publish, `os.replace`
    always succeeds, so the racing create would silently overwrite the
    winner's file and report `created` anyway."""
    from pyrite.exceptions import EntryExistsError
    from pyrite.plugins.registry import get_registry
    from pyrite.services.kb_service import KBService
    from pyrite.storage.database import PyriteDB

    winner_file = software_env["kb_path"] / "adrs" / "0005-race.md"

    def concurrent_winner(entry, ctx):
        # Simulate another process's create landing on disk in the window
        # between this create's exists() check and its own write.
        winner_file.parent.mkdir(parents=True, exist_ok=True)
        winner_file.write_text(
            "---\nid: adr-winner\ntype: adr\ntitle: Race\nadr_number: 5\n"
            "status: proposed\n---\n\nWINNER\n"
        )

    class RaceHookPlugin:
        name = "race_hook_plugin"

        def get_hooks(self):
            return {"before_save": [concurrent_winner]}

    reg = get_registry()
    reg.register(RaceHookPlugin())
    db = PyriteDB(software_env["db_path"])
    try:
        svc = KBService(software_env["config"], db)
        with pytest.raises(EntryExistsError):
            svc.create_entry("sw", "adr-loser", "Race", "adr", "LOSER", adr_number=5)

        assert winner_file.exists()
        assert "WINNER" in winner_file.read_text(), (
            "the exclusive write must refuse, not silently overwrite the concurrent winner's file"
        )
        assert len(list(software_env["kb_path"].rglob("*.md"))) == 1
    finally:
        del reg._plugins["race_hook_plugin"]
        db.close()


def test_update_keeps_the_existing_filename_for_a_file_pattern_type(software_env):
    """#391 cold read blocker 2. `document_manager.py` deleted the old file
    and wrote a new one whenever the resolved path changed on UPDATE. With a
    `file_pattern` like `{adr_number:04d}-{title}.md`, EVERY title edit (and,
    before #391's fix, even just updating `status`) renamed the file. A
    file's name is fixed at creation; only create resolves a filename."""
    from pyrite.services.kb_service import KBService
    from pyrite.storage.database import PyriteDB

    db = PyriteDB(software_env["db_path"])
    try:
        svc = KBService(software_env["config"], db)
        svc.create_entry(
            "sw", "adr-x", "Original Title", "adr", "body", adr_number=9, status="proposed"
        )
        original_file = software_env["kb_path"] / "adrs" / "0009-original-title.md"
        assert original_file.exists(), original_file

        # Update the status only: the resolved path is unaffected by status,
        # so this pins the simplest case first.
        svc.update_entry("adr-x", "sw", status="accepted")
        assert original_file.exists(), "status update must not move the file"
        assert "status: accepted" in original_file.read_text()

        # Update the title: {title} IS part of the file_pattern, so a naive
        # re-resolution renames the file. It must not.
        svc.update_entry("adr-x", "sw", title="A Completely Different Title")
        assert original_file.exists(), "title update must not rename the file"
        assert not (
            software_env["kb_path"] / "adrs" / "0009-a-completely-different-title.md"
        ).exists()
        content = original_file.read_text()
        assert "A Completely Different Title" in content
        assert "status: accepted" in content
    finally:
        db.close()


def test_rename_an_adr_keeps_its_file_and_survives_index_verification(software_env):
    """#391 cold read round 2 MUST: renaming an ADR's id deleted its file.
    `KBRepository.rename` re-resolved the filename after the id change;
    the `adr` pattern (`{adr_number:04d}-{title}.md`) has no `{id}`/`{slug}`,
    so the new path equaled the old one, and the unconditional
    `src.unlink()` deleted the file it had just written -- `renamed: True`,
    zero files left, and the index-verification step below then failed too
    (nothing on disk for sync_incremental to find)."""
    from pyrite.services.kb_service import KBService
    from pyrite.storage.database import PyriteDB

    db = PyriteDB(software_env["db_path"])
    try:
        svc = KBService(software_env["config"], db)
        svc.create_entry("sw", "adr-x", "Keep Me", "adr", "body", adr_number=9, status="proposed")
        original_file = software_env["kb_path"] / "adrs" / "0009-keep-me.md"
        assert original_file.exists(), original_file

        result = svc.rename_entry("adr-x", "adr-y", "sw")

        assert result["renamed"] is True, result
        assert result.get("index_verified") is True, result
        assert original_file.exists(), "the file must survive the rename"
        assert "id: adr-y" in original_file.read_text()
        assert len(list(software_env["kb_path"].rglob("*.md"))) == 1
        assert db.get_entry("adr-y", "sw") is not None
        assert db.get_entry("adr-x", "sw") is None
    finally:
        db.close()


def test_cli_import_dry_run_reports_a_duplicate_within_the_batch(env):
    """Review item 5: the dry run sees the ids earlier records would create."""
    path = env["tmp_path"] / "twins.json"
    path.write_text(
        json.dumps(
            [
                {"type": "person", "title": "Twin Dry", "body": "a"},
                {"type": "person", "title": "Twin Dry", "body": "b"},
            ]
        )
    )
    result = _cli(env, ["import", str(path), "-k", KB, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert result.output.count("Would refuse [ENTRY_EXISTS]") == 1, result.output
    assert _file(env, "twin-dry") is None


def test_a_required_only_schema_field_is_updatable_and_written(tmp_path):
    """Review item 7: `updatable_fields` and `update` agree on `required:` fields."""
    fenv = _kb_env(
        tmp_path, "fk", "generic", "name: fk\ntypes:\n  finding:\n    required: [title, owner]\n"
    )
    (fenv["kb_path"] / "f.md").write_text(
        "---\nid: f\ntitle: F\ntype: finding\nowner: alice\n---\nb\n"
    )
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(fenv["config"], tier="admin")
    try:
        server.index_mgr.index_all()
        assert "owner" in server.svc.updatable_fields("f", "fk")
        res = server._dispatch_tool("kb_update", {"kb_name": "fk", "entry_id": "f", "owner": "bob"})
        assert res.get("updated"), res
        assert "owner: bob" in (fenv["kb_path"] / "f.md").read_text()
    finally:
        server.close()


def _fake_clip(monkeypatch, title):
    from pyrite.services import clipper

    class Result:
        def __init__(self):
            self.title = title
            self.body = "clipped body"
            self.source_url = "https://example.org/a"

    async def clip_url(self, url, title=None):
        return Result()

    monkeypatch.setattr(clipper.ClipperService, "clip_url", clip_url)


def test_clipper_goes_through_the_pipeline(env, rest, monkeypatch):
    """Review item 8: the clipper maps the pipeline's codes and takes the override."""
    _fake_clip(monkeypatch, "Clipped Page")
    # `pipe` declares note, person and finding; a clip filed as `widget` is undeclared.
    base = {"url": "https://example.org/a", "kb": KB, "entry_type": "widget"}

    refused = rest.post("/api/clip", json=base)
    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"]["code"] == "UNDECLARED_TYPE"

    _stop_enforcing(env)
    ok = rest.post("/api/clip", json={**base, "allow_undeclared": True})
    assert ok.status_code == 200, ok.text

    again = rest.post("/api/clip", json={**base, "allow_undeclared": True})
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["code"] == "ENTRY_EXISTS"


def test_bulk_create_description_does_not_promise_a_single_index_sync():
    """Review item 9: each entry is indexed as it is saved."""
    from pyrite.server.tool_schemas import WRITE_TOOLS

    assert "single index sync" not in WRITE_TOOLS["kb_bulk_create"]["description"]


def test_plugin_validator_receives_documented_context_for_create_and_update(env, mcp, monkeypatch):
    """#600: validators get the schema and the original Entry on update."""
    from pyrite.plugins import get_registry
    from pyrite.plugins.capabilities import Capability
    from pyrite.schema.kb_schema import KBSchema

    calls = []

    def record_context(entry_type, fields, context):
        calls.append((fields.get("role"), dict(context)))
        return []

    class RecordingPlugin:
        name = "issue_600_validator_context"
        capabilities = {Capability.STORAGE}

        def get_kb_types(self):
            return ["generic"]

        def get_validators(self):
            return [record_context]

    registry = get_registry()
    monkeypatch.setattr(registry, "_discovered", True)
    monkeypatch.setattr(registry, "_plugins", dict(registry._plugins))
    monkeypatch.setattr(registry, "_conformance_cache", {})
    registry.register(RecordingPlugin())

    created = mcp._dispatch_tool(
        "kb_create",
        {
            "kb_name": KB,
            "entry_type": "person",
            "title": "Validator Context",
            "body": "before update",
            "role": "author",
        },
    )
    assert created.get("created"), created
    entry_id = created["entry_id"]

    updated = mcp._dispatch_tool(
        "kb_update",
        {"kb_name": KB, "entry_id": entry_id, "role": "editor"},
    )
    assert updated.get("updated"), updated

    create_contexts = [context for role, context in calls if role == "author"]
    update_contexts = [context for role, context in calls if role == "editor"]
    assert create_contexts
    assert update_contexts
    expected_keys = {"kb_name", "kb_schema", "kb_type", "_schema_version", "existing_entry"}
    assert all(set(context) == expected_keys for context in create_contexts + update_contexts)
    assert all(context["kb_name"] == KB for context in create_contexts + update_contexts)
    assert all(
        isinstance(context["kb_schema"], KBSchema) for context in create_contexts + update_contexts
    )
    assert all(context["existing_entry"] is None for context in create_contexts)

    for context in update_contexts:
        existing = context["existing_entry"]
        assert existing is not None
        assert existing.title == "Validator Context"
        assert existing.role == "author"


def _register_invalid_person_role_hook(plugin_name):
    from pyrite.plugins.registry import Capability, get_registry

    def set_invalid_role(entry, _ctx):
        entry.role = "reviewer"
        return entry

    class InvalidRoleHook:
        name = plugin_name
        capabilities = {Capability.STORAGE}

        def get_hooks(self):
            return {"before_save": [set_invalid_role]}

    registry = get_registry()
    registry.register(InvalidRoleHook())
    return registry


def test_before_save_hook_output_is_validated_on_create(env):
    from pyrite.exceptions import SchemaViolationError
    from pyrite.services.kb_service import KBService
    from pyrite.storage.database import PyriteDB

    plugin_name = "issue_601_invalid_create_role"
    registry = _register_invalid_person_role_hook(plugin_name)
    db = PyriteDB(env["db_path"])
    try:
        svc = KBService(env["config"], db)
        with pytest.raises(SchemaViolationError, match="reviewer"):
            svc.create_entry(KB, "hook-create", "Hook Create", "person", "body", role="author")
        assert _file(env, "hook-create") is None
    finally:
        del registry._plugins[plugin_name]
        db.close()


def test_before_save_hook_output_is_validated_on_update_and_preserves_file(env):
    from pyrite.exceptions import SchemaViolationError
    from pyrite.services.kb_service import KBService
    from pyrite.storage.database import PyriteDB

    plugin_name = "issue_601_invalid_update_role"
    db = PyriteDB(env["db_path"])
    registry = None
    try:
        svc = KBService(env["config"], db)
        svc.create_entry(KB, "hook-update", "Hook Update", "person", "before", role="author")
        target = _file(env, "hook-update")
        assert target is not None
        original = target.read_bytes()

        registry = _register_invalid_person_role_hook(plugin_name)
        with pytest.raises(SchemaViolationError, match="reviewer"):
            svc.update_entry("hook-update", KB, body="after")
        assert target.read_bytes() == original
    finally:
        if registry is not None:
            del registry._plugins[plugin_name]
        db.close()
