"""A KB marked read_only takes no admin-tier write either.

The write-tier tools are refused at the MCP dispatcher
(`test_mcp_write_check_refuses_read_only_kbs.py`). Of the admin-tier tools,
the ones that change anything under a KB's root are:

- `kb_manage` `add_type`, `remove_type`, `set_schema` -- rewrite `kb.yaml`;
- `kb_commit` -- creates a commit in the KB's repository;
- `kb_push` -- updates the remote and the KB's remote-tracking refs.

Each is refused for a read-only KB by the service that performs it
(`SchemaService`, `ExportService`), so the CLI and REST refuse it too. The
others -- `discover`, `validate`, `show_schema`, `kb_index_sync`,
`kb_registry_*`, `wiki_protect` -- change nothing under a KB's root.

The last case: a caller that names no KB, so the dispatcher has nothing to
check, still cannot write to a read-only default KB.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.server.mcp_server import PyriteMCPServer

RO = "frozen"
KB_YAML = "name: frozen\ntypes:\n  memo:\n    description: A memo\n"


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True, timeout=60
    ).stdout.strip()


@pytest.fixture
def env(tmp_path: Path):
    kb = tmp_path / RO
    kb.mkdir()
    (kb / "kb.yaml").write_text(KB_YAML)
    (kb / "note.md").write_text("---\ntitle: Note\ntype: note\n---\nbody\n")
    _git(kb, "init", "-q")
    _git(kb, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "add", ".")
    _git(kb, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "init")
    (kb / "note.md").write_text("---\ntitle: Note\ntype: note\n---\nchanged\n")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=RO, path=kb, kb_type="generic", read_only=True)],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    server = PyriteMCPServer(config=config, tier="admin")
    try:
        yield server, kb
    finally:
        server.close()


def _call(server, tool, args):
    return server._dispatch_tool(tool, args, client_kind="local")


@pytest.mark.parametrize(
    "args",
    [
        {"action": "add_type", "type_name": "extra", "type_def": {"description": "x"}},
        {"action": "remove_type", "type_name": "memo"},
        {"action": "set_schema", "schema": {"types": {}}},
    ],
    ids=["add_type", "remove_type", "set_schema"],
)
def test_a_schema_change_to_a_read_only_kb_is_refused(env, args):
    server, kb = env
    result = _call(server, "kb_manage", {**args, "kb_name": RO})
    assert result.get("error_code") == "KB_READ_ONLY", result
    assert (kb / "kb.yaml").read_text() == KB_YAML


def test_a_commit_in_a_read_only_kb_is_refused(env):
    server, kb = env
    head = _git(kb, "rev-parse", "HEAD")
    result = _call(server, "kb_commit", {"kb": RO, "message": "m"})
    assert result.get("error_code") == "KB_READ_ONLY", result
    assert _git(kb, "rev-parse", "HEAD") == head


def test_a_push_from_a_read_only_kb_is_refused(env):
    server, _ = env
    result = _call(server, "kb_push", {"kb": RO})
    assert result.get("error_code") == "KB_READ_ONLY", result


@pytest.mark.control(reason="reading a read-only KB's schema was allowed before and after")
def test_reading_the_schema_of_a_read_only_kb_is_allowed(env):
    server, _ = env
    result = _call(server, "kb_manage", {"action": "show_schema", "kb_name": RO})
    assert "memo" in result.get("types", {}), result


def test_an_unscoped_write_to_a_read_only_default_kb_is_refused(tmp_path):
    # investigation_bulk_edges resolves the investigation KB when none is
    # named, so the dispatcher sees no KB to check; the plugin's own
    # writable-KB check, before KBService, is the guard on this path.
    kb = tmp_path / "inv"
    kb.mkdir()
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name="inv", path=kb, kb_type="journalism-investigation", read_only=True)
        ],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    server = PyriteMCPServer(config=config, tier="write")
    try:
        edges = [{"type": "ownership", "fields": {"owner": "[[a]]", "asset": "[[b]]"}}]
        result = server._dispatch_tool(
            "investigation_bulk_edges", {"edges": edges}, client_kind="operator_key"
        )
        rows = server.db._raw_conn.execute("SELECT id FROM entry WHERE kb_name = 'inv'").fetchall()
    finally:
        server.close()
    assert result.get("error_code") == "KB_READ_ONLY", result
    assert rows == [] and list(kb.rglob("*.md")) == []


# `pyrite schema migrate` rewrites entry files under the KB's root. It came
# after the read-only rule was first written (#955), so it is checked here
# through the command an operator runs: a read-only KB keeps every byte.

_THING_KB_YAML = "name: frozen\nkb_type: generic\ntypes:\n  thing:\n    version: 2\n"
_THING = "---\nid: t\ntitle: T\ntype: thing\nold: 1\n---\n\nBody.\n"


@pytest.fixture
def thing_migration():
    from pyrite.migrations import Migration, get_migration_registry

    registry = get_migration_registry()

    def rename_old(data: dict) -> dict:
        data = dict(data)
        data["new"] = data.pop("old")
        return data

    registry.add(Migration("thing", 0, 2, rename_old, "old -> new"))
    try:
        yield
    finally:
        registry._migrations.pop("thing", None)


def test_a_schema_migration_of_a_read_only_kb_is_refused(tmp_path, thing_migration):
    from tests._world import build_world

    kb = tmp_path / RO
    (kb / "things").mkdir(parents=True)
    (kb / "kb.yaml").write_text(_THING_KB_YAML)
    entry = kb / "things" / "t.md"
    entry.write_text(_THING)
    world = build_world(
        tmp_path / "world",
        kbs=[KBConfig(name=RO, path=kb, kb_type="generic", read_only=True)],
        rest=False,
    )
    try:
        result = world.cli("schema", "migrate", "--kb", RO)
    finally:
        world.close()
    assert result.exit_code == 1, result.output
    assert "READ_ONLY" in result.output, result.output
    assert entry.read_text() == _THING
