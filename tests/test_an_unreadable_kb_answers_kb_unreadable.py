"""A KB whose files cannot be read is never read as empty or schema-less (#1542).

Two ways a registered KB's files cannot be read: its folder cannot be
searched (mode 600), or its ``kb.yaml`` cannot be opened (mode 000). Either
way, every operation that needs that KB's files answers one refusal on every
surface and verb: code ``KB_UNREADABLE``, ``retryable: false``, and one fixed
message that names no path and no OS text. Nothing derived from its files
(its index rows) is removed, and every other KB still works.

The servers start with the KB already unreadable (MCP and the REST app are
built from a config loaded after the chmod), as a restart would.

Medium: real files and modes, the real ``load_config``, the CLI in-process,
``PyriteMCPServer._dispatch_tool``, the app as ``pyrite serve`` builds it
under a ``TestClient`` with its lifespan. Nothing mocks the filesystem.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

import pytest

from pyrite.config import KBConfig, load_config
from tests._world import _create_app_without_static, build_world

ENTRY = "---\nid: x\ntype: note\ntitle: X\n---\nBody\n"


def _lock(case: str, locked: Path) -> Path:
    """Make ``locked`` unreadable the ``case`` way; return what to restore."""
    target = locked if case == "unsearchable-root" else locked / "kb.yaml"
    target.chmod(0o600 if case == "unsearchable-root" else 0o000)
    stopped = (
        not os.access(locked, os.X_OK)
        if case == "unsearchable-root"
        else not os.access(target, os.R_OK)
    )
    if not stopped:
        target.chmod(0o755 if target.is_dir() else 0o644)
        pytest.skip(
            f"running as a user (uid {os.getuid()}) whom mode "
            f"{'600' if case == 'unsearchable-root' else '000'} does not stop: "
            "the unreadable-KB case is not entered"
        )
    return target


@pytest.fixture(params=["unsearchable-root", "unreadable-kb-yaml"])
def unreadable(request, tmp_path):
    """Two KBs, both indexed; then ``locked`` is made unreadable."""
    good, locked = tmp_path / "good", tmp_path / "locked"
    good.mkdir()
    locked.mkdir()
    (locked / "kb.yaml").write_text("name: locked\ntypes: {}\n", encoding="utf-8")
    (locked / "x.md").write_text(ENTRY, encoding="utf-8")
    (good / "g.md").write_text(ENTRY.replace("id: x", "id: g"), encoding="utf-8")
    world = build_world(
        tmp_path / "w",
        kbs=[
            KBConfig(name="good", path=good, kb_type="generic"),
            KBConfig(name="locked", path=locked, kb_type="generic"),
        ],
        rest=False,
    )
    target = _lock(request.param, locked)
    try:
        yield world, locked
    finally:
        target.chmod(0o755 if target.is_dir() else 0o644)
        world.close()


@contextlib.contextmanager
def _servers(world):
    """MCP and REST started now, from the config as it loads now."""
    from starlette.testclient import TestClient

    with world.pointed():
        config = load_config()
    from pyrite.server.mcp_server import PyriteMCPServer

    mcp = PyriteMCPServer(config=config, tier="admin")
    app = _create_app_without_static(config, world.root)
    client = TestClient(app)
    client.__enter__()
    try:
        yield config, mcp, client
    finally:
        client.__exit__(None, None, None)
        mcp.close()
        state_db = getattr(app.state, "pyrite_db", None)
        if state_db is not None:
            state_db.close()


def _rows(world, kb):
    return sorted(
        r["id"] for r in world.db.execute_sql("SELECT id FROM entry WHERE kb_name = :k", {"k": kb})
    )


def test_every_surface_and_verb_answers_one_refusal(unreadable):
    world, locked = unreadable
    answers: dict[str, tuple] = {}

    cli = {
        "get": ["get", "x", "-k", "locked", "--format", "json"],
        "create": ["create", "-k", "locked", "-t", "note", "--title", "New", "--format", "json"],
        "update": ["update", "x", "-k", "locked", "--title", "T", "--format", "json"],
        "delete": ["delete", "x", "-k", "locked", "--force", "--format", "json"],
        "schema show": ["kb", "schema", "show", "locked", "--format", "json"],
    }
    for verb, args in cli.items():
        result = world.cli(*args)
        assert result.exit_code == 1, (verb, result.output)
        body = json.loads(result.output.strip().splitlines()[-1])
        answers[f"cli {verb}"] = (body["error_code"], body["error"], body["retryable"])

    with _servers(world) as (_config, mcp, client):
        tools = {
            "get": ("kb_get", {"entry_id": "x"}),
            "create": ("kb_create", {"entry_type": "note", "title": "New"}),
            "update": ("kb_update", {"entry_id": "x", "title": "T"}),
            "delete": ("kb_delete", {"entry_id": "x"}),
        }
        for verb, (tool, args) in tools.items():
            res = mcp._dispatch_tool(tool, {"kb_name": "locked", **args})
            answers[f"mcp {verb}"] = (res.get("error_code"), res.get("error"), res.get("retryable"))

        calls = {
            "get": lambda: client.get("/api/entries/x", params={"kb": "locked"}),
            "create": lambda: client.post(
                "/api/entries", json={"kb": "locked", "entry_type": "note", "title": "New"}
            ),
            "update": lambda: client.put("/api/entries/x", json={"kb": "locked", "title": "T"}),
            "delete": lambda: client.delete("/api/entries/x", params={"kb": "locked"}),
        }
        for verb, call in calls.items():
            resp = call()
            assert resp.status_code == 500, (verb, resp.text)
            detail = resp.json()["detail"]
            answers[f"rest {verb}"] = (detail["code"], detail["message"], detail["retryable"])

        # The other KB is served by the same servers.
        assert "entry" in mcp._dispatch_tool("kb_get", {"kb_name": "good", "entry_id": "g"})
        assert client.get("/api/entries/g", params={"kb": "good"}).status_code == 200

    assert len(set(answers.values())) == 1, answers
    code, message, retryable = next(iter(answers.values()))
    assert (code, retryable) == ("KB_UNREADABLE", False)
    assert str(locked) not in message and "Permission denied" not in message
    assert "Errno" not in message
    assert _rows(world, "locked") == ["x"]  # nothing deleted from the index


def test_a_sync_of_every_kb_keeps_the_unreadable_kbs_rows_and_names_it(unreadable):
    world, locked = unreadable
    (world.kb_path("good") / "h.md").write_text(ENTRY.replace("id: x", "id: h"), encoding="utf-8")

    result = world.cli("index", "sync", "--no-embed")

    assert result.exit_code == 3, result.output  # partial: good synced, locked not
    assert "Not synced: locked" in result.output
    assert "Removed: 0" in result.output
    assert _rows(world, "locked") == ["x"]
    assert _rows(world, "good") == ["g", "h"]


def test_the_sync_result_names_the_unreadable_kb_on_every_surface(unreadable):
    world, locked = unreadable
    from pyrite.storage.index import IndexManager

    with world.pointed():
        config = load_config()
    results = IndexManager(world.db, config).sync_incremental()
    assert results["removed"] == 0
    assert [row["kb"] for row in results["unreadable_kbs"]] == ["locked"]

    with _servers(world) as (_config, mcp, client):
        res = mcp._dispatch_tool("kb_index_sync", {})
        assert [row["kb"] for row in res["unreadable_kbs"]] == ["locked"], res
        one = mcp._dispatch_tool("kb_index_sync", {"kb_name": "locked"})
        assert (one["error_code"], one["retryable"]) == ("KB_UNREADABLE", False), one
        resp = client.post("/api/index/sync", params={"wait": "true"})
        assert resp.status_code == 200, resp.text
        assert [row["kb"] for row in resp.json()["unreadable_kbs"]] == ["locked"]
    assert _rows(world, "locked") == ["x"]


def test_a_sync_or_rebuild_of_the_unreadable_kb_alone_changes_nothing(unreadable):
    world, locked = unreadable

    one = world.cli("index", "sync", "-k", "locked", "--no-embed")
    assert one.exit_code == 1, one.output
    assert "KB_UNREADABLE" in one.output or "could not read" in one.output

    build = world.cli("index", "build", "--no-embed")
    assert build.exit_code == 3, build.output
    assert "locked" in build.output
    assert _rows(world, "locked") == ["x"]
    assert _rows(world, "good") == ["g"]
