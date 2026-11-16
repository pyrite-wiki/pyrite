"""A response carries text Pyrite wrote for the caller, or text that is the caller's own.

ADR-0052 N3, completed (0.26.3): text from outside Pyrite belongs to the
operator unless it is marked otherwise *where it is made*. Two origins are the
caller's to see:

- **caller-request**: detail about their own request (a parse error quoting
  their YAML, their query, their JSON upload; the parser is never given a
  filename, so its text can quote only bytes);
- **KB(name)**: bytes of a KB, shown to a caller who holds a read grant on
  that KB (maintainer decision D4).

``pyrite.exceptions`` is the constructor: ``caller_owned`` / ``kb_owned`` mark
an exception, ``outside_text`` / ``public_text`` show a marked exception's text
to the caller it belongs to and nothing else. Everything unmarked stays the
operator's (logged; the CLI still sees it). ``held_fault`` keeps an exception
for a later message, its text decided when the message is shown; ``os_reason``
is the C library's text for an errno code (D5: an exception's own ``strerror``
is not used, it can carry anything).
"""

from __future__ import annotations

import errno
import os
import sys

import pytest

from pyrite.config import KBConfig
from pyrite.exceptions import (
    OUTSIDE_TEXT_WITHHELD,
    StorageError,
    ValidationError,
    outside_text,
    public_text,
)
from pyrite.services.acting import local_process
from tests._world import build_world

SENTINEL = "/srv/sentinel-c3d1/private/path"


# -- the constructor -----------------------------------------------------------


def test_caller_owned_text_reaches_the_network_caller_it_belongs_to():
    from pyrite.exceptions import caller_owned

    exc = caller_owned(ValueError("line 2, column 7: expected ',' or ']'"))
    assert outside_text(exc, local=False) == "line 2, column 7: expected ',' or ']'"
    assert public_text(exc, local=False) == "line 2, column 7: expected ',' or ']'"


@pytest.mark.control(reason="unmarked outside text was already withheld (0.26.2)")
def test_unmarked_outside_text_stays_the_operators():
    exc = ValueError(SENTINEL)
    assert outside_text(exc, local=False) == OUTSIDE_TEXT_WITHHELD
    assert SENTINEL not in public_text(exc, local=False)


def test_kb_text_reaches_only_a_caller_who_reads_that_kb():
    from pyrite.exceptions import kb_owned
    from pyrite.services.acting import read_grant

    shown = kb_owned(ValueError("line 3: bad indentation"), "notes")
    other = kb_owned(ValueError(SENTINEL), "private")
    with read_grant(lambda name: name == "notes"):
        assert outside_text(shown, local=False) == "line 3: bad indentation"
        assert outside_text(other, local=False) == OUTSIDE_TEXT_WITHHELD
    # With no grant bound (a job, an embedded caller), KB text is withheld:
    # deny by default.
    assert outside_text(shown, local=False) == OUTSIDE_TEXT_WITHHELD


def test_marking_a_pyrite_error_does_not_unmask_a_class_that_fixes_its_message():
    from pyrite.exceptions import caller_owned

    exc = caller_owned(StorageError(SENTINEL))
    assert SENTINEL not in public_text(exc, local=False)
    assert SENTINEL not in outside_text(exc, local=False)


def test_a_held_fault_is_decided_when_it_is_shown():
    from pyrite.exceptions import held_fault

    held = held_fault(RuntimeError(SENTINEL))
    with local_process(False):
        assert SENTINEL not in str(held)
    with local_process(True):
        assert SENTINEL in str(held)
    assert held.type_name == "RuntimeError"


def test_public_attributes_come_only_from_a_pyrite_error():
    from pyrite.exceptions import public_attribute

    outside = ValueError("x")
    outside.suggestion = SENTINEL  # type: ignore[attr-defined]
    outside.reason = SENTINEL  # type: ignore[attr-defined]
    assert public_attribute(outside, "suggestion") is None
    assert public_attribute(outside, "reason") is None
    assert public_attribute(ValidationError("t", suggestion="add a title"), "suggestion") == (
        "add a title"
    )


