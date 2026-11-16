"""No write-tier MCP tool runs against a read-only KB, for any caller.

A KB the operator marked `read_only` takes no write. Roles say nothing about
the flag, and a plugin tool that writes its own tables never meets
`KBService`'s refusal, so `_dispatch_tool` refuses a write-tier tool -- core
or plugin, scoped caller or not -- that names a read-only KB, before any
handler runs. A `target_kb` is only referenced, so a link into a read-only
KB is not refused. The admin-tier tools that change anything under a KB's
root are refused by their services: `test_read_only_kb_takes_no_admin_write.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.plugins import get_registry
from pyrite.server.mcp_server import PyriteMCPServer

RO, RW = "frozen-kb", "open-kb"


class _Recorder:
    """A plugin with one write-tier tool that writes nothing but a note of
    the call, so the test sees whether the handler ran."""

    name = "recorder"
    capabilities: set = set()

    def __init__(self):
        self.calls: list[dict] = []

    def get_mcp_tools(self, tier: str) -> dict[str, dict]:
        if tier not in ("write", "admin"):
            return {}
        return {
            "recorder_note": {
                "description": "Record a note against a KB",
                "inputSchema": {
                    "type": "object",
                    "properties": {"kb_name": {"type": "string"}},
                    "required": ["kb_name"],
                },
                "handler": self._note,
            }
        }

    def _note(self, args):
        self.calls.append(args)
        return {"noted": True}


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    for name in (RO, RW):
        (tmp_path / name).mkdir()
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name=RO, path=tmp_path / RO, kb_type="generic", read_only=True),
            KBConfig(name=RW, path=tmp_path / RW, kb_type="generic"),
        ],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    recorder = _Recorder()
    registry = get_registry()
    registry.discover()
    monkeypatch.setitem(registry._plugins, recorder.name, recorder)
    server = PyriteMCPServer(config=config, tier="write")
    try:
        yield server, recorder
    finally:
        server.close()


def _scoped(server, tool, args):
    """A scoped caller whose role grants write on both KBs -- the sets
    `mcp_routes` resolves from roles alone."""
    both = {RO, RW}
    return server._dispatch_tool(
        tool, args, client_kind="user", client_id="1", readable_kbs=both, writable_kbs=both
    )


def test_a_plugin_write_tool_does_not_run_against_a_read_only_kb(env):
    server, recorder = env
    result = _scoped(server, "recorder_note", {"kb_name": RO})
    assert result.get("error_code") == "KB_READ_ONLY", result
    assert recorder.calls == []


def test_a_core_write_tool_is_refused_before_its_handler_too(env):
    server, _ = env
    result = _scoped(server, "kb_create", {"kb_name": RO, "title": "T", "body": "b"})
    assert result.get("error_code") == "KB_READ_ONLY", result
    assert not list(server.config.get_kb(RO).path.rglob("*.md"))


@pytest.mark.control(reason="a writable KB is served as before")
def test_a_write_tool_runs_against_a_writable_kb(env):
    server, recorder = env
    assert _scoped(server, "recorder_note", {"kb_name": RW}) == {"noted": True}
    assert recorder.calls == [{"kb_name": RW}]


@pytest.mark.control(reason="a read tool may read a read-only KB, as before")
def test_a_read_tool_reads_a_read_only_kb(env):
    server, _ = env
    result = _scoped(server, "kb_list_entries", {"kb_name": RO})
    assert "error" not in result, result


def test_an_unscoped_caller_is_refused_too(env):
    server, recorder = env
    result = server._dispatch_tool("recorder_note", {"kb_name": RO}, client_kind="local")
    assert result.get("error_code") == "KB_READ_ONLY", result
    assert recorder.calls == []


@pytest.mark.control(reason="a link into a read-only KB writes only its source, as before")
def test_a_link_from_a_writable_kb_to_a_read_only_kb_is_not_refused_as_read_only(env):
    server, _ = env
    server.svc.create_entry(RW, "src", "Source", "note", "b")
    result = _scoped(
        server,
        "kb_link",
        {"source_kb": RW, "source_id": "src", "target_kb": RO, "target_id": "dst"},
    )
    assert result.get("error_code") != "KB_READ_ONLY", result
