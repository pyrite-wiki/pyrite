"""Published MCP schemas say which tools refuse undeclared keys (#1779).

Since #1774 (#1758) the dispatcher refuses an undeclared argument key on
a write tool and reports it on a read tool, driven by the tool's own
``inputSchema``. But in JSON Schema a missing ``additionalProperties``
means extra keys are allowed, so a client reading ``tools/list`` could
not tell which tools are closed.

The invariant under test: what ``tools/list`` says matches what the
dispatcher does, for every registered tool (core and plugin, admin tier):

- a tool the dispatcher checks publishes ``"additionalProperties": false``;
- a free-form tool, one that takes any top-level key (``FREE_FORM``, the
  table ``tests/test_mcp_unknown_arguments.py`` pins for #1758),
  publishes anything but false (``true`` today).
"""

from __future__ import annotations

import pytest

from tests._world import mcp_server as _make_mcp_server
from tests.mcp_seed import STANDARD_KB_DEFS
from tests.test_mcp_unknown_arguments import FREE_FORM


@pytest.fixture(scope="module")
def server():
    with _make_mcp_server(STANDARD_KB_DEFS, tier="admin") as env:
        yield env["server"]


@pytest.mark.control(reason="guards against a vacuous pass")
def test_the_surface_is_the_full_one(server):
    """Guard against a vacuous pass: core write and read tools and plugin tools."""
    assert len(server.tools) > 80, len(server.tools)
    assert {"task_create", "task_claim", "kb_search", "sw_claim"} <= set(server.tools)


def test_every_published_schema_says_whether_the_tool_is_closed(server):
    published = {tool["name"]: tool["inputSchema"] for tool in server.get_tools_list()}
    assert set(published) == set(server.tools)
    wrong: list[str] = []
    for name, schema in sorted(published.items()):
        declared = schema.get("additionalProperties")
        if name in FREE_FORM:
            if declared is False:
                wrong.append(f"{name}: free-form but publishes additionalProperties: false")
        elif declared is not False:
            wrong.append(
                f"{name}: publishes additionalProperties {declared!r}, "
                "but the dispatcher refuses or reports undeclared keys"
            )
    assert not wrong, "\n  ".join(["published schema disagrees with the dispatcher:", *wrong])
