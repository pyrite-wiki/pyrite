"""An entry type is a plain name from one charset, on every create path.

A KB that declares its types refuses any other name; a KB that declares none,
or a caller passing `allow_undeclared`, used to accept any string at all as a
type. The type names a subdirectory and a plugin class, and is shown back in
every listing, so it is held to one charset everywhere: word characters
(letters and digits in any script, and `_`) and `-`, starting with a word
character, at most 64 characters.
Checked in `KBService._prepare`, which every create path goes through.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import ValidationError
from pyrite.server.mcp_server import PyriteMCPServer
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB

NOT_PLAIN = ["two words", "a/b", "a\\b", ".hidden", "-leading", "x" * 65, "tab\there", "a:b"]
PLAIN = ["note", "meeting_note", "meeting-note", "Note2", "réunion", "x" * 64]


@pytest.fixture
def env(tmp_path: Path):
    (tmp_path / "kb").mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="kb", path=tmp_path / "kb", kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        yield config, KBService(config, db)
    finally:
        db.close()


def _files(config) -> list[Path]:
    return list(config.get_kb("kb").path.rglob("*.md"))


@pytest.mark.parametrize("entry_type", NOT_PLAIN)
def test_create_refuses_a_type_that_is_not_a_plain_name(env, entry_type):
    config, svc = env
    with pytest.raises(ValidationError, match="type"):
        svc.create_entry("kb", "e1", "Title", entry_type, "body", allow_undeclared=True)
    assert _files(config) == []


@pytest.mark.parametrize("entry_type", NOT_PLAIN[:3])
def test_bulk_create_refuses_a_type_that_is_not_a_plain_name(env, entry_type):
    config, svc = env
    [result] = svc.bulk_create_entries(
        "kb", [{"title": "Title", "entry_type": entry_type, "body": "b"}]
    )
    assert result.get("created") is not True, result
    assert _files(config) == []


def test_a_file_import_refuses_a_type_that_is_not_a_plain_name(env, tmp_path):
    config, svc = env
    source = tmp_path / "upload.md"
    source.write_text("---\ntitle: Imported\ntype: two words\n---\nbody\n")
    with pytest.raises(ValidationError, match="type"):
        svc.add_entry_from_file("kb", source)
    assert _files(config) == []


def test_kb_create_over_mcp_refuses_it_even_with_allow_undeclared(env):
    config, _ = env
    server = PyriteMCPServer(config=config, tier="write")
    try:
        result = server._dispatch_tool(
            "kb_create",
            {"kb_name": "kb", "title": "T", "entry_type": "a/b", "allow_undeclared": True},
            client_kind="local",
        )
    finally:
        server.close()
    assert result.get("error_code") == "VALIDATION_FAILED", result
    assert _files(config) == []


@pytest.mark.control(reason="plain type names were accepted before the change too")
@pytest.mark.parametrize("entry_type", PLAIN)
def test_create_accepts_a_plain_type_name(env, entry_type):
    config, svc = env
    entry = svc.create_entry("kb", "e1", "Title", entry_type, "body", allow_undeclared=True)
    assert entry.entry_type == entry_type
    assert len(_files(config)) == 1
