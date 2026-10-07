"""Missing MCP resources use domain codes through the public tool dispatch (#761)."""

import asyncio
import json

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import StorageError
from pyrite.server.mcp_server import PyriteMCPServer


@pytest.fixture
def mcp_server(tmp_path):
    kb_path = tmp_path / "notes"
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text("name: notes\nkb_type: generic\n", encoding="utf-8")
    private_path = tmp_path / "private"
    private_path.mkdir()
    (private_path / "kb.yaml").write_text("name: private\nkb_type: generic\n", encoding="utf-8")
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name="notes", path=kb_path, kb_type="generic"),
            KBConfig(name="private", path=private_path, kb_type="generic"),
        ],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    server = PyriteMCPServer(config=config, tier="write")
    try:
        yield server
    finally:
        server.close()


def _call(server, tool, arguments, *, scoped=False):
    from mcp.shared.memory import create_connected_server_and_client_session

    sdk = server.build_sdk_server(
        client_id="missing-resource-test",
        readable_kbs={"notes"} if scoped else None,
        writable_kbs={"notes"} if scoped else None,
    )

    async def run():
        async with create_connected_server_and_client_session(sdk) as session:
            result = await session.call_tool(tool, arguments)
            return json.loads(result.content[0].text)

    return asyncio.run(run())


@pytest.mark.parametrize("tool", ["kb_get", "kb_update"])
def test_missing_entry_has_domain_code_and_legacy_code(mcp_server, tool):
    args = {"kb_name": "notes", "entry_id": "missing"}
    if tool == "kb_update":
        args["title"] = "New title"
    result = _call(mcp_server, tool, args)
    assert result["error_code"] == "ENTRY_NOT_FOUND", result
    assert result["legacy_error_code"] == ("UPDATE_FAILED" if tool == "kb_update" else "NOT_FOUND")
    assert result["retryable"] is False


@pytest.mark.parametrize("tool", ["kb_get", "kb_update"])
def test_missing_kb_has_domain_code(mcp_server, tool):
    result = _call(
        mcp_server,
        tool,
        {"kb_name": "missing", "entry_id": "missing", "title": "New title"},
    )
    assert result["error_code"] == "KB_NOT_FOUND", result
    assert result["legacy_error_code"] == ("UPDATE_FAILED" if tool == "kb_update" else "NOT_FOUND")
    assert result["retryable"] is False


@pytest.mark.parametrize("tool", ["kb_get", "kb_update"])
@pytest.mark.control(
    reason="existing dispatcher scoping must keep absent and unreadable KBs indistinguishable"
)
def test_scoped_caller_cannot_distinguish_unreadable_kb_from_absent(mcp_server, tool):
    args = {"kb_name": "private", "entry_id": "anything"}
    if tool == "kb_update":
        args["title"] = "New title"
    private = _call(mcp_server, tool, args, scoped=True)
    absent = _call(mcp_server, tool, {**args, "kb_name": "absent"}, scoped=True)
    assert private == {**absent, "error": "KB 'private' not found"}
    assert private["error_code"] == "KB_NOT_FOUND"
    assert private["legacy_error_code"] == "NOT_FOUND"
    assert private["retryable"] is False


@pytest.mark.control(
    reason="unrelated storage failures retain the existing safe UPDATE_FAILED response"
)
def test_unrelated_update_failure_keeps_safe_message_and_retry_semantics(mcp_server, monkeypatch):
    def fail(*_args, **_kwargs):
        raise StorageError("sensitive/path/index.db is locked")

    monkeypatch.setattr(mcp_server.svc, "update", fail)
    result = _call(
        mcp_server,
        "kb_update",
        {"kb_name": "notes", "entry_id": "missing", "title": "New title"},
    )
    assert result["error_code"] == "UPDATE_FAILED"
    assert result["retryable"] is True
    assert "sensitive/path" not in result["error"]
