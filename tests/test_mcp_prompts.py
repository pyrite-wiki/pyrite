"""
Tests for MCP Prompts and Resources.
"""

import json
import tempfile
from pathlib import Path

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.server.mcp_server import PyriteMCPServer


@pytest.fixture
def mcp_server(indexed_test_env):
    """MCP server with indexed test data."""
    env = indexed_test_env
    server = PyriteMCPServer(config=env["config"], tier="read")
    yield server
    server.close()


@pytest.fixture
def mcp_server_minimal():
    """Minimal MCP server for protocol-level tests (no indexed data)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"
        kb_path = tmpdir / "kb"
        kb_path.mkdir()

        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="test", path=kb_path, kb_type=KBType.EVENTS)],
            settings=Settings(index_path=db_path),
        )

        server = PyriteMCPServer(config)
        yield server
        server.close()


# =========================================================================
# Prompt Tests
# =========================================================================


class TestPromptsList:
    """Tests for prompts/list."""

    def test_prompts_list_returns_all_prompts(self, mcp_server):
        """prompts/list returns all 4 prompts."""
        prompts = list(mcp_server.prompts.values())
        assert len(prompts) == 4

        names = {p["name"] for p in prompts}
        assert names == {"research_topic", "summarize_entry", "find_connections", "daily_briefing"}

    def test_each_prompt_has_required_fields(self, mcp_server):
        """Each prompt has name, description, and arguments."""
        for prompt in mcp_server.prompts.values():
            assert "name" in prompt
            assert "description" in prompt
            assert "arguments" in prompt
            assert isinstance(prompt["arguments"], list)

    def test_prompt_arguments_have_required_fields(self, mcp_server):
        """Each argument has name, description, and required fields."""
        for prompt in mcp_server.prompts.values():
            for arg in prompt["arguments"]:
                assert "name" in arg
                assert "description" in arg
                assert "required" in arg


class TestPromptsGet:
    """Tests for prompts/get."""

    def test_research_topic_returns_messages(self, mcp_server):
        """prompts/get for research_topic returns messages with the topic."""
        result = mcp_server._get_prompt("research_topic", {"topic": "immigration"})

        assert "messages" in result
        messages = result["messages"]
        assert len(messages) >= 1
        assert messages[0]["role"] == "user"
        assert "immigration" in messages[0]["content"]["text"]

    def test_summarize_entry_with_valid_entry(self, mcp_server):
        """prompts/get for summarize_entry returns entry content."""
        # Get an entry ID from search
        search_result = mcp_server._dispatch_tool("kb_search", {"query": "Stephen Miller"})
        entry_id = search_result["results"][0]["id"]

        result = mcp_server._get_prompt(
            "summarize_entry", {"entry_id": entry_id, "kb_name": "test-research"}
        )

        assert "messages" in result
        messages = result["messages"]
        assert len(messages) >= 1
        assert "Summarize" in messages[0]["content"]["text"]
        # Entry content should be included
        assert "Stephen Miller" in messages[0]["content"]["text"]

    def test_summarize_entry_with_invalid_entry(self, mcp_server):
        """prompts/get for summarize_entry handles missing entry gracefully."""
        result = mcp_server._get_prompt("summarize_entry", {"entry_id": "nonexistent-entry"})

        assert "messages" in result
        messages = result["messages"]
        assert len(messages) >= 1
        # Should still return a message, not an error
        assert "not found" in messages[0]["content"]["text"].lower()

    def test_find_connections_returns_both_entries(self, mcp_server):
        """prompts/get for find_connections returns content about both entries."""
        result = mcp_server._get_prompt(
            "find_connections", {"entry_a": "some-entry-a", "entry_b": "some-entry-b"}
        )

        assert "messages" in result
        messages = result["messages"]
        assert len(messages) >= 1
        text = messages[0]["content"]["text"]
        assert "Entry A" in text
        assert "Entry B" in text
        assert "connections" in text.lower()

    def test_daily_briefing_returns_recent_data(self, mcp_server):
        """prompts/get for daily_briefing returns briefing context."""
        result = mcp_server._get_prompt("daily_briefing", {"days": "30"})

        assert "messages" in result
        messages = result["messages"]
        assert len(messages) >= 1
        text = messages[0]["content"]["text"]
        assert "briefing" in text.lower()

    def test_daily_briefing_default_days(self, mcp_server):
        """daily_briefing defaults to 7 days when not specified."""
        result = mcp_server._get_prompt("daily_briefing", {})

        assert "messages" in result
        messages = result["messages"]
        assert "7 days" in messages[0]["content"]["text"]

    def test_unknown_prompt_returns_error(self, mcp_server):
        """prompts/get for unknown prompt returns error."""
        result = mcp_server._get_prompt("nonexistent_prompt", {})

        assert "error" in result
        assert "nonexistent_prompt" in result["error"]


# =========================================================================
# Resource Tests
# =========================================================================


class TestResourcesList:
    """Tests for resources/list."""

    def test_resources_list_returns_static_resources(self, mcp_server):
        """resources/list returns the static pyrite://kbs resource."""
        resources = mcp_server.resources
        assert len(resources) >= 1

        uris = {r["uri"] for r in resources}
        assert "pyrite://kbs" in uris

    def test_resources_have_required_fields(self, mcp_server):
        """Each resource has uri, name, description, mimeType."""
        for resource in mcp_server.resources:
            assert "uri" in resource
            assert "name" in resource
            assert "description" in resource
            assert "mimeType" in resource


