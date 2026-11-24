"""A tool call runs off the event loop; one slow call never holds up another.

``_call_tool`` used to call the synchronous ``_dispatch_tool`` on the loop, so
every session sharing the process waited behind the slowest tool. The property:
the handler runs in a worker thread, the per-call context (the write-target
binding and any other contextvar) reaches it, and a second caller is answered
while the first is still working.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import threading
import time
from pathlib import Path

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams

from pyrite.config import KBConfig
from pyrite.server.mcp_server import PyriteMCPServer
from pyrite.services.write_target import binding, current_target
from tests._world import build_world

MARK: contextvars.ContextVar[str] = contextvars.ContextVar("probe_mark", default="unset")
SEEN: dict[str, object] = {}

SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


def _probe_slow(arguments):
    SEEN["slow_thread"] = threading.get_ident()
    time.sleep(1.0)
    return {"slept": True}


def _probe_fast(arguments):
    SEEN["fast_done_at"] = time.monotonic()
    return {"fast": True}


def _probe_context(arguments):
    SEEN["thread"] = threading.get_ident()
    SEEN["mark"] = MARK.get()
    SEEN["target"] = current_target()
    return {"ok": True}


@pytest.fixture
def server(tmp_path: Path):
    w = build_world(
        tmp_path / "w",
        kbs=[KBConfig(name="k", path=tmp_path / "w" / "k", kb_type="generic")],
    )
    s = PyriteMCPServer(config=w.config, tier="read")
    for name, fn in (
        ("probe_slow", _probe_slow),
        ("probe_fast", _probe_fast),
        ("probe_context", _probe_context),
    ):
        s._register_tool(name, {"description": name, "inputSchema": SCHEMA, "handler": fn}, "read")
    SEEN.clear()
    yield s, w
    s.close()
    w.close()


async def _call(sdk, name):
    req = CallToolRequest(
        method="tools/call", params=CallToolRequestParams(name=name, arguments={})
    )
    result = (await sdk.request_handlers[CallToolRequest](req)).root
    return json.loads(result.content[0].text)


def test_the_handler_runs_in_a_worker_thread_with_the_callers_context(server):
    s, w = server
    sdk = s.build_sdk_server()
    loop_thread = {}

    async def go():
        loop_thread["id"] = threading.get_ident()
        MARK.set("from-the-caller")
        with binding((w.config, w.db)):
            bound = current_target()
            out = await _call(sdk, "probe_context")
        return bound, out

    bound, out = asyncio.run(go())
    assert out == {"ok": True}
    assert SEEN["thread"] != loop_thread["id"]
    assert SEEN["mark"] == "from-the-caller"
    assert SEEN["target"] is bound and bound is not None


def test_one_slow_call_does_not_hold_up_another_caller(server):
    s, _ = server
    slow_sdk = s.build_sdk_server(client_id="a")
    fast_sdk = s.build_sdk_server(client_id="b")

    async def go():
        started = time.monotonic()
        slow = asyncio.create_task(_call(slow_sdk, "probe_slow"))
        await asyncio.sleep(0.1)  # the slow call is in its handler
        await _call(fast_sdk, "probe_fast")
        fast_took = SEEN["fast_done_at"] - started
        await slow
        return fast_took

    fast_took = asyncio.run(go())
    assert fast_took < 0.7, f"the fast call waited {fast_took:.2f}s behind the slow one"
