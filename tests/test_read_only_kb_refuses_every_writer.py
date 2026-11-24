"""No write reaches a file in a KB marked ``read_only``, whatever writes it.

`KBService` refuses every entry write to a read-only KB, and the MCP
dispatcher refuses a tool above the read tier that names one. These are the
writers that reach a KB's files another way, each driven through the command
or route an operator uses:

- ``pyrite ids pin`` (adds an ``id:`` line to each file without one);
- ``pyrite sw migrate-standards --apply`` (retypes and moves files);
- ``pyrite readme --write`` (writes the KB's ``README.md``);
- the admin merge of a user's change set (``git merge`` into the KB's repo);
- an admin-tier MCP tool, core or plugin, that names the KB.

Each leaves every byte of the KB as it was. The dispatcher's own check
fails closed: a KB it cannot look up is refused, never let through.
"""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import pytest
import yaml

from pyrite.config import AuthConfig, KBConfig
from tests._world import build_world

RO = "frozen"


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.parts
    }


def _world(tmp_path, *, kb_type="generic", kb_yaml=None, files=()):
    root = tmp_path / RO
    root.mkdir()
    if kb_yaml is not None:
        (root / "kb.yaml").write_text(kb_yaml)
    world = build_world(
        tmp_path / "world",
        kbs=[KBConfig(name=RO, path=root, kb_type=kb_type, read_only=True)],
        rest=False,
    )
    for rel, text in files:
        world.add_entry(RO, text, filename=rel)
    return world, root


def test_ids_pin_is_refused(tmp_path):
    world, root = _world(tmp_path, files=[("notes/a.md", "---\ntitle: A\ntype: note\n---\nbody\n")])
    before = _tree(root)
    try:
        result = world.cli("ids", "pin", "-k", RO)
    finally:
        world.close()
    assert result.exit_code == 1, result.output
    assert "READ_ONLY" in result.output, result.output
    assert _tree(root) == before


@pytest.mark.control(reason="a dry run never wrote, read-only or not")
def test_ids_pin_dry_run_still_plans(tmp_path):
    world, root = _world(tmp_path, files=[("notes/a.md", "---\ntitle: A\ntype: note\n---\nbody\n")])
    try:
        result = world.cli("ids", "pin", "-k", RO, "--dry-run")
    finally:
        world.close()
    assert result.exit_code in (0, 3), result.output


def test_sw_migrate_standards_leaves_a_read_only_kb_alone(tmp_path):
    from pyrite_software_kb.preset import SOFTWARE_KB_PRESET

    world, root = _world(
        tmp_path,
        kb_type="software",
        kb_yaml=yaml.safe_dump(copy.deepcopy(SOFTWARE_KB_PRESET)),
        files=[
            (
                "standards/s.md",
                "---\nid: s\ntype: standard\ntitle: S\nenforced: true\n---\nRule.\n",
            )
        ],
    )
    before = _tree(root)
    try:
        result = world.cli("sw", "migrate-standards", "--apply", "-k", RO)
    finally:
        world.close()
    assert "read-only" in result.output, result.output
    assert "Migrated" not in result.output, result.output
    assert _tree(root) == before


def test_readme_write_is_refused(tmp_path):
    world, root = _world(tmp_path)
    before = _tree(root)
    try:
        result = world.cli("readme", "-k", RO, "--write")
    finally:
        world.close()
    assert result.exit_code == 1, result.output
    assert "READ_ONLY" in result.output, result.output
    assert _tree(root) == before


# -- the admin merge of a change set -----------------------------------------


@pytest.fixture
def merge_world(tmp_path, monkeypatch):
    from pyrite.services.worktree_service import WorktreeService
    from tests.auth_seed import seed_and_sign_in, seed_user
    from tests.test_worktree_service import _init_git_repo

    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-config"))
    repo = tmp_path / "repo"
    kb = repo / "kb"
    kb.mkdir(parents=True)
    (kb / "kb.yaml").write_text("name: test-kb\ntypes: {}\n", encoding="utf-8")
    _init_git_repo(repo)
    world = build_world(
        tmp_path / "world",
        kbs=[KBConfig(name="test-kb", path=kb, kb_type="generic")],
        settings={"auth": AuthConfig(enabled=True)},
    )
    try:
        seed_user(world.db, "operator", role="admin")
        user = seed_and_sign_in(world.client, "alice", role="write")
        wt = WorktreeService(world.config, world.db).ensure_worktree("test-kb", user["id"], "alice")
        yield world, repo, wt
    finally:
        world.close()