class TestResourceTemplatesList:
    """Tests for resources/templates/list."""

    def test_templates_list_returns_templates(self, mcp_server):
        """resources/templates/list returns URI templates."""
        templates = mcp_server.resource_templates
        assert len(templates) == 3

        uri_templates = {t["uriTemplate"] for t in templates}
        assert "pyrite://kbs/{name}/entries" in uri_templates
        # #528: an entry id in more than one KB is read through its KB.
        assert "pyrite://kbs/{name}/entries/{id}" in uri_templates
        assert "pyrite://entries/{id}" in uri_templates

    def test_templates_have_required_fields(self, mcp_server):
        """Each template has uriTemplate, name, description, mimeType."""
        for template in mcp_server.resource_templates:
            assert "uriTemplate" in template
            assert "name" in template
            assert "description" in template
            assert "mimeType" in template


class TestResourcesRead:
    """Tests for resources/read."""

    def test_read_kbs_returns_kb_list(self, mcp_server):
        """resources/read for pyrite://kbs returns KB list."""
        result = mcp_server._read_resource("pyrite://kbs")

        assert "contents" in result
        contents = result["contents"]
        assert len(contents) == 1
        assert contents[0]["uri"] == "pyrite://kbs"
        assert contents[0]["mimeType"] == "application/json"

        data = json.loads(contents[0]["text"])
        assert isinstance(data, list)
        kb_names = [kb["name"] for kb in data]
        assert "test-events" in kb_names
        assert "test-research" in kb_names

    def test_read_kb_entries_returns_entries(self, mcp_server):
        """resources/read for pyrite://kbs/{name}/entries returns entry list."""
        result = mcp_server._read_resource("pyrite://kbs/test-events/entries")

        assert "contents" in result
        contents = result["contents"]
        assert len(contents) == 1
        assert contents[0]["uri"] == "pyrite://kbs/test-events/entries"
        assert contents[0]["mimeType"] == "application/json"

        data = json.loads(contents[0]["text"])
        assert isinstance(data, list)

    def test_read_entry_returns_entry(self, mcp_server):
        """resources/read for pyrite://entries/{id} returns entry data."""
        # First find an entry ID
        search_result = mcp_server._dispatch_tool("kb_search", {"query": "Stephen Miller"})
        entry_id = search_result["results"][0]["id"]

        result = mcp_server._read_resource(f"pyrite://entries/{entry_id}")

        assert "contents" in result
        contents = result["contents"]
        assert len(contents) == 1
        assert contents[0]["mimeType"] == "application/json"

        data = json.loads(contents[0]["text"])
        assert data["title"] == "Stephen Miller"

    def test_read_entry_not_found(self, mcp_server):
        """resources/read for nonexistent entry returns error."""
        result = mcp_server._read_resource("pyrite://entries/nonexistent-id")
        assert "error" in result

    def test_read_unknown_uri_returns_error(self, mcp_server):
        """resources/read for unknown URI returns error."""
        result = mcp_server._read_resource("pyrite://unknown/path")
        assert "error" in result


