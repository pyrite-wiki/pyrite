"""An update is refused only for a violation it causes (#676; ADR-0042 decision 8).

The property: a violation that was in the file before and is still there, in a
field the request does not name, comes back as a warning with the write's
result. A violation the write introduces -- or a bad value the request sets --
is refused. Create stays strict. The grid is surface x violation kind; the
oracle is the effect (accepted with a warning naming the field, the file's
bytes unchanged but for the request), never the message text.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from typer.testing import CliRunner

from pyrite.cli import app as cli_app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import SchemaViolationError
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

runner = CliRunner()
KB = "ro"

KB_YAML = """\
name: ro
types:
  note:
    required: [title]
    subdirectory: notes/
    fields:
      lanes: {type: multi-select, options: [a, b]}
      score: {type: number, min: 0, max: 10}
  event:
    required: [title, date]
    subdirectory: events/
validation:
  enforce: true
"""

#: kind -> (entry id, file text, the violated field)
CONTROL = pytest.mark.control(reason="already true before the change: the rule must keep holding")

ON_DISK = {
    "multi-select holds a scalar": (
        "typed3",
        "---\nid: typed3\ntitle: Typed3\ntype: note\nlanes: a\n---\n\nb\n",
        "lanes",
    ),
    "number out of range": (
        "scored",
        "---\nid: scored\ntitle: Scored\ntype: note\nscore: 99\n---\n\nb\n",
        "score",
    ),
    "required field missing": (
        "ev2",
        "---\nid: ev2\ntitle: Ev2\ntype: event\n---\n\nno date\n",
        "date",
    ),
    "off-list enum (control)": (
        "offlist",
        "---\nid: offlist\ntitle: Offlist\ntype: note\nlanes: [zz]\n---\n\nb\n",
        "lanes",
    ),
}
SURFACES = ["cli", "mcp", "rest", "in-process"]


@pytest.fixture
def env(tmp_path):
    kb_path = tmp_path / KB
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text(KB_YAML)
    for entry_id, text, _ in ON_DISK.values():
        sub = "events" if entry_id == "ev2" else "notes"
        (kb_path / sub).mkdir(exist_ok=True)
        (kb_path / sub / f"{entry_id}.md").write_text(text)
    db_path = tmp_path / "index.db"
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=db_path, auto_embed=False),
    )
    db = PyriteDB(db_path)
    IndexManager(db, config).index_all()
    db.close()
    return {"config": config, "kb_path": kb_path, "db_path": db_path, "tmp_path": tmp_path}


def _file(env, entry_id):
    (found,) = env["kb_path"].rglob(f"{entry_id}.md")
    return found


def _request(env, surface, entry_id, fields):
    """Send ``fields`` as an update through ``surface``; return ``(ok, warnings, text)``."""
    config = env["config"]
    if surface == "cli":
        args = ["update", entry_id, "-k", KB, "--format", "json"]
        for k, v in fields.items():
            args += ["--tags", ",".join(v)] if k == "tags" else ["-f", f"{k}={v}"]
        with (
            patch("pyrite.cli.load_config", return_value=config),
            patch("pyrite.cli.context.load_config", return_value=config),
        ):
            res = runner.invoke(cli_app, args)
        text = res.output
        try:
            body = json.loads(text[text.index("{") :])
        except ValueError:
            body = {}
        return res.exit_code == 0, body.get("warnings", []), text
    if surface == "mcp":
        from pyrite.server.mcp_server import PyriteMCPServer

        server = PyriteMCPServer(config, tier="admin")
        try:
            res = server._dispatch_tool(
                "kb_update", {"kb_name": KB, "entry_id": entry_id, **fields}
            )
        finally:
            server.close()
        return bool(res.get("updated")), res.get("warnings", []), json.dumps(res)
    if surface == "rest":
        from starlette.testclient import TestClient

        from pyrite.server.api import create_app, get_config, get_db, get_index_worker
        from pyrite.services.index_worker import IndexWorker

        app = create_app(config=config)
        db = PyriteDB(env["db_path"])
        worker = IndexWorker(db, config)
        app.dependency_overrides[get_config] = lambda: config
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_index_worker] = lambda: worker
        try:
            ((field, value),) = fields.items()  # PATCH sets one field
            resp = TestClient(app).patch(
                f"/api/entries/{entry_id}", json={"kb": KB, "field": field, "value": value}
            )
            body = resp.json() if resp.status_code == 200 else {}
            return resp.status_code == 200, body.get("warnings", []), resp.text
        finally:
            worker.wait_for_idle(timeout=10)
            db.close()
    db = PyriteDB(env["db_path"])
    try:
        svc = KBService(config, db)
        try:
            svc.update_entry(entry_id, KB, **fields)
        except SchemaViolationError as e:
            return False, [], str(e)
        return True, None, ""  # update_entry answers the entry, not the warnings
    finally:
        db.close()


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize(
    "kind",
    [pytest.param(k, marks=CONTROL) if "control" in k else k for k in ON_DISK],
)
def test_an_update_that_does_not_name_the_bad_field_is_accepted_with_a_warning(env, surface, kind):
    entry_id, _text, field = ON_DISK[kind]
    before = _file(env, entry_id).read_text()
    ok, warnings, text = _request(env, surface, entry_id, {"tags": ["x"]})
    assert ok, text
    if surface != "in-process":
        assert any(w.get("field") == field for w in warnings), (warnings, text)
        assert any("already on disk" in json.dumps(w) for w in warnings), warnings
    after = _file(env, entry_id).read_text()
    # The bad value is byte-identical; only the requested tag was added.
    for line in before.splitlines():
        if line.startswith(("lanes:", "score:")):
            assert line in after.splitlines(), after
    assert "x" in after


@pytest.mark.parametrize("kind", ["multi-select holds a scalar", "required field missing"])
def test_an_echo_of_kb_get_is_accepted_with_the_warning(env, kind):
    entry_id, _text, field = ON_DISK[kind]
    ok, _w, _t = _request(env, "mcp", entry_id, {"tags": ["seed"]})
    assert ok
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(env["config"], tier="admin")
    try:
        got = server._dispatch_tool("kb_get", {"kb_name": KB, "entry_id": entry_id})["entry"]
        res = server._dispatch_tool(
            "kb_update", {**got, "kb_name": KB, "entry_id": entry_id, "body": "edited"}
        )
    finally:
        server.close()
    assert res.get("updated"), res
    assert any(w.get("field") == field for w in res.get("warnings", [])), res


@CONTROL
def test_a_request_that_sets_the_bad_field_to_another_bad_value_is_refused(env):
    before = _file(env, "scored").read_bytes()
    ok, _w, text = _request(env, "mcp", "scored", {"score": 98})
    assert not ok and "score" in text, text
    assert _file(env, "scored").read_bytes() == before


def test_a_request_that_fixes_one_of_two_violations_is_accepted_with_the_other_warned(env):
    (env["kb_path"] / "notes" / "both.md").write_text(
        "---\nid: both\ntitle: Both\ntype: note\nlanes: a\nscore: 99\n---\n\nb\n"
    )
    db = PyriteDB(env["db_path"])
    IndexManager(db, env["config"]).index_all()
    db.close()
    ok, warnings, text = _request(env, "mcp", "both", {"score": 5})
    assert ok, text
    assert [w["field"] for w in warnings if "already on disk" in json.dumps(w)] == ["lanes"]


def test_a_request_that_changes_nothing_is_not_refused_for_what_is_on_disk(env):
    before = _file(env, "scored").read_bytes()
    ok, warnings, text = _request(env, "mcp", "scored", {"title": "Scored"})
    assert ok, text
    assert any(w.get("field") == "score" for w in warnings), warnings
    assert _file(env, "scored").read_bytes() == before


@CONTROL
def test_create_with_the_same_values_is_still_refused(env):
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(env["config"], tier="admin")
    try:
        res = server._dispatch_tool(
            "kb_create", {"kb_name": KB, "entry_type": "note", "title": "New", "score": 99}
        )
    finally:
        server.close()
    assert not res.get("created"), res


@CONTROL
def test_a_violation_the_write_introduces_in_an_unnamed_field_is_refused(tmp_path):
    """Cross-field: setting `transaction_type: payment` makes `amount` required.
    The request does not name `amount`, but the finding is new, so it refuses."""
    pytest.importorskip("pyrite_journalism_investigation")
    kb_path = tmp_path / "j"
    (kb_path / "transactions").mkdir(parents=True)
    (kb_path / "kb.yaml").write_text("name: j\n")
    (kb_path / "transactions" / "t1.md").write_text(
        "---\nid: t1\ntitle: T1\ntype: transaction\ndate: 2024-01-01\n"
        "transaction_type: transfer\nsender: a\nreceiver: b\n---\n\nx\n"
    )
    db_path = tmp_path / "index.db"
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="j", path=kb_path, kb_type="journalism-investigation")],
        settings=Settings(index_path=db_path, auto_embed=False),
    )
    db = PyriteDB(db_path)
    IndexManager(db, config).index_all()
    try:
        svc = KBService(config, db)
        before = (kb_path / "transactions" / "t1.md").read_bytes()
        with pytest.raises(SchemaViolationError, match="amount"):
            svc.update("t1", "j", {"transaction_type": "payment"})
        assert (kb_path / "transactions" / "t1.md").read_bytes() == before
    finally:
        db.close()


def test_the_update_path_has_no_second_schema_check(env):
    """`Entry.validate()` runs on create only; an update that called it would
    refuse a violation already on disk behind `_validate_write`'s back."""
    from pyrite.models.base import Entry

    with patch.object(Entry, "validate", side_effect=AssertionError("validate on update")):
        ok, _w, text = _request(env, "mcp", "ev2", {"tags": ["x"]})
    assert ok, text
