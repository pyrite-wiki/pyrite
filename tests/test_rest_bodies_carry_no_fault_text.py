"""No REST response body carries the text of a non-Pyrite exception or git's
raw stderr.

An expected fault (a provider error, a file the server cannot read, a git
command that fails) answers Pyrite's own fixed message with its error code;
the exception's text -- which can name a server path, a provider endpoint, a
driver's message -- goes to the server log only. A ``PyriteError`` keeps its
own text: Pyrite wrote it for the caller.

The root is ``pyrite.exceptions.public_text``, the one function every
transport asks "what may this exception show?": it now answers a fixed
message for any exception that is not a ``PyriteError``. Each route below,
with the call under it made to fail with a sentinel path in its text, is
driven through ``TestClient``.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

import pytest

from pyrite.config import KBConfig
from pyrite.exceptions import StorageError, ValidationError, public_text
from pyrite.services.kb_service import KBService
from tests._world import build_world

SENTINEL = "/srv/sentinel-a91f/private/path"
#: ``pyrite.exceptions.INTERNAL_PUBLIC_MESSAGE``, written out: the message a
#: caller sees in place of a fault's own text.
INTERNAL_PUBLIC_MESSAGE = "Internal error; the server log has the details."


# -- the root ----------------------------------------------------------------


def test_public_text_masks_a_non_pyrite_exception_and_logs_it(caplog):
    with caplog.at_level(logging.WARNING, logger="pyrite.exceptions"):
        shown = public_text(PermissionError(13, "Permission denied", SENTINEL))
    assert SENTINEL not in shown
    assert shown == INTERNAL_PUBLIC_MESSAGE
    assert SENTINEL in caplog.text


def test_public_text_masks_a_value_error_too():
    """A stdlib ``ValueError`` quotes its input: ``Path.relative_to`` names
    both absolute paths."""
    assert SENTINEL not in public_text(ValueError(f"{SENTINEL} is not in the subpath of /x"))


@pytest.mark.control(reason="a PyriteError's text is Pyrite's, written for the caller")
def test_public_text_keeps_a_pyrite_errors_own_text():
    assert public_text(ValidationError("title is required")) == "title is required"


def test_public_text_for_the_local_operator_keeps_the_text():
    """The CLI is the operator on their own machine: the text is theirs."""
    assert SENTINEL in public_text(OSError(SENTINEL), local=True)


# -- a world ----------------------------------------------------------------


KB = "k"


@pytest.fixture
def world(tmp_path):
    kb = tmp_path / "kb"
    (kb / "notes").mkdir(parents=True)
    (kb / "notes" / "one.md").write_text("---\nid: one\ntitle: One\ntype: note\n---\nbody\n")
    w = build_world(tmp_path / "world", kbs=[KBConfig(name=KB, path=kb, kb_type="generic")])
    w.index(KB)
    yield w, kb
    w.close()


# -- AI ----------------------------------------------------------------------


class _LLM:
    def status(self):
        return {"configured": True, "provider": "mock", "model": "m"}

    async def complete(self, *a, **k):
        raise OSError(SENTINEL)

    async def stream(self, *a, **k):
        raise OSError(SENTINEL)
        yield ""  # pragma: no cover - makes this an async generator


def _with_llm(w, llm):
    from unittest.mock import MagicMock

    from pyrite.server.api import get_llm_service, get_search_service

    w.app.dependency_overrides[get_llm_service] = lambda: llm
    # suggest-links asks the model only about candidates the search found.
    search = MagicMock()
    search.search.return_value = [
        {"id": "two", "kb_name": KB, "title": "Two", "entry_type": "note", "snippet": "s"}
    ]
    w.app.dependency_overrides[get_search_service] = lambda: search


@pytest.mark.parametrize("route", ["summarize", "auto-tag", "suggest-links"])
def test_ai_routes_do_not_return_the_providers_exception_text(world, route, caplog):
    w, _kb = world
    _with_llm(w, _LLM())
    with caplog.at_level(logging.ERROR):
        r = w.client.post(f"/api/ai/{route}", json={"entry_id": "one", "kb_name": KB})
    assert r.status_code == 500, r.text
    assert r.json()["detail"]["code"] == "AI_ERROR"
    assert SENTINEL not in r.text
    assert SENTINEL in caplog.text


def test_ai_chat_stream_error_event_does_not_carry_the_exception_text(world, caplog):
    w, _kb = world
    _with_llm(w, _LLM())
    with caplog.at_level(logging.ERROR):
        r = w.client.post(
            "/api/ai/chat", json={"messages": [{"role": "user", "content": "hi"}], "kb": KB}
        )
    assert r.status_code == 200
    events = [json.loads(x[6:]) for x in r.text.splitlines() if x.startswith("data: ")]
    assert [e["type"] for e in events] == ["error"]
    assert SENTINEL not in r.text
    assert SENTINEL in caplog.text


def test_ai_connection_test_does_not_return_the_providers_exception_text(world, caplog):
    from pyrite.server.api import get_llm_service
    from pyrite.services.llm_service import LLMService

    w, _kb = world
    settings = dataclasses.replace(
        w.config.settings, ai_provider="anthropic", ai_api_key="k", ai_model="m"
    )
    llm = LLMService(settings)

    def boom():
        raise RuntimeError(f"connect to {SENTINEL} failed")

    llm._get_anthropic_client = boom
    w.app.dependency_overrides[get_llm_service] = lambda: llm
    with caplog.at_level(logging.WARNING):
        r = w.client.post("/api/ai/test")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is False
    assert SENTINEL not in r.text
    assert SENTINEL in caplog.text


# -- import per-item results ---------------------------------------------------


def test_import_per_item_errors_do_not_carry_the_exception_text(world, monkeypatch):
    from pyrite.services.kb_service import KBService

    w, _kb = world

    def boom(self, *a, **k):
        raise PermissionError(13, "Permission denied", SENTINEL)

    monkeypatch.setattr(KBService, "_prepare_and_save", boom)
    payload = json.dumps([{"title": "Two", "entry_type": "note", "body": "b"}])
    r = w.client.post(
        f"/api/entries/import?kb={KB}",
        files={"file": ("x.json", payload, "application/json")},
    )
    assert r.status_code in (200, 207), r.text
    assert SENTINEL not in r.text
    assert "CREATE_FAILED" in r.text


# -- QA ----------------------------------------------------------------------


def test_qa_validate_does_not_quote_an_unreadable_files_error(world, monkeypatch):
    """The issue's probe: an indexed file the server can no longer read."""
    from pyrite.storage.repository import KBRepository

    w, _kb = world

    def unreadable(self, path, *a, **k):
        raise PermissionError(13, "Permission denied", SENTINEL)

    monkeypatch.setattr(KBRepository, "load_entry_from_file", unreadable)
    for url in (f"/api/qa/validate/one?kb={KB}", f"/api/qa/validate?kb={KB}"):
        r = w.client.get(url)
        assert r.status_code == 200, r.text
        assert SENTINEL not in r.text, url


