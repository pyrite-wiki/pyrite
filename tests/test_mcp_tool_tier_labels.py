"""Tool descriptions advertise their own tier over both MCP listing paths (#68)."""

import asyncio
from copy import deepcopy

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.plugins.registry import PluginRegistry
from pyrite.server import tool_schemas
from pyrite.server.mcp_server import PyriteMCPServer

TIERS = ("read", "write", "admin")
PREFIXES = ("[read] ", "[write] ", "[admin] ")


class SharedToolPlugin:
    """A real plugin that returns its original tool dictionaries on every call."""

    name = "tier-label-test"

    def __init__(self):
        self.tools = {
            f"tier_label_{tier}": {
                "description": f"Describe a {tier} tool.",
                "inputSchema": {"type": "object", "properties": {}},
                "handler": self.handle,
            }
            for tier in TIERS
        }

    @staticmethod
    def handle(args):
        return {"ok": True}

    def get_mcp_tools(self, tier):
        return {
            f"tier_label_{t}": self.tools[f"tier_label_{t}"] for t in TIERS[: TIERS.index(tier) + 1]
        }


@pytest.fixture
def registry(monkeypatch):
    import pyrite.plugins.registry as registry_module

    registry = PluginRegistry()
    registry.discover(strict=True)
    plugin = SharedToolPlugin()
    registry.register(plugin)
    monkeypatch.setattr(registry_module, "_registry", registry)
    return registry, plugin


@pytest.fixture
def minimal_config(tmp_path, registry):
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()
    return PyriteConfig(
        knowledge_bases=[KBConfig(name="test-kb", path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db"),
    )


def assert_tier_labels(server, tools):
    assert {tool["name"] for tool in tools} == set(server.tools)
    # Installed plugins must participate, rather than letting the inventory
    # silently shrink to only core tools when the test environment is incomplete.
    assert "sw_backlog" in server.tools
    for tool in tools:
        prefix = f"[{server._tool_tiers[tool['name']]}] "
        description = tool["description"]
        assert description.startswith(prefix), (tool["name"], description, prefix)
        assert not description[len(prefix) :].startswith(PREFIXES), tool["name"]


@pytest.mark.parametrize("tier", TIERS)
def test_every_advertised_tool_has_exactly_one_label_for_its_own_tier(minimal_config, tier):
    server = PyriteMCPServer(minimal_config, tier=tier)
    try:
        assert_tier_labels(server, server.get_tools_list())
    finally:
        server.close()


@pytest.mark.parametrize("tier", TIERS)
def test_tools_list_over_a_real_mcp_session_has_tier_labels(minimal_config, tier):
    server = PyriteMCPServer(minimal_config, tier=tier)

    async def list_tools():
        async with create_connected_server_and_client_session(server.build_sdk_server()) as session:
            result = await session.list_tools()
            return [tool.model_dump() for tool in result.tools]

    try:
        assert_tier_labels(server, asyncio.run(list_tools()))
    finally:
        server.close()


@pytest.mark.parametrize("tier", TIERS)
def test_repeated_construction_and_registration_keep_sources_and_one_label(
    minimal_config, registry, tier
):
    _, plugin = registry
    plugin_sources = deepcopy(plugin.tools)
    core_sources = deepcopy(
        (tool_schemas.READ_TOOLS, tool_schemas.WRITE_TOOLS, tool_schemas.ADMIN_TOOLS)
    )
    server = PyriteMCPServer(minimal_config, tier=tier)
    try:
        assert_tier_labels(server, server.get_tools_list())
        advertised = server.get_tools_list()
        for _ in range(2):
            for t in TIERS[: TIERS.index(tier) + 1]:
                getattr(server, f"_build_{t}_tools")()
            server._register_plugin_tools()
            assert server.get_tools_list() == advertised
            assert_tier_labels(server, server.get_tools_list())

        another = PyriteMCPServer(minimal_config, tier=tier)
        try:
            assert another.get_tools_list() == advertised
            assert_tier_labels(another, another.get_tools_list())
        finally:
            another.close()

        assert plugin.tools == plugin_sources
        assert (
            tool_schemas.READ_TOOLS,
            tool_schemas.WRITE_TOOLS,
            tool_schemas.ADMIN_TOOLS,
        ) == core_sources
        for t in TIERS[: TIERS.index(tier) + 1]:
            name = f"tier_label_{t}"
            assert server.tools[name] is not plugin.tools[name]
            assert server.tools[name]["handler"] is plugin.tools[name]["handler"]
            assert server.tools[name]["inputSchema"] == plugin.tools[name]["inputSchema"]
            assert server.tools[name]["description"] == f"[{t}] Describe a {t} tool."
    finally:
        server.close()


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("description", ["", None, pytest.param("missing", id="missing")])
@pytest.mark.parametrize("source", ["core", "plugin"])
def test_empty_or_missing_descriptions_are_advertised_with_a_label(
    minimal_config, registry, monkeypatch, tier, description, source
):
    if source == "core":
        name = {"read": "kb_list", "write": "kb_create", "admin": "kb_manage"}[tier]
        schema = getattr(tool_schemas, f"{tier.upper()}_TOOLS")[name]
    else:
        name = f"tier_label_{tier}"
        schema = registry[1].tools[name]
    if description == "missing":
        monkeypatch.delitem(schema, "description")
    else:
        monkeypatch.setitem(schema, "description", description)

    server = PyriteMCPServer(minimal_config, tier=tier)
    try:
        advertised = {tool["name"]: tool for tool in server.get_tools_list()}
        assert advertised[name]["description"] == f"[{tier}] "
        assert_tier_labels(server, server.get_tools_list())
    finally:
        server.close()


@pytest.mark.parametrize("tier", TIERS)
def test_tool_in_multiple_tier_tables_has_one_final_label(minimal_config, monkeypatch, tier):
    schema = tool_schemas.READ_TOOLS["kb_list"]
    monkeypatch.setitem(tool_schemas.WRITE_TOOLS, "kb_list", schema)
    monkeypatch.setitem(tool_schemas.ADMIN_TOOLS, "kb_list", schema)
    server = PyriteMCPServer(minimal_config, tier=tier)
    try:
        advertised = {tool["name"]: tool for tool in server.get_tools_list()}
        assert advertised["kb_list"]["description"] == (
            f"[{tier}] List all mounted knowledge bases with their types and entry counts"
        )
        assert server._tool_tiers["kb_list"] == tier
        assert (
            schema["description"]
            == "List all mounted knowledge bases with their types and entry counts"
        )
    finally:
        server.close()