def test_os_reason_is_the_c_librarys_text_for_the_code_not_the_exceptions():
    """D5: ``strerror`` is the exception's, and an ``OSError`` can be built
    with any text there; the reason for its code is written by the C library."""
    from pyrite.exceptions import os_reason

    lying = OSError(errno.EACCES, SENTINEL, "/srv/file")
    assert os_reason(lying) == os.strerror(errno.EACCES)
    assert os_reason(OSError("no code")) == "OSError"


# -- the vec loader's text ---------------------------------------------


@pytest.fixture
def vec_fails(tmp_path, monkeypatch):
    """sqlite-vec installed, its load raising text that names the host."""
    from importlib.machinery import ModuleSpec
    from types import ModuleType

    def load(_conn):
        raise RuntimeError(f"dlopen({SENTINEL}/vec0.so): incompatible architecture")

    module = ModuleType("sqlite_vec")
    module.__spec__ = ModuleSpec("sqlite_vec", None)
    module.load = load
    monkeypatch.setitem(sys.modules, "sqlite_vec", module)
    monkeypatch.setattr("pyrite.services.embedding_service.is_available", lambda: True)
    kb = tmp_path / "kb"
    (kb / "notes").mkdir(parents=True)
    (kb / "notes" / "one.md").write_text("---\nid: one\ntitle: Falcon\ntype: note\n---\nb\n")
    w = build_world(tmp_path / "world", kbs=[KBConfig(name="k", path=kb, kb_type="generic")])
    w.index("k")
    yield w
    w.close()


def test_rest_search_warning_names_the_loader_class_not_its_text(vec_fails):
    r = vec_fails.client.get("/api/search", params={"q": "falcon", "mode": "semantic"})
    assert r.status_code == 200, r.text
    assert SENTINEL not in r.text
    assert "RuntimeError" in r.text  # the cause class and the remedy remain


def test_mcp_search_warning_names_the_loader_class_not_its_text(vec_fails):
    result = vec_fails.mcp()._dispatch_tool("kb_search", {"query": "falcon", "mode": "semantic"})
    text = str(result)
    assert SENTINEL not in text
    assert "RuntimeError" in text


@pytest.mark.control(reason="the local operator kept the loader's text before, and keeps it")
def test_cli_search_still_shows_the_loader_text(vec_fails):
    r = vec_fails.cli("search", "falcon", "-k", "k", "--mode", "semantic", "--format", "json")
    assert SENTINEL in r.output, r.output


# -- the caller's own input --------------------------------------------


@pytest.fixture
def world(tmp_path):
    kb = tmp_path / "kb"
    (kb / "notes").mkdir(parents=True)
    (kb / "notes" / "one.md").write_text("---\nid: one\ntitle: One\ntype: note\n---\nbody\n")
    w = build_world(tmp_path / "world", kbs=[KBConfig(name="k", path=kb, kb_type="generic")])
    w.index("k")
    yield w, kb
    w.close()


def test_rest_import_names_where_the_callers_json_does_not_parse(world):
    w, _kb = world
    r = w.client.post(
        "/api/entries/import",
        params={"kb": "k", "format": "json"},
        files={"file": ("e.json", b'[{"title": "a",}]', "application/json")},
    )
    assert r.status_code == 400, r.text
    message = r.json()["detail"]["message"]
    assert "line 1 column" in message, message  # json's own words about the upload


def test_rest_import_names_where_the_callers_markdown_yaml_does_not_parse(world):
    w, _kb = world
    r = w.client.post(
        "/api/entries/import",
        params={"kb": "k", "format": "markdown"},
        files={"file": ("e.md", b"---\ntitle: [unclosed\n---\nbody\n", "text/markdown")},
    )
    assert r.status_code in (400, 422), r.text
    assert "line 2" in r.text or "flow sequence" in r.text, r.text


