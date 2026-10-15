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
    mcp_server.svc.create_entry("notes", "present", "Present", "note", "body")

    def fail(*_args, **_kwargs):
        raise StorageError("sensitive/path/index.db is locked")

    # The file write's OS edge, so the real update path runs up to it (the
    # rule in test_tests_drive_the_surface_not_the_mock.py).
    monkeypatch.setattr("pyrite.utils.atomic_write.os.replace", fail)
    result = _call(
        mcp_server,
        "kb_update",
        {"kb_name": "notes", "entry_id": "present", "title": "New title"},
    )
    assert result["error_code"] == "UPDATE_FAILED"
    assert result["retryable"] is True
    assert "sensitive/path" not in result["error"]


def _note(server, kb_name, entry_id, title="Note", body="body text"):
    server.svc.create_entry(kb_name, entry_id, title, "note", body)


@pytest.mark.parametrize("tool", ["kb_get", "kb_update"])
def test_id_only_in_another_kb_is_entry_not_found_in_the_named_kb(mcp_server, tool):
    # The id exists, but in `private`; asked of `notes` it is absent there.
    # Neither the other KB's entry nor a KB-level code may answer.
    _note(mcp_server, "private", "elsewhere", title="Private title")
    args = {"kb_name": "notes", "entry_id": "elsewhere"}
    if tool == "kb_update":
        args["title"] = "Overwritten"
    result = _call(mcp_server, tool, args)
    assert result["error_code"] == "ENTRY_NOT_FOUND", result
    assert result["legacy_error_code"] == ("UPDATE_FAILED" if tool == "kb_update" else "NOT_FOUND")
    assert result["retryable"] is False
    assert "Private title" not in json.dumps(result)
    kept = _call(mcp_server, "kb_get", {"kb_name": "private", "entry_id": "elsewhere"})
    assert kept["entry"]["title"] == "Private title"


def _register_late_kb(server, tmp_path):
    """`pyrite kb add` from another process while a stdio server runs: the KB
    is in the shared index's registry, not in the server's startup config."""
    from pyrite.services.kb_registry_service import KBRegistryService
    from pyrite.services.kb_service import KBService
    from pyrite.storage.database import PyriteDB

    index_path = server.config.settings.index_path
    other_config = PyriteConfig(knowledge_bases=[], settings=Settings(index_path=index_path))
    other_db = PyriteDB(index_path)
    try:
        KBRegistryService(other_config, other_db).add_kb("late", str(tmp_path / "late"))
        KBService(other_config, other_db).create_entry("late", "late-note", "Late", "note", "hi")
    finally:
        other_db.close()
    assert server.config.get_kb("late") is None  # the startup config never saw it


@pytest.mark.control(
    reason="dev's kb_get has no KB check; this guards #764's own check, red against its first version"
)
def test_kb_registered_after_startup_is_served_by_kb_get(mcp_server, tmp_path):
    # kb_list_entries and kb_read_body serve a late-registered KB; kb_get must
    # too, not answer KB_NOT_FOUND from the startup config alone.
    _register_late_kb(mcp_server, tmp_path)

    listed = _call(mcp_server, "kb_list_entries", {"kb_name": "late"})
    assert [e["id"] for e in listed["entries"]] == ["late-note"], listed
    body = _call(mcp_server, "kb_read_body", {"kb_name": "late", "entry_id": "late-note"})
    assert body["body"] == "hi", body

    got = _call(mcp_server, "kb_get", {"kb_name": "late", "entry_id": "late-note"})
    assert got.get("entry", {}).get("id") == "late-note", got


def test_kb_registered_after_startup_is_updated_by_kb_update(mcp_server, tmp_path):
    # The KB kb_get serves is one kb_update may write: both decide that it
    # exists from the same registry-aware lookup, so kb_update never answers
    # a non-retryable KB_NOT_FOUND for a KB kb_get just served.
    _register_late_kb(mcp_server, tmp_path)

    result = _call(
        mcp_server,
        "kb_update",
        {"kb_name": "late", "entry_id": "late-note", "title": "Renamed late"},
    )
    assert "error_code" not in result, result
    got = _call(mcp_server, "kb_get", {"kb_name": "late", "entry_id": "late-note"})
    assert got["entry"]["title"] == "Renamed late", got
    on_disk = (tmp_path / "late").rglob("late-note.md")
    assert any("Renamed late" in f.read_text(encoding="utf-8") for f in on_disk)
