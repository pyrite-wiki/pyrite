"""MCP over SSE against a live server, driven the way Claude Code drives it.

Regression for PR #3. `mount_mcp_routes()` nests the SSE app under
`Mount("/mcp", ...)` but constructed `SseServerTransport("/mcp/messages/")`.
The SDK builds the client-facing POST path as
`scope["root_path"].rstrip("/") + self._endpoint`, and `root_path` is already
"/mcp" by then -- so the emitted `event: endpoint` pointed at
`/mcp/mcp/messages/?session_id=...`, which is not a registered route. The SSE
handshake looked fine and every real client died on its first POST with a 404.

`tests/test_mcp_routes.py` never asserted the emitted path, so nothing in
~4000 tests saw it. The `sse_session_result` fixture (conftest) reads the
endpoint event off the wire and completes a real session over it: initialize,
tools/list, kb_search. Everything below reads from that one session.
"""

from __future__ import annotations

import urllib.parse

import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture
def reachable_session(sse_session_result):
    """The session, or a skip when the endpoint itself is unroutable.

    When the advertised endpoint 404s, the two tests below it report that
    directly; every assertion that needs a working session would otherwise
    repeat the same failure and bury the cause.
    """
    if sse_session_result["unreachable"]:
        pytest.skip("the advertised MCP endpoint is unroutable; see the endpoint-path tests")
    return sse_session_result


def test_sse_advertises_an_endpoint_that_is_not_double_prefixed(sse_session_result):
    """PR #3's bug, asserted on the wire.

    The advertised path must be `/mcp/messages/?session_id=...`. If it reads
    `/mcp/mcp/messages/...`, the transport was constructed with a path that
    already includes the outer mount's prefix, and every real client 404s on
    its first POST.
    """
    path = sse_session_result["endpoint"]
    assert path is not None
    parsed = urllib.parse.urlparse(path)
    assert parsed.path == "/mcp/messages/", (
        f"advertised endpoint path is {parsed.path!r}; "
        f"'/mcp/mcp/messages/' is PR #3's double prefix"
    )
    assert "session_id=" in (parsed.query or ""), path


def test_the_advertised_endpoint_is_actually_routable(sse_session_result):
    assert sse_session_result["ping_status"] != 404, (
        f"POST to the advertised endpoint returned 404 "
        f"({sse_session_result['endpoint']}) -- it is not a registered route"
    )


def test_sse_session_initializes(reachable_session):
    assert "serverInfo" in reachable_session["server_info"], reachable_session["server_info"]


def test_sse_exposes_every_declared_read_tool(reachable_session):
    """The advertised tool list must contain everything tool_schemas declares.

    Superset, not equality: installed extensions contribute their own
    read-tier tools (sw_*, investigation_* ...) through the plugin
    registry, so equality would fail on any install that has them -- which
    CI's does. What must hold is that nothing declared goes missing. The
    stdio module asserts the stronger property: the two transports advertise
    exactly the same set as each other.
    """
    from pyrite.server.tool_schemas import READ_TOOLS

    missing = set(READ_TOOLS) - reachable_session["tool_names"]
    assert not missing, f"declared read tools absent from the SSE tool list: {sorted(missing)}"


def test_kb_search_over_sse_returns_the_seeded_entry(reachable_session):
    text = reachable_session["search_text"]
    assert "Analytical Engine" in text, text