# -- export --------------------------------------------------------------------


def test_export_catch_all_does_not_return_the_exception_text(world, monkeypatch, caplog):
    from pyrite.services.export_service import ExportService

    w, _kb = world

    def boom(self, *a, **k):
        raise OSError(SENTINEL)

    monkeypatch.setattr(ExportService, "export_kb_to_repo", boom)
    with caplog.at_level(logging.ERROR):
        r = w.client.post(
            f"/api/kbs/{KB}/export", json={"repo_url": "https://github.com/o/r", "branch": "main"}
        )
    assert r.status_code == 500, r.text
    assert r.json()["detail"]["code"] == "EXPORT_FAILED"
    assert SENTINEL not in r.text
    assert SENTINEL in caplog.text


# -- git: commit and the worktree diff ------------------------------------------


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, timeout=30)


def test_commit_route_does_not_return_gits_stderr(world, caplog):
    """A commit git refuses (the index is locked: git names the lock file's
    absolute path) answers Pyrite's message; the 200 body's ``error`` field
    and a 400 alike."""
    w, kb = world
    _git(kb, "init", "-q", "-b", "main")
    _git(
        kb, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "0"
    )
    (kb / "notes" / "two.md").write_text("---\nid: two\ntitle: Two\ntype: note\n---\n")
    (kb / ".git" / "index.lock").write_text("")
    with caplog.at_level(logging.WARNING):
        r = w.client.post(f"/api/kbs/{KB}/commit", json={"message": "m"})
    assert str(kb) not in r.text, r.text
    assert "index.lock" not in r.text
    assert "index.lock" in caplog.text