# =========================================================================
# Capability Advertisement Tests
# =========================================================================


class TestCapabilities:
    """Test that the SDK server is properly configured."""

    def test_build_sdk_server_creates_server(self, mcp_server_minimal):
        """build_sdk_server returns a configured SDK Server."""
        from mcp.server import Server

        sdk = mcp_server_minimal.build_sdk_server()
        assert isinstance(sdk, Server)

    def test_prompts_available_at_all_tiers(self):
        """Prompts are available at read, write, and admin tiers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            db_path = tmpdir / "index.db"
            kb_path = tmpdir / "kb"
            kb_path.mkdir()

            config = PyriteConfig(
                knowledge_bases=[KBConfig(name="test", path=kb_path, kb_type=KBType.EVENTS)],
                settings=Settings(index_path=db_path),
            )

            for tier in ("read", "write", "admin"):
                server = PyriteMCPServer(config, tier=tier)
                try:
                    assert len(server.prompts) == 4, f"Tier {tier} should have 4 prompts"
                    assert len(server.resources) >= 1, f"Tier {tier} should have resources"
                    assert len(server.resource_templates) == 3, (
                        f"Tier {tier} should have 3 templates"
                    )
                finally:
                    server.close()


# ---------------------------------------------------------------------------
# White-label branding in the research_topic prompt (pyrite-white-labeling)
# ---------------------------------------------------------------------------


class TestResearchTopicBranding:
    def test_default_prompt_names_pyrite(self, mcp_server):
        result = mcp_server._get_prompt("research_topic", {"topic": "anything"})
        text = result["messages"][0]["content"]["text"]
        assert "Pyrite knowledge bases" in text

    def test_branded_prompt_names_brand_and_pyrite(self, tmp_path):
        import textwrap

        branding_dir = tmp_path / "branding"
        branding_dir.mkdir()
        (branding_dir / "branding.yaml").write_text(
            textwrap.dedent(
                """
                name: "Transparency Cascade Press"
                mcp:
                  agent_prompt_brand: "Transparency Cascade"
                """
            ).lstrip()
        )

        kb_path = tmp_path / "kb"
        kb_path.mkdir()
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="test-kb", path=kb_path, kb_type=KBType.GENERIC)],
            settings=Settings(
                index_path=tmp_path / "index.db",
                branding_dir=branding_dir,
            ),
        )
        server = PyriteMCPServer(config=config, tier="read")
        try:
            result = server._get_prompt("research_topic", {"topic": "anything"})
            text = result["messages"][0]["content"]["text"]
            assert "Transparency Cascade knowledge bases" in text
            assert "powered by Pyrite" in text
        finally:
            server.close()

    def test_broken_branding_yaml_is_a_refusal_not_a_raised_exception(self, tmp_path):
        """#445 cold read: before this fix, a broken ``branding.yaml`` made
        ``_get_prompt`` raise ``BrandingInvalidError`` straight out of
        ``_prompt_research_topic`` -- nothing in the dispatch path caught it,
        so it propagated as a raw exception (and, if serialized anywhere,
        would show the branding.yaml's absolute path). It must come back as
        the same structured MCP refusal envelope every other domain error
        gets, with a code from ``_DOMAIN_ERROR_CODES`` and no path in the
        message.
        """
        branding_dir = tmp_path / "branding"
        branding_dir.mkdir()
        (branding_dir / "branding.yaml").write_text("- a list, not a mapping\n")

        kb_path = tmp_path / "kb"
        kb_path.mkdir()
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="test-kb", path=kb_path, kb_type=KBType.GENERIC)],
            settings=Settings(index_path=tmp_path / "index.db", branding_dir=branding_dir),
        )
        server = PyriteMCPServer(config=config, tier="read")
        try:
            result = server._get_prompt("research_topic", {"topic": "anything"})
        finally:
            server.close()

        assert "error" in result, result
        assert result["error_code"] == "BRANDING_INVALID", result
        assert str(branding_dir) not in result["error"], result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
