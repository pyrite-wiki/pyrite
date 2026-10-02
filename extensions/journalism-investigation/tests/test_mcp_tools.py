"""Tests for journalism-investigation MCP tools."""

from pyrite_journalism_investigation.plugin import JournalismInvestigationPlugin


class TestMCPToolRegistration:
    def test_read_tier_tools_registered(self):
        plugin = JournalismInvestigationPlugin()
        tools = plugin.get_mcp_tools("read")
        assert "investigation_timeline" in tools
        assert "investigation_entities" in tools
        assert "investigation_network" in tools
        assert "investigation_sources" in tools
        assert "investigation_claims" in tools
        assert "investigation_evidence_chain" in tools
        assert "investigation_qa_report" in tools
        # Write-tier tools should NOT be in read tier
        assert "investigation_create_entity" not in tools

    def test_write_tier_inherits_read(self):
        plugin = JournalismInvestigationPlugin()
        tools = plugin.get_mcp_tools("write")
        assert "investigation_timeline" in tools

    def test_no_tools_for_invalid_tier(self):
        plugin = JournalismInvestigationPlugin()
        tools = plugin.get_mcp_tools("invalid")
        assert tools == {}

    def test_timeline_tool_schema(self):
        plugin = JournalismInvestigationPlugin()
        tools = plugin.get_mcp_tools("read")
        schema = tools["investigation_timeline"]["inputSchema"]
        props = schema["properties"]
        assert "from_date" in props
        assert "to_date" in props
        assert "actor" in props
        assert "event_type" in props
        assert "min_importance" in props
        assert "limit" in props
        assert "kb_name" in props

    def test_entities_tool_schema(self):
        plugin = JournalismInvestigationPlugin()
        tools = plugin.get_mcp_tools("read")
        schema = tools["investigation_entities"]["inputSchema"]
        props = schema["properties"]
        assert "entity_type" in props
        assert "min_importance" in props
        assert "jurisdiction" in props
        assert "kb_name" in props

    def test_network_tool_schema(self):
        plugin = JournalismInvestigationPlugin()
        tools = plugin.get_mcp_tools("read")
        schema = tools["investigation_network"]["inputSchema"]
        assert "entry_id" in schema["required"]
        assert "kb_name" in schema["properties"]  # optional, auto-detected
        # #63: both directions are paged, so the schema advertises how.
        assert "limit" in schema["properties"]
        assert "offset" in schema["properties"]

    def test_sources_tool_schema(self):
        plugin = JournalismInvestigationPlugin()
        tools = plugin.get_mcp_tools("read")
        schema = tools["investigation_sources"]["inputSchema"]
        props = schema["properties"]
        assert "reliability" in props
        assert "classification" in props
        assert "kb_name" in props

    def test_claims_tool_schema(self):
        plugin = JournalismInvestigationPlugin()
        tools = plugin.get_mcp_tools("read")
        schema = tools["investigation_claims"]["inputSchema"]
        props = schema["properties"]
        assert "claim_status" in props
        assert "confidence" in props
        assert "kb_name" in props

    def test_evidence_chain_tool_schema(self):
        plugin = JournalismInvestigationPlugin()
        tools = plugin.get_mcp_tools("read")
        schema = tools["investigation_evidence_chain"]["inputSchema"]
        assert "claim_id" in schema["required"]
        assert "kb_name" in schema["properties"]  # optional, auto-detected

    def test_all_tools_have_handler(self):
        plugin = JournalismInvestigationPlugin()
        for tier in ("read", "write", "admin"):
            tools = plugin.get_mcp_tools(tier)
            for name, tool in tools.items():
                assert "handler" in tool, f"{name} missing handler"
                assert callable(tool["handler"]), f"{name} handler not callable"

    def test_all_tools_have_description(self):
        plugin = JournalismInvestigationPlugin()
        for tier in ("read", "write", "admin"):
            tools = plugin.get_mcp_tools(tier)
            for name, tool in tools.items():
                assert "description" in tool, f"{name} missing description"
                assert len(tool["description"]) > 10, f"{name} description too short"

    def test_every_registered_tool_starts_with_experimental_notice(self):
        notice = "EXPERIMENTAL: Unsupported until the 0.28 alpha plugin/API contract."
        plugin = JournalismInvestigationPlugin()
        names = set()

        for tier in ("read", "write", "admin"):
            tools = plugin.get_mcp_tools(tier)
            names.update(tools)
            for name, tool in tools.items():
                assert tool["description"].startswith(notice + " "), name

        assert names
        assert all(name.startswith("investigation_") for name in names)


class TestMCPWriteToolRegistration:
    def test_write_tier_has_create_tools(self):
        plugin = JournalismInvestigationPlugin()
        tools = plugin.get_mcp_tools("write")
        assert "investigation_create_entity" in tools
        assert "investigation_create_event" in tools
        assert "investigation_create_claim" in tools
        assert "investigation_log_source" in tools

    def test_write_tier_also_has_read_tools(self):
        plugin = JournalismInvestigationPlugin()
        tools = plugin.get_mcp_tools("write")
        assert "investigation_timeline" in tools
        assert "investigation_claims" in tools

    def test_create_entity_schema(self):
        plugin = JournalismInvestigationPlugin()
        tools = plugin.get_mcp_tools("write")
        schema = tools["investigation_create_entity"]["inputSchema"]
        props = schema["properties"]
        assert "entity_type" in props
        assert "title" in props
        assert "kb_name" in props
        assert "title" in schema["required"]
        assert "entity_type" in schema["required"]

    def test_create_event_schema(self):
        plugin = JournalismInvestigationPlugin()
        tools = plugin.get_mcp_tools("write")
        schema = tools["investigation_create_event"]["inputSchema"]
        props = schema["properties"]
        assert "event_type" in props
        assert "title" in props
        assert "date" in props
        assert "kb_name" in props

    def test_create_claim_schema(self):
        plugin = JournalismInvestigationPlugin()
        tools = plugin.get_mcp_tools("write")
        schema = tools["investigation_create_claim"]["inputSchema"]
        props = schema["properties"]
        assert "title" in props
        assert "assertion" in props
        assert "evidence_refs" in props
        assert "kb_name" in props

    def test_log_source_schema(self):
        plugin = JournalismInvestigationPlugin()
        tools = plugin.get_mcp_tools("write")
        schema = tools["investigation_log_source"]["inputSchema"]
        props = schema["properties"]
        assert "title" in props
        assert "url" in props
        assert "reliability" in props
        assert "classification" in props
        assert "kb_name" in props