def test_worktree_diff_failure_does_not_return_os_or_git_text(tmp_path, caplog):
    """``GitService.diff_branches`` (``GET /api/worktree/changes`` returns its
    failure text as ``error``): a worktree folder that is gone answers a
    fixed message, not the OS error naming the folder."""
    from pyrite.services.git_service import GitService

    gone = tmp_path / "sentinel-a91f-gone"
    with caplog.at_level(logging.WARNING):
        ok, text = GitService.diff_branches(repo_path=gone, base="main", head="user/x")
    assert ok is False
    assert str(gone) not in text
    assert str(gone) in caplog.text


# -- every route and every tool (ADR-0052 N3) ---------------------------------
#
# The partner of ``tests/test_response_text_is_pyrites.py`` (the AST ratchet):
# that one reads the code for the shapes that put an exception's text into a
# response; these call every surface and read what comes back. Every public
# method of ``KBService`` is made to raise a fault whose text is a canary: an
# ``OSError`` (text from outside Pyrite) and a ``StorageError`` (a class whose
# ``public_message`` is fixed, so its own text is the operator's). Then every
# ``/api`` route, and every MCP tool at the admin tier, is called as a network
# caller calls it. The calls are filled generically (a KB name where a KB is
# named, a placeholder elsewhere), so not every one reaches ``KBService``; the
# tests count those that did and require a floor, so a filler that stops
# reaching the service fails rather than passing on nothing.

CANARY = "CANARY-n3-7f3a"
WALK_KB = "walk-kb"

FAULTS = {
    "OSError": lambda: OSError(13, f"Permission denied: '/srv/{CANARY}/entry.md'"),
    "StorageError": lambda: StorageError(f"disk fault at /srv/{CANARY}/index.db"),
}

#: Parameters that name a KB get the walk's KB; any other a placeholder.
_KB_PARAMS = {"kb", "kb_name", "name", "source_kb", "target_kb"}

#: Floors, set below the first run's counts (2026-11-10: 51 of 110 ``/api``
#: operations and 94 tools reached ``KBService``) so a new route filled less
#: well does not fail, but a filler that reaches nothing does.
MIN_REST_REACHED = 45
MIN_MCP_REACHED = 85


def _raising(calls: list[str], fault):
    def make(name: str):
        def raiser(*_args, **_kwargs):
            calls.append(name)
            raise fault()

        return raiser

    return make


@pytest.fixture
def faulty_kb_service(monkeypatch):
    """Patch every public KBService method to raise; yield (set_fault, calls)."""
    calls: list[str] = []
    state: dict[str, Any] = {}

    def fault():
        return FAULTS[state["fault"]]()

    make = _raising(calls, fault)
    for name, _member in inspect.getmembers(KBService, inspect.isfunction):
        if not name.startswith("_"):
            monkeypatch.setattr(KBService, name, make(name))

    def set_fault(kind: str) -> None:
        state["fault"] = kind

    return set_fault, calls


def _placeholder(schema: dict, name: str) -> Any:
    if name in _KB_PARAMS:
        return WALK_KB
    if "anyOf" in schema:
        schema = next((s for s in schema["anyOf"] if s.get("type") != "null"), {})
    if schema.get("enum"):
        return schema["enum"][0]
    return {
        "integer": 1,
        "number": 1,
        "boolean": False,
        "array": [],
        "object": {},
    }.get(schema.get("type", "string"), "x")


def _body(route) -> dict | None:
    field = route.body_field
    if field is None:
        return None
    model = getattr(field, "type_", None)
    try:
        schema = model.model_json_schema()
    except AttributeError:
        return {}
    props = schema.get("properties", {})
    return {k: _placeholder(props.get(k, {}), k) for k in schema.get("required", [])}


