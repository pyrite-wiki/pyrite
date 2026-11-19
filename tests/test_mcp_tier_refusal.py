"""Known higher-tier tools explain the permission they require."""

import asyncio
import ast
import json
import re
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from tests._world import build_world


@pytest.fixture
def world(tmp_path):
    result = build_world(tmp_path, kbs=[KBConfig(name="notes", path=tmp_path / "kb")], rest=False)
    try:
        yield result
    finally:
        result.close()


@pytest.mark.parametrize("tier", ["read", "write"])
@pytest.mark.parametrize("kind", ["local", "api_key"])
def test_every_higher_tier_tool_explains_its_required_tier(world, tier, kind):
    servers = {t: world.mcp(t) for t in ("read", "write", "admin")}
    required = {}
    for t, server in servers.items():
        for name in server.tools:
            required.setdefault(name, t)
    server = servers[tier]
    higher = set(servers["admin"].tools) - set(server.tools)
    assert {"kb_registry_reindex", "sw_log"} & higher
    for name in higher:
        result = server._dispatch_tool(name, {}, client_kind=kind)
        assert result["error_code"] == "FORBIDDEN", (name, result)
        assert result["required_tier"] == required[name], (name, result)
        assert result["retryable"] is False
        if kind == "local":
            assert f"--tier {required[name]}" in result["suggestion"]
        else:
            assert "operator" in result["suggestion"]
            assert "--tier" not in result["suggestion"]


@pytest.mark.parametrize("kind", ["local", "api_key"])
def test_sdk_session_keeps_the_tier_refusal_in_the_wire_result(world, kind):
    from mcp.shared.memory import create_connected_server_and_client_session

    server = world.mcp("read")
    sdk = server.build_sdk_server(client_id="tier-test", client_kind=kind)

    async def run():
        async with create_connected_server_and_client_session(sdk) as session:
            result = await session.call_tool("kb_create", {"kb_name": "notes", "title": "Test"})
            return json.loads(result.content[0].text)

    result = asyncio.run(run())
    assert result["error_code"] == "FORBIDDEN", result
    assert result["required_tier"] == "write"
    assert ("--tier write" in result["suggestion"]) == (kind == "local")


@pytest.mark.control(reason="an unknown name remains unknown and higher-tier tools stay unlisted")
@pytest.mark.parametrize("tier", ["read", "write", "admin"])
def test_unknown_tools_and_discovery_keep_their_contract(world, tier):
    server = world.mcp(tier)
    result = server._dispatch_tool("not_a_pyrite_tool", {})
    assert result["error_code"] == "UNKNOWN_TOOL"
    assert "required_tier" not in result
    if tier == "read":
        assert "kb_create" not in server.tools


def test_mcp_literal_error_codes_are_documented():
    root = Path(__file__).resolve().parent.parent
    source = ast.parse((root / "pyrite/server/mcp_server.py").read_text())
    codes = set()
    for node in ast.walk(source):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_error"
        ):
            if node.args and isinstance(node.args[0], ast.Constant):
                codes.add(node.args[0].value)
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "error_code"
                    and isinstance(value, ast.Constant)
                ):
                    codes.add(value.value)
    docs = (root / "docs/json-contracts.md").read_text()
    documented = set(re.findall(r"^\| `([A-Z_]+)`", docs, re.MULTILINE))
    assert codes <= documented, sorted(codes - documented)
