"""MCP write tools enforce the per-KB write rule REST enforces.

Over REST, a write names its KB and `requires_kb_tier("write")` checks the
caller's *effective* role on it (explicit grant -> KB default_role -> global
role). Over MCP the caller's global role only chose which tools were
registered; nothing checked the per-KB role, so a session user with global
role `write` could create, update and delete entries in a KB where their
effective role is `read`.

The rule is not reimplemented here: `api.kbs_for_user_at_tier` walks the KBs
through `api.effective_kb_role_for_user`, the same resolution REST's
`resolve_effective_kb_role` uses, and `mcp_routes._resolve_bearer_auth`
resolves a connection's writable set with it. `_dispatch_tool` -- the one
chokepoint every tool passes -- refuses a write-tier tool unless every KB it
names is writable.

`TestEveryWriteToolIsChecked` is the structural half: it walks every tool
registered above the read tier, core and plugin, and fails if one reaches its
handler for a KB the caller may only read.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.mcp_server import KB_ARGUMENT_NAMES, PyriteMCPServer
from pyrite.services.auth_service import AuthService
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from tests.auth_seed import seed_user
from pyrite.services.access_policy import UNSCOPED

PUB, TEAM = "pub", "team"


@pytest.fixture
def env(tmp_path: Path):
    for name in (PUB, TEAM):
        (tmp_path / name).mkdir()
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name=PUB, path=tmp_path / PUB, kb_type="generic", default_role="read"),
            KBConfig(name=TEAM, path=tmp_path / TEAM, kb_type="generic", default_role="read"),
        ],
        settings=Settings(
            index_path=tmp_path / "index.db",
            auth=AuthConfig(enabled=True, allow_registration=True),
        ),
    )
    db = PyriteDB(config.settings.index_path)
    svc = KBService(config, db)
    svc.create_entry(PUB, "pub-note", "Pub note", "note", "original body")
    svc.create_entry(TEAM, "team-note", "Team note", "note", "team body")

    auth = AuthService(db, config.settings.auth)
    # Seeded via the operator path: admin is the first
    # user; writer and granted get global "write" (covering every KB, as a
    # registrant used to), matching what this test's assertions rely on.
    seed_user(db, "admin-user", "password123", role="admin")  # first user: admin
    seed_user(db, "writer", "password123", role="write")  # global write, per-KB read everywhere
    seed_user(db, "granted", "password123", role="write")  # global write, per-KB write on TEAM
    users = {u["username"]: u for u in auth.list_users()}
    auth.grant_kb_permission(users["granted"]["id"], TEAM, "write", users["admin-user"]["id"])
    users = {u["username"]: u for u in auth.list_users()}

    cache: dict[str, PyriteMCPServer] = {}

    def server(tier: str) -> PyriteMCPServer:
        if tier not in cache:
            cache[tier] = PyriteMCPServer(config=config, tier=tier)
        return cache[tier]

    try:
        yield {
            "config": config,
            "db": db,
            "svc": svc,
            "auth": auth,
            "users": users,
            "server": server,
        }
    finally:
        for s in cache.values():
            s.close()
        db.close()


def _connection_ctx(env, username: str) -> dict:
    """Resolve the caller exactly as `mcp_routes.handle_sse` does: a bearer
    session token through `_resolve_bearer_auth`."""
    from starlette.requests import Request

    from pyrite.server.mcp_routes import _resolve_bearer_auth

    _, token = env["auth"].login(username, "password123")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/mcp/sse",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "query_string": b"",
        }
    )
    return _resolve_bearer_auth(request, env["config"], env["db"])


def _session_call(env, username: str, tool: str, arguments: dict) -> dict:
    """One real MCP `tools/call` over the SDK's in-memory transport, with the
    connection scope `handle_sse` would hand `build_sdk_server`."""
    from mcp.shared.memory import create_connected_server_and_client_session

    ctx = _connection_ctx(env, username)
    tier = ctx["role"]
    sdk = env["server"](tier).build_sdk_server(
        client_id=username,
        readable_kbs=ctx["readable_kbs"],
        writable_kbs=ctx["writable_kbs"],
    )

    async def _run():
        async with create_connected_server_and_client_session(sdk) as session:
            result = await session.call_tool(tool, arguments)
            return json.loads(result.content[0].text)

    return asyncio.run(_run())


def _body(env, kb: str, entry_id: str) -> str | None:
    entry = env["svc"].get_entry(entry_id, kb_name=kb, readable_kbs=UNSCOPED)
    return entry.get("body") if entry else None


# ---------------------------------------------------------------------------
# The resolver: the writable set comes from the shared per-KB rule
# ---------------------------------------------------------------------------


class TestConnectionScope:
    def test_read_only_user_has_no_writable_kb(self, env):
        ctx = _connection_ctx(env, "writer")
        assert ctx["readable_kbs"] == {PUB, TEAM}
        assert ctx["writable_kbs"] == set()

    def test_granted_user_can_write_only_the_granted_kb(self, env):
        assert _connection_ctx(env, "granted")["writable_kbs"] == {TEAM}

    def test_global_admin_is_unscoped(self, env):
        ctx = _connection_ctx(env, "admin-user")
        assert ctx["readable_kbs"] is None and ctx["writable_kbs"] is None


# ---------------------------------------------------------------------------
# Direct: create, update, delete over a real session
# ---------------------------------------------------------------------------


class TestReadOnlyKBRefusesWrites:
    def test_kb_create_refused(self, env):
        out = _session_call(
            env,
            "writer",
            "kb_create",
            {"kb_name": PUB, "entry_type": "note", "title": "Planted", "body": "x"},
        )
        assert out.get("error_code") == "FORBIDDEN", out
        assert env["svc"].get_entry("planted", kb_name=PUB, readable_kbs=UNSCOPED) is None

    def test_kb_update_refused(self, env):
        out = _session_call(
            env,
            "writer",
            "kb_update",
            {"kb_name": PUB, "entry_id": "pub-note", "body": "overwritten"},
        )
        assert out.get("error_code") == "FORBIDDEN", out
        assert _body(env, PUB, "pub-note") == "original body"

    def test_kb_delete_refused(self, env):
        out = _session_call(env, "writer", "kb_delete", {"kb_name": PUB, "entry_id": "pub-note"})
        assert out.get("error_code") == "FORBIDDEN", out
        assert _body(env, PUB, "pub-note") == "original body"

    def test_naming_a_writable_kb_alongside_does_not_help(self, env):
        # kb_link names two KBs; both must be writable.
        out = _session_call(
            env,
            "granted",
            "kb_link",
            {
                "source_id": "pub-note",
                "source_kb": PUB,
                "target_id": "team-note",
                "target_kb": TEAM,
            },
        )
        assert out.get("error_code") == "FORBIDDEN", out


class TestPermittedWritersStillWrite:
    def test_granted_user_creates_updates_deletes_in_granted_kb(self, env):
        out = _session_call(
            env,
            "granted",
            "kb_create",
            {"kb_name": TEAM, "entry_type": "note", "title": "Fresh", "body": "new"},
        )
        assert "error" not in out, out
        out = _session_call(
            env, "granted", "kb_update", {"kb_name": TEAM, "entry_id": "team-note", "body": "edit"}
        )
        assert "error" not in out, out
        assert _body(env, TEAM, "team-note") == "edit"
        out = _session_call(env, "granted", "kb_delete", {"kb_name": TEAM, "entry_id": "team-note"})
        assert "error" not in out, out

    def test_granted_user_still_refused_on_other_kb(self, env):
        out = _session_call(
            env, "granted", "kb_update", {"kb_name": PUB, "entry_id": "pub-note", "body": "x"}
        )
        assert out.get("error_code") == "FORBIDDEN", out

    def test_operator_api_key_is_unaffected(self, env):
        # Unscoped: an operator key resolves to readable/writable None.
        out = env["server"]("write")._dispatch_tool(
            "kb_update",
            {"kb_name": PUB, "entry_id": "pub-note", "body": "operator edit"},
            readable_kbs=None,
            writable_kbs=None,
        )
        assert "error" not in out, out
        assert _body(env, PUB, "pub-note") == "operator edit"

    def test_read_tools_still_work_for_read_only_user(self, env):
        out = _session_call(env, "writer", "kb_get", {"kb_name": PUB, "entry_id": "pub-note"})
        assert "error" not in out, out


# ---------------------------------------------------------------------------
# Structural: every tool above the read tier, core and plugin
# ---------------------------------------------------------------------------


def _tools_above_read(env) -> dict[str, str]:
    """Every tool registered only at write or admin tier, from registration
    itself (present at a higher tier's server, absent at the lower's)."""
    read = set(env["server"]("read").tools)
    write = set(env["server"]("write").tools)
    admin = set(env["server"]("admin").tools)
    out = dict.fromkeys(write - read, "write")
    out.update(dict.fromkeys(admin - write, "admin"))
    return out


def _args_naming(tool_schema: dict, kb: str) -> dict:
    props = tool_schema.get("inputSchema", {}).get("properties", {})
    return {p: ([kb] if p == "kb_names" else kb) for p in props if p in KB_ARGUMENT_NAMES}


# Tools that write nothing, named here so the reverse walk covers them even
# if a plugin registers them above the read tier (as the journalism plugin
# once did).
KNOWN_READ_ONLY_TOOLS = frozenset({"investigation_search_all", "investigation_status"})


class TestEveryWriteToolIsChecked:
    def test_inventory_is_nonempty_and_includes_plugins(self, env):
        tools = _tools_above_read(env)
        assert {"kb_create", "kb_update", "kb_delete", "task_create"} <= set(tools)
        assert any(not n.startswith(("kb_", "task_")) for n in tools), (
            "no plugin write tools found -- the walk would miss extensions"
        )

    def test_every_write_tool_refuses_a_read_only_kb(self, env):
        """Guarded means *the write check* refused it -- FORBIDDEN, which only
        that check emits. A NOT_FOUND from an earlier gate (the read scoping's
        fail-closed branch, say) would hide a write tool the check misses."""
        unguarded = []
        for name, tier in sorted(_tools_above_read(env).items()):
            server = env["server"](tier)
            args = _args_naming(server.tools[name], PUB)
            out = server._dispatch_tool(
                name, args, client_id=f"gate-{name}", readable_kbs={PUB, TEAM}, writable_kbs=set()
            )
            if out.get("error_code") != "FORBIDDEN":
                unguarded.append((name, out))
        assert not unguarded, f"write-tier tools not refused by the per-KB write check: {unguarded}"

    def test_every_write_tool_refuses_when_writable_set_is_missing(self, env):
        # A scoped caller whose writable set was never resolved fails closed.
        for name, tier in sorted(_tools_above_read(env).items()):
            server = env["server"](tier)
            out = server._dispatch_tool(
                name,
                _args_naming(server.tools[name], PUB),
                client_id=f"gate2-{name}",
                readable_kbs={PUB, TEAM},
            )
            assert out.get("error_code") == "FORBIDDEN", (name, out)

    def test_every_read_tool_passes_the_write_check_for_a_read_only_user(self, env):
        """The reverse walk: a read-only scoped user is never refused a
        read-tier tool by the write check. Other refusals (missing required
        arguments, a KB of the wrong type) are the handler's business."""
        server = env["server"]("write")
        refused = []
        # The read tier's own registry, plus the tools known to only read
        # wherever a plugin registers them -- the walk must not trust the
        # registration it is checking.
        names = set(env["server"]("read").tools) | (KNOWN_READ_ONLY_TOOLS & set(server.tools))
        for name in sorted(names):
            out = server._dispatch_tool(
                name,
                _args_naming(server.tools[name], PUB),
                client_id=f"rev-{name}",
                readable_kbs={PUB, TEAM},
                writable_kbs=set(),
            )
            if out.get("error_code") == "FORBIDDEN":
                refused.append((name, out))
        assert not refused, f"read-tier tools refused to a read-only user: {refused}"

    @pytest.mark.parametrize(
        ("tool", "args"),
        [
            ("investigation_search_all", {"query": "note", "kb_names": [PUB]}),
            ("investigation_search_all", {"query": "note"}),
            ("investigation_status", {"kb_name": PUB}),
        ],
    )
    def test_journalism_read_tools_run_for_a_read_only_user(self, env, tool, args):
        """These two only read (#223 narrowed them to the readable set) but
        were registered in the plugin's write-tier block."""
        server = env["server"]("write")
        if tool not in server.tools:
            pytest.skip("journalism-investigation extension not installed")
        assert server._tool_tiers[tool] == "read"
        out = server._dispatch_tool(
            tool, args, client_id=f"j-{tool}", readable_kbs={PUB, TEAM}, writable_kbs=set()
        )
        assert "error_code" not in out, out

    def test_plugin_read_tools_are_classified_read(self, env):
        # The write check keys off each tool's tier, so a plugin's read tool
        # labelled with the server's tier would be refused to read-only users.
        server = env["server"]("write")
        read_names = set(env["server"]("read").tools)
        mislabelled = sorted(n for n in read_names if server._tool_tiers[n] != "read")
        assert not mislabelled, mislabelled
