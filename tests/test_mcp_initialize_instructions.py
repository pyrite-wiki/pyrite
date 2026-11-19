"""Test that MCP initialize result contains server instructions (#1292)."""

import asyncio

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.server.mcp_server import PyriteMCPServer


@pytest.mark.parametrize("tier", ["read", "write", "admin"])
def test_mcp_initialize_carries_instructions_with_kb_orient_and_tier(tmp_path, tier):
    cfg = PyriteConfig(
        knowledge_bases=[KBConfig(name="test", path=tmp_path)],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    mcp_server = PyriteMCPServer(config=cfg, tier=tier)
    sdk = mcp_server.build_sdk_server(client_id="test-client")

    async def _run():
        async with create_connected_server_and_client_session(sdk) as session:
            init = await session.initialize()
            return init.instructions

    instructions = asyncio.run(_run())
    assert instructions is not None
    assert "kb_orient" in instructions
    assert tier in instructions
    assert "higher tier are not listed" in instructions
    assert "error" in instructions and "error_code" in instructions and "retryable" in instructions
    assert len(instructions) < 600