@pytest.mark.control(reason="a fault that is not the parser's was hidden before, and stays hidden")
def test_rest_import_keeps_a_fault_that_is_not_the_callers_input_hidden(world, monkeypatch):
    w, _kb = world
    from pyrite.formats import importers

    registry = importers.get_importer_registry()

    def boom(_content, **_k):
        raise PermissionError(13, "Permission denied", SENTINEL)

    monkeypatch.setitem(registry._importers, "json", boom)
    r = w.client.post(
        "/api/entries/import",
        params={"kb": "k", "format": "json"},
        files={"file": ("e.json", b"[]", "application/json")},
    )
    assert r.status_code == 400, r.text
    assert SENTINEL not in r.text


def test_the_reread_after_an_edit_is_the_kbs_text_not_the_callers():
    """``_final_check`` re-reads the file with the caller's edit applied: the
    bytes are the KB's. Shown to a caller who reads that KB (D4), never as
    the caller's own request, and withheld when no origin is named."""
    from types import SimpleNamespace

    from pyrite.exceptions import kb_origin
    from pyrite.services.acting import read_grant
    from pyrite.storage.file_operations import _final_check

    doc = SimpleNamespace(text="---\ntitle: [unclosed\n---\nbody\n")  # it reads only .text
    with local_process(False), read_grant(lambda kb: kb == "k"):
        shown = _final_check(doc, {"title": "x"}, origin=kb_origin("k"))
        other = _final_check(doc, {"title": "x"}, origin=kb_origin("private"))
        unnamed = _final_check(doc, {"title": "x"})
    assert "flow sequence" in shown, shown
    assert OUTSIDE_TEXT_WITHHELD in other and "flow sequence" not in other, other
    assert OUTSIDE_TEXT_WITHHELD in unnamed and "flow sequence" not in unnamed, unnamed


def test_rest_qa_of_a_kb_file_whose_yaml_breaks_names_the_parsers_detail(world):
    """D4: the bytes are the KB's, and this caller reads that KB."""
    w, kb = world
    (kb / "notes" / "one.md").write_text("---\nid: one\ntitle: [unclosed\ntype: note\n---\nb\n")
    r = w.client.get("/api/qa/validate/one", params={"kb": "k"})
    assert r.status_code == 200, r.text
    assert "Invalid YAML frontmatter" in r.text, r.text
    assert OUTSIDE_TEXT_WITHHELD not in r.text, r.text
    assert "flow sequence" in r.text, r.text


def _broken(kb):
    (kb / "notes" / "one.md").write_text("---\nid: one\ntitle: [unclosed\ntype: note\n---\nb\n")


def test_mcp_qa_of_a_kb_file_the_caller_reads_names_the_parsers_detail(world):
    """D4 over MCP: the dispatcher binds the caller's read grants."""
    from pyrite.services.access_policy import Principal

    w, kb = world
    _broken(kb)
    result = w.mcp()._dispatch_tool(
        "kb_qa_validate",
        {"kb_name": "k", "entry_id": "one"},
        readable_kbs={"k"},
        principal=Principal.from_api_key("admin"),
    )
    text = str(result)
    assert "Invalid YAML frontmatter" in text, text
    assert "flow sequence" in text, text


def test_kb_text_is_withheld_from_a_caller_without_a_read_grant_on_that_kb(world):
    """The same read, the grant check answering no: the operator's message.
    (A caller with no grant cannot name the KB at all; this pins the rule at
    the point where the text is decided.)"""
    from pyrite.services.acting import read_grant
    from pyrite.storage.repository import KBRepository

    w, kb = world
    _broken(kb)
    repo = KBRepository(w.config.get_kb("k"))
    with local_process(False), read_grant(lambda _kb: False), pytest.raises(Exception) as exc:
        repo.load_entry_from_file(kb / "notes" / "one.md")
    assert "flow sequence" not in str(exc.value)
    assert OUTSIDE_TEXT_WITHHELD in str(exc.value)


