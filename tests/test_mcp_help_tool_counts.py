"""The `pyrite mcp --help` text states a tool count per tier. Those numbers
must match what a live server actually exposes at that tier, including
plugin tools registered dynamically (`PyriteMCPServer._register_plugin_tools`)
-- not just the core `READ_TOOLS`/`WRITE_TOOLS`/`ADMIN_TOOLS` dict sizes.

Regression for #229: the help text derived its numbers only from the core
dicts (29/11/8), while a constructed server exposes far more once plugin
tools (sw_*, investigation_*, zettel_*, wiki_*, social_*) are
counted in -- 70 tools at read tier alone.
"""

import re

from pyrite.cli import _mcp_command_help
from pyrite.plugins import get_registry
from pyrite.server.tool_schemas import ADMIN_TOOLS, READ_TOOLS, WRITE_TOOLS


def _true_tier_counts() -> dict[str, int]:
    """Core + plugin tool counts per tier, the same way PyriteMCPServer
    assembles `self.tools` in __init__ (core tables are cumulative across
    tiers; plugin tools come from `get_all_mcp_tools(tier)`, which is
    itself already cumulative per tier)."""
    registry = get_registry()
    core_cumulative = {
        "read": len(READ_TOOLS),
        "write": len(READ_TOOLS) + len(WRITE_TOOLS),
        "admin": len(READ_TOOLS) + len(WRITE_TOOLS) + len(ADMIN_TOOLS),
    }
    return {
        tier: core_cumulative[tier] + len(registry.get_all_mcp_tools(tier))
        for tier in ("read", "write", "admin")
    }


def test_mcp_help_text_tool_counts_match_live_server():
    help_text = _mcp_command_help()
    true_counts = _true_tier_counts()

    read_match = re.search(r"read: (\d+) tools", help_text)
    assert read_match, "help text should state a read tool count"
    assert int(read_match.group(1)) == true_counts["read"]

    write_match = re.search(r"write: read tier \+ (\d+) more", help_text)
    assert write_match, "help text should state additional write tools"
    read_plus_write = true_counts["read"] + int(write_match.group(1))
    assert read_plus_write == true_counts["write"]

    admin_match = re.search(r"admin: write tier \+ (\d+) more", help_text)
    assert admin_match, "help text should state additional admin tools"
    write_plus_admin = true_counts["write"] + int(admin_match.group(1))
    assert write_plus_admin == true_counts["admin"]
