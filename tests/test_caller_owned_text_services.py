"""Provenance marks inside the services, driven through the service itself.

The surface-level properties live in ``tests/test_caller_owned_text.py``; these
two need a seam inside a service (whose ``apply`` call the update makes, a write
that fails on cue), which a file that drives REST, MCP or the CLI may not patch
(``tests/test_tests_drive_the_surface_not_the_mock.py``).
"""

from __future__ import annotations

import pytest

from pyrite.config import KBConfig
from pyrite.services.acting import local_process
from tests._world import build_world

SENTINEL = "/srv/sentinel-c3d1/private/path"


@pytest.fixture
def world(tmp_path):
    kb = tmp_path / "kb"
    (kb / "notes").mkdir(parents=True)
    (kb / "notes" / "one.md").write_text("---\nid: one\ntitle: One\ntype: note\n---\nbody\n")
    w = build_world(
        tmp_path / "world", kbs=[KBConfig(name="k", path=kb, kb_type="generic")], rest=False
    )
    w.index("k")
    yield w
    w.close()


def test_an_update_names_its_kb_as_the_origin_of_the_file_text(world, monkeypatch):
    """The update pipeline tells ``apply()`` whose bytes it parses. (A file
    whose YAML breaks fails the update's earlier read first, so the origin is
    pinned where it is passed.)"""
    from pyrite.exceptions import kb_origin
    from pyrite.services import kb_service
    from pyrite.services.kb_service import KBService
    from pyrite.storage import file_operations

    w = world
    seen: list = []
    real = file_operations.apply

    def spy(*args, **kwargs):
        seen.append(kwargs.get("origin"))
        return real(*args, **kwargs)

    monkeypatch.setattr(kb_service, "apply", spy)
    KBService(w.config, w.db).update_entry("one", "k", status="done")
    assert seen and all(origin == kb_origin("k") for origin in seen), seen


def test_a_qa_fix_that_fails_names_no_fault_text_to_a_network_caller(tmp_path, monkeypatch):
    """The reason is built in the handler and used after it; the fault's own
    text is the operator's (the CLI still shows it)."""
    from pyrite.services.kb_service import KBService
    from tests.test_qa_records_only_what_ran import _fix_world, _run_fix

    config, _path = _fix_world(tmp_path, "tag_case")

    def fail(*_a, **_k):
        raise PermissionError(13, "Permission denied", SENTINEL)

    monkeypatch.setattr(KBService, "apply_operations", fail)
    monkeypatch.setattr(KBService, "update_entry", fail)
    with local_process(False):
        network = _run_fix(config, "tag_case")
    with local_process(True):
        local = _run_fix(config, "tag_case")
    assert network["failed"], network
    assert all(SENTINEL not in f["reason"] for f in network["failed"]), network["failed"]
    assert all(f["reason"].startswith("update_failed: PermissionError") for f in network["failed"])
    assert any(SENTINEL in f["reason"] for f in local["failed"]), local["failed"]


class _RaisingDB:
    """A database whose search fails with the FTS parser's own message."""

    def __init__(self, message: str):
        self.message = message

    def search(self, **_kwargs):
        import sqlite3

        raise sqlite3.OperationalError(self.message)


def test_a_query_pyrite_built_is_not_the_callers_own():
    """``caller_query`` means the words the caller wrote, nothing else: a query
    Pyrite builds (suggest-links from an entry's title and tags, a chat
    message's words, a plugin's query) does not have the parser's message shown
    to a network caller; a surface that passes the caller's own query says so."""
    from pyrite.exceptions import QuerySyntaxError
    from pyrite.services.search_service import SearchService

    service = SearchService(_RaisingDB('fts5: syntax error near "harbour"'))
    with local_process(False):
        with pytest.raises(QuerySyntaxError) as built:
            service.search("harbour AND", mode="keyword", sanitize=False)
        with pytest.raises(QuerySyntaxError) as written:
            service.search("harbour AND", mode="keyword", sanitize=False, caller_wrote_query=True)
    assert "harbour" not in str(built.value), str(built.value)
    assert 'near "harbour"' in str(written.value), str(written.value)
