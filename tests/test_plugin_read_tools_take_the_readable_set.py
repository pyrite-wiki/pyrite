"""Every plugin read tool that takes a KB argument also takes ``readable_kbs``.

A read tool's KB argument is checked by the dispatcher: the caller may read
the KB it names. That check says nothing about what the handler reads after
it: backlinks, a search, a graph or a report can reach entries in other KBs.
So a plugin read tool that names a KB must also take the caller's readable
set (``readable_kbs``, ``READABLE_KBS_KWARG``) and filter what it returns by
it (``pyrite/plugins/protocol.py``, ``get_mcp_tools``).

The list is generated from the registry, so a new read tool, in any
installed plugin, is held to the rule the day it is registered.
"""

from __future__ import annotations

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.plugins import get_registry
from pyrite.server.mcp_server import KB_ARGUMENT_NAMES, PyriteMCPServer


def _plugin_read_tools() -> list[str]:
    tools = get_registry().get_all_mcp_tools("read")
    return sorted(
        name
        for name, tool in tools.items()
        if set((tool.get("inputSchema") or {}).get("properties", {})) & set(KB_ARGUMENT_NAMES)
    )


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("w")
    (root / "k").mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="k", path=root / "k")],
        settings=Settings(index_path=root / "index.db"),
    )
    s = PyriteMCPServer(config=config, tier="read")
    yield s
    s.close()


@pytest.mark.control(reason="a check on the parametrisation, not on the code")
def test_the_registry_has_plugin_read_tools_that_name_a_kb():
    """The parametrisation below is not empty: the rule is checked on
    something."""
    assert len(_plugin_read_tools()) > 10


@pytest.mark.control(
    reason="every plugin read tool takes the readable set today; this holds a new one to it"
)
@pytest.mark.parametrize("name", _plugin_read_tools())
def test_a_plugin_read_tool_that_names_a_kb_takes_the_readable_set(server, name):
    assert name in server.tools, f"{name} is registered by a plugin but not served"
    assert server._handler_takes_readable_kbs(name), (
        f"{name} takes a KB argument but not readable_kbs: it must take the caller's "
        "readable set and filter what it returns by it (plugins/protocol.py)"
    )