def _span(text):
    from pyrite.storage.file_operations import FrontmatterSpan
    from pyrite.utils.frontmatter import split_frontmatter

    split = split_frontmatter(text)
    return FrontmatterSpan(split.yaml_start, split.close_start, split.close_end)


@pytest.mark.parametrize(
    ("line", "detail"),
    [
        ("title: [unclosed", "flow sequence"),  # the composer
        ("title: x\nd: 2024-02-30", "day is out of range"),  # a constructor
    ],
)
def test_a_file_on_disk_that_does_not_parse_is_the_kbs_text(line, detail):
    """``apply()`` parses the file as it is on disk before any edit: its bytes
    are the KB's, shown only to a caller holding a read grant on that KB."""
    from pyrite.exceptions import kb_origin
    from pyrite.services.acting import read_grant
    from pyrite.storage.file_operations import OperationRefusedError, Set, apply

    text = f"---\n{line}\n---\nbody\n"

    def refusal(grant, **origin) -> str:
        with local_process(False), read_grant(grant), pytest.raises(OperationRefusedError) as r:
            apply(text, [Set("title", "y")], _span(text), **origin)
        return str(r.value)

    reader = refusal(lambda kb: kb == "k", origin=kb_origin("k"))
    assert detail in reader and OUTSIDE_TEXT_WITHHELD not in reader, reader
    stranger = refusal(lambda kb: False, origin=kb_origin("k"))
    assert detail not in stranger and OUTSIDE_TEXT_WITHHELD in stranger, stranger
    unnamed = refusal(lambda kb: True)  # no origin named: the operator's
    assert detail not in unnamed and OUTSIDE_TEXT_WITHHELD in unnamed, unnamed


def test_refusal_fields_come_only_from_a_pyrite_error():
    from pyrite.exceptions import refusal_fields

    outside = ValueError("x")
    outside.candidate_kbs = [SENTINEL]  # type: ignore[attr-defined]
    outside.errors = [SENTINEL]  # type: ignore[attr-defined]
    assert refusal_fields(outside) == {}


def _public_and_private(tmp_path):
    from pyrite.config import AuthConfig

    public, private = tmp_path / "pub", tmp_path / "priv"
    for root in (public, private):
        (root / "notes").mkdir(parents=True)
        (root / "notes" / "one.md").write_text("---\nid: one\ntitle: One\ntype: note\n---\nb\n")
    w = build_world(
        tmp_path / "w",
        kbs=[
            KBConfig(name="pub", path=public, kb_type="generic", default_role="read"),
            KBConfig(name="priv", path=private, kb_type="generic", default_role="none"),
        ],
        settings={"auth": AuthConfig(enabled=True, anonymous_tier="read")},
    )
    w.index("pub")
    w.index("priv")
    for root in (public, private):
        _broken(root)
    return w


def test_an_anonymous_visitor_reads_no_kb_text_even_where_anonymous_may_read(tmp_path):
    """Maintainer decision on D4 (2026-11-16): an anonymous visitor never sees
    text quoted from a KB file, whatever the KB's ``default_role`` allows; it
    reads Pyrite's own message. A private KB, which it cannot name, answers as
    missing."""
    w = _public_and_private(tmp_path)
    try:
        public = w.client.get("/api/qa/validate/one", params={"kb": "pub"})
        assert public.status_code == 200, public.text
        assert "Invalid YAML frontmatter" in public.text, public.text
        assert "flow sequence" not in public.text, public.text
        assert OUTSIDE_TEXT_WITHHELD in public.text, public.text
        private = w.client.get("/api/qa/validate/one", params={"kb": "priv"})
        assert private.status_code == 404, private.text
        assert "flow sequence" not in private.text
    finally:
        w.close()