def test_the_admin_merge_into_a_read_only_kb_is_refused(merge_world):
    from tests.auth_seed import SESSION_COOKIE, sign_in

    world, repo, wt = merge_world
    edited = Path(wt.worktree_path) / "kb" / "alice-note.md"
    edited.write_text("---\nid: alice-note\ntitle: A\ntype: note\n---\nEdit\n", encoding="utf-8")
    assert world.client.post("/api/worktree/submit", json={"kb": "test-kb"}).status_code == 200
    # The operator marks the KB read-only while the change set waits.
    world.config.get_kb("test-kb").read_only = True
    head = subprocess.run(
        ["git", "rev-parse", "main"], cwd=repo, capture_output=True, text=True, timeout=30
    ).stdout
    world.client.cookies.set(SESSION_COOKIE, sign_in(world.app, "operator"))
    merged = world.client.post("/api/admin/merge-queue/alice/merge", json={"kb": "test-kb"})
    assert merged.status_code == 403, merged.text
    assert not (repo / "kb" / "alice-note.md").exists()
    after = subprocess.run(
        ["git", "rev-parse", "main"], cwd=repo, capture_output=True, text=True, timeout=30
    ).stdout
    assert after == head


# -- the MCP dispatcher ------------------------------------------------------


@pytest.fixture
def server_with_an_admin_writer(tmp_path, monkeypatch):
    """An admin-tier server with one plugin admin tool that writes a file
    into the KB it names, with no check of its own."""
    from pyrite.plugins import get_registry

    reached: list[str] = []

    class AdminWriter:
        name = "admin_writer_plugin"
        capabilities: set = set()

        def get_mcp_tools(self, tier):
            if tier != "admin":
                return {}

            def handler(args):
                reached.append(args["kb_name"])
                root = world.config.get_kb(args["kb_name"]).path
                (root / "written.md").write_text("---\ntitle: W\n---\n")
                return {"ok": True}

            return {
                "admin_write_file": {
                    "description": "writes a file",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"kb_name": {"type": "string"}},
                        "required": ["kb_name"],
                    },
                    "handler": handler,
                }
            }

    registry = get_registry()
    registry.discover()
    monkeypatch.setitem(registry._plugins, "admin_writer_plugin", AdminWriter())
    world, root = _world(tmp_path)
    try:
        yield world.mcp("admin"), root, reached
    finally:
        world.close()


def test_an_admin_tier_tool_naming_a_read_only_kb_is_refused(server_with_an_admin_writer):
    server, root, reached = server_with_an_admin_writer
    before = _tree(root)
    result = server._dispatch_tool("admin_write_file", {"kb_name": RO})
    assert result.get("error_code") == "KB_READ_ONLY", result
    assert reached == []
    assert _tree(root) == before


@pytest.mark.control(reason="reading and indexing a read-only KB was always allowed")
@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("kb_index_sync", {"kb_name": RO}),
        ("kb_manage", {"kb_name": RO, "action": "show_schema"}),
        ("kb_manage", {"kb_name": RO, "action": "validate"}),
    ],
)
def test_admin_tools_that_write_no_kb_file_still_serve_a_read_only_kb(
    server_with_an_admin_writer, tool, args
):
    server, _, _ = server_with_an_admin_writer
    result = server._dispatch_tool(tool, args)
    assert result.get("error_code") not in ("KB_READ_ONLY", "READ_ONLY"), result


def test_a_kb_the_check_cannot_look_up_is_refused_not_let_through(server_with_an_admin_writer):
    """The read-only check fails closed: if looking the KB up fails, the
    call is refused and the handler never runs."""
    from pyrite.exceptions import StorageError
    from pyrite.services.kb_service import KBService

    server, root, reached = server_with_an_admin_writer
    real = server.svc

    class LookupFails(KBService):
        def require_kb(self, name):
            raise StorageError("the index could not be read")

    server.svc = LookupFails(real.config, real.db)
    server.svc.resolve_kb = real.resolve_kb  # the resolver still answers
    try:
        result = server._dispatch_tool("admin_write_file", {"kb_name": RO})
    finally:
        server.svc = real
    assert "error_code" in result, result
    assert reached == []