def _rest_calls(app) -> list[tuple[str, str, dict, dict | None]]:
    from tests._surface_inventory import _walk_routes

    calls = []
    for full_path, route in _walk_routes(app.routes):
        if not full_path.startswith("/api"):
            continue
        path = full_path
        for param in route.dependant.path_params:
            value = WALK_KB if param.name in _KB_PARAMS else "x"
            path = path.replace("{" + param.name + "}", value).replace(
                "{" + param.name + ":path}", value
            )
        query = {"kb": WALK_KB, "kb_name": WALK_KB, "q": "x", "query": "x"}
        for param in route.dependant.query_params:
            if param.field_info.is_required() and param.name not in query:
                query[param.name] = _placeholder({}, param.name)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            calls.append((method, path, query, _body(route)))
    return calls


@pytest.fixture
def rest_world(tmp_path):
    from fastapi.testclient import TestClient

    world = build_world(
        tmp_path, kbs=[KBConfig(name=WALK_KB, path=tmp_path / "kb", kb_type="generic")]
    )
    try:
        yield (
            world,
            TestClient(
                world.app, raise_server_exceptions=False, base_url=str(world.client.base_url)
            ),
        )
    finally:
        world.close()


@pytest.mark.parametrize(
    "fault",
    [
        pytest.param(
            "OSError",
            marks=pytest.mark.control(
                reason="no route re-answered a KBService OSError's text before this change "
                "(the earlier masking of non-Pyrite exceptions); guards the next one"
            ),
        ),
        # Red before this change: DELETE /api/entries/{id} (DELETE_FAILED) and
        # POST /api/kbs/{kb}/publish (PUBLISH_FAILED) answered str(StorageError).
        "StorageError",
    ],
)
def test_no_rest_body_carries_a_faults_text(rest_world, faulty_kb_service, fault):
    world, client = rest_world
    set_fault, calls = faulty_kb_service
    set_fault(fault)
    leaked, reached = [], 0
    for method, path, query, body in _rest_calls(world.app):
        before = len(calls)
        resp = client.request(method, path, params=query, json=body)
        reached += len(calls) > before
        if CANARY in resp.text:
            leaked.append(f"{method} {path} -> {resp.status_code}: {resp.text[:200]}")
    assert not leaked, f"{fault}'s text reached a REST body:\n" + "\n".join(leaked)
    assert reached >= MIN_REST_REACHED, f"only {reached} routes reached KBService"


def _tool_arguments(meta: dict) -> dict:
    schema = meta.get("inputSchema") or {}
    props = schema.get("properties", {})
    args = {k: _placeholder(props.get(k, {}), k) for k in schema.get("required", [])}
    for key in ("kb_name", "kb"):
        if key in props:
            args[key] = WALK_KB
    if "mode" in props:
        args["mode"] = "keyword"  # never load an embedding model here
    return args


@pytest.fixture
def mcp_world(tmp_path):
    from pyrite.server.mcp_server import PyriteMCPServer

    world = build_world(
        tmp_path, kbs=[KBConfig(name=WALK_KB, path=tmp_path / "kb", kb_type="generic")], rest=False
    )
    server = PyriteMCPServer(config=world.config, tier="admin")
    try:
        yield server
    finally:
        server.close()
        world.close()


@pytest.mark.control(
    reason="MCP's dispatcher already withheld both faults' text before this change; "
    "guards the next handler that catches one itself"
)
@pytest.mark.parametrize("fault", sorted(FAULTS))
def test_no_mcp_result_carries_a_faults_text(mcp_world, faulty_kb_service, fault):
    """Called as ``_dispatch_tool`` is called for an MCP request over HTTP:
    ``acting.is_local_process`` is False (its default), so this is a network
    caller."""
    set_fault, calls = faulty_kb_service
    set_fault(fault)
    leaked, reached = [], 0
    for name, meta in sorted(mcp_world.tools.items()):
        before = len(calls)
        result = mcp_world._dispatch_tool(name, _tool_arguments(meta), client_id="n3-test")
        reached += len(calls) > before
        text = json.dumps(result, default=str)
        if CANARY in text:
            leaked.append(f"{name}: {text[:200]}")
    assert not leaked, f"{fault}'s text reached an MCP result:\n" + "\n".join(leaked)
    assert reached >= MIN_MCP_REACHED, f"only {reached} tools reached KBService"