@pytest.mark.control(reason="signed-in callers keep D4 as built: read through default_role")
def test_a_signed_in_caller_reading_through_default_role_sees_kb_text(tmp_path):
    from pyrite.services.auth_service import AuthService
    from tests.auth_seed import app_db, seed_user

    w = _public_and_private(tmp_path)
    try:
        db = app_db(w.client.app)
        seed_user(db, "admin", role="admin")
        seed_user(db, "reader", role="read")  # no explicit grant on pub
        token = AuthService(db, w.config.settings.auth).login("reader", "password123")[1]
        w.client.cookies.set("pyrite_session", token)
        shown = w.client.get("/api/qa/validate/one", params={"kb": "pub"})
        assert shown.status_code == 200, shown.text
        assert "flow sequence" in shown.text, shown.text
    finally:
        w.close()


def test_an_anonymous_visitors_own_input_is_still_theirs(tmp_path):
    """Caller-request text is unchanged for every caller, anonymous included."""
    w = _public_and_private(tmp_path)
    try:
        r = w.client.get("/api/search", params={"q": "falcon AND", "kb": "pub"})
        assert r.status_code == 400, r.text
        assert 'fts5: syntax error near ""' in r.json()["detail"]["message"], r.text
    finally:
        w.close()


# -- whose query: only the words the caller wrote --------------------------------


def test_rest_search_shows_the_parsers_words_about_the_callers_own_query(world):
    w, _kb = world
    r = w.client.get("/api/search", params={"q": "falcon AND", "kb": "k"})
    assert r.status_code == 400, r.text
    assert 'fts5: syntax error near ""' in r.json()["detail"]["message"], r.text


def test_mcp_search_shows_the_parsers_words_about_the_callers_own_query(world):
    from pyrite.services.access_policy import Principal

    w, _kb = world
    result = w.mcp()._dispatch_tool(
        "kb_search",
        {"query": "falcon AND", "kb_name": "k", "mode": "keyword"},
        readable_kbs={"k"},
        principal=Principal.from_api_key("read"),
    )
    assert 'fts5: syntax error near ""' in str(result), result


def test_a_held_fault_does_not_carry_a_caller_mark_to_a_later_caller():
    """A held exception is shown later, possibly to another caller: a
    caller-request mark meant the caller who sent the request, so a held fault
    ignores it. A KB mark is still decided against whoever reads at show time."""
    from pyrite.exceptions import caller_owned, held_fault, kb_owned
    from pyrite.services.acting import read_grant

    from_a_request = held_fault(caller_owned(ValueError(SENTINEL)))
    from_a_kb = held_fault(kb_owned(ValueError("line 3: bad indentation"), "notes"))
    with local_process(False):
        assert SENTINEL not in str(from_a_request)
        with read_grant(lambda kb: kb == "notes"):
            assert str(from_a_kb) == "line 3: bad indentation"
        with read_grant(lambda kb: False):
            assert "bad indentation" not in str(from_a_kb)
    with local_process(True):
        assert SENTINEL in str(from_a_request)  # the operator's to see


@pytest.mark.parametrize(
    ("fmt", "name"), [("json", "e.json"), ("yaml", "e.yaml"), ("markdown", "e.md")]
)
def test_rest_import_names_where_the_callers_upload_is_not_utf8(world, fmt, name):
    """Decoding the upload is the caller's bytes: the codec's words reach them.
    (CSV's decode is pinned in tests/test_request_body_limit.py.)"""
    w, _kb = world
    r = w.client.post(
        "/api/entries/import",
        params={"kb": "k", "format": fmt},
        files={"file": (name, b"title: \xff\xfe not utf-8\n", "application/octet-stream")},
    )
    assert r.status_code == 400, r.text
    message = r.json()["detail"]["message"]
    assert "can't decode byte 0xff" in message, message


def test_rest_import_stream_names_where_the_next_entrys_yaml_does_not_parse(world):
    """The markdown stream reads each next entry's frontmatter from the upload."""
    w, _kb = world
    stream = b"---\ntitle: First\n---\nbody\n---\ntitle: [unclosed\n---\nbody\n"
    r = w.client.post(
        "/api/entries/import",
        params={"kb": "k", "format": "markdown", "stream": "true"},
        files={"file": ("e.md", stream, "text/markdown")},
    )
    assert r.status_code in (400, 422), r.text
    assert "flow sequence" in r.text, r.text
