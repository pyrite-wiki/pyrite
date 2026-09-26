"""A blank KB name is never a KB (private #74).

`_resolve_kb_names` and the MCP chokepoint skip an empty KB name, so nothing
is refused for it; a handler that then passed `""` on as "the KB" reached a
service that reads a falsy name as "no KB given" and searched every KB. The
cold read of C1 round 1 found three routes returning a private entry's body,
a private collection's metadata, or whether a private id exists.

This pins the class, not the three routes. Every KB-bearing REST route and
every KB-bearing MCP tool (the ADR-0037 characterization inventory) is sent
a blank KB name by a scoped caller, twice:

- **A** names ids that exist only in the private KB (an entry, a
  collection);
- **B** names ids that exist nowhere.

A scoped caller cannot read the private KB, so A and B must answer
identically once the id itself is set aside (P-R5), and neither may carry
the private rows' content (P-R4). A route that refuses a blank KB passes;
so does one that applies the caller's scope. One that treats blank as
"every KB" fails, whatever its shape.
"""

from __future__ import annotations

import json
import re

import pytest

from pyrite.services.kb_service import KBService
from tests.characterization import mcp_calls, rest_calls
from tests.characterization.normalize import normalize_mcp_result, normalize_rest_body
from tests.characterization.surfaces import (
    kb_bearing_mcp_tool_names,
    kb_bearing_rest_operations,
)
from tests.characterization.world import PRIVATE, PRIVATE_ENTRY, READABLE, build_world

PRIVATE_COLLECTION = "private-collection-74"
PRIVATE_TAG = "private-tag-74"
PRIVATE_MARKERS = (
    PRIVATE_TAG,
    "Private note",
    "zebra behind the wall",
    "Private collection title",
    "private collection description",
    PRIVATE,
)
# Variant A names private ids, B names ids that exist nowhere. Each pair is
# replaced by one token before A and B are compared.
ID_PAIRS = {
    "entry": (PRIVATE_ENTRY, "no-such-entry-74"),
    "collection": (PRIVATE_COLLECTION, "no-such-collection-74"),
    "call_key": ("blank-kb-variant-a", "blank-kb-variant-b"),
}
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?")


@pytest.fixture(scope="module")
def w(tmp_path_factory):
    world = build_world(tmp_path_factory, label="blank-kb-74")
    KBService(world.config, world.db).create_entry(
        PRIVATE,
        PRIVATE_COLLECTION,
        "Private collection title",
        "collection",
        "private collection body",
        metadata={"description": "private collection description", "source_type": "folder"},
    )
    KBService(world.config, world.db).create_entry(
        PRIVATE, "private-tagged-74", "Tagged", "note", "t", tags=[PRIVATE_TAG]
    )
    KBService(world.config, world.db).create_entry(
        READABLE, "blank-kb-task", "Readable task", "task", "t"
    )
    world.index_worker.wait_for_idle(timeout=10)
    try:
        yield world
    finally:
        world.close()


class _EchoLLM:
    """A configured LLM that answers with its prompt and keeps every prompt,
    so what an AI route *sends* is checked as well as what it returns (the
    auto-tag route sent every KB's tag names to the provider)."""

    prompts: list[str] = []

    def status(self):
        return {"configured": True, "provider": "echo", "model": "echo"}

    def with_user_key(self, **_kwargs):
        return self

    async def complete(self, prompt, system=None, **_kwargs):
        self.prompts.append(f"{system or ''}\n{prompt}")
        return prompt

    def __getattr__(self, name):  # anything else the routes touch: harmless
        raise AttributeError(name)


@pytest.fixture(scope="module")
def echo_llm(w):
    from pyrite.server.api import get_llm_service

    llm = _EchoLLM()
    w.app.dependency_overrides[get_llm_service] = lambda: llm
    try:
        yield llm
    finally:
        w.app.dependency_overrides.pop(get_llm_service, None)


@pytest.fixture(scope="module")
def writer(w):
    """A scoped session user with write on the readable KB and no grant on
    the private one -- the principal the AI and other write-tier routes
    need to get past their tier check."""
    from pyrite.services.auth_service import AuthService
    from tests.auth_seed import SESSION_COOKIE, seed_user, sign_in

    row = seed_user(w.db, "blank-kb-writer", role="write")
    AuthService(w.db, w.config.settings.auth).grant_kb_permission(
        row["id"], READABLE, "write", granted_by=row["id"]
    )
    return {SESSION_COOKIE: sign_in(w.app, "blank-kb-writer")}


def _patch_ids(monkeypatch, variant: int) -> None:
    entry, collection = ID_PAIRS["entry"][variant], ID_PAIRS["collection"][variant]
    monkeypatch.setattr(rest_calls, "_entry_for", lambda kb: entry)
    monkeypatch.setattr(rest_calls, "FAKE_ENTRY_ID", entry)
    monkeypatch.setattr(rest_calls, "FAKE_COLLECTION_ID", collection)
    monkeypatch.setattr(mcp_calls, "_entry_for", lambda world, kb, tool_name="", call_key="": entry)


def _canonical(text: str, variant: int) -> str:
    for a_b in ID_PAIRS.values():
        text = text.replace(a_b[variant], f"<{a_b[0]}>")
    return _ISO.sub("<ts>", text)


def _assert_no_private_content(where: str, text: str) -> None:
    leaked = [m for m in PRIVATE_MARKERS if m in text]
    assert not leaked, f"{where} leaked {leaked}: {text[:400]}"


BLANKS = ["", "   "]


def _rest_answer(w, cookies, method, path, variant, monkeypatch, blank):
    _patch_ids(monkeypatch, variant)
    kwargs = rest_calls.build_call(w, method, path, blank, call_key=ID_PAIRS["call_key"][variant])
    w.release_idle_connections()
    r = w.client.request(method, cookies=cookies, **kwargs)
    try:
        body = normalize_rest_body(method, path, r.json(), tmpdir=str(w.tmpdir))
    except ValueError:
        body = r.text
    return r.status_code, json.dumps(body, sort_keys=True, default=str)


def _rest_cases(w):
    return [(op.method, op.path) for op in kb_bearing_rest_operations(w.app)]


_HELD_ON_THE_BASE = pytest.mark.control(
    reason="a class sweep: this combination already held before the fix (the resolver "
    "refused a whitespace name for a reader); it pins the class for every route"
)


@pytest.mark.parametrize(
    ("principal", "blank"),
    [
        pytest.param("local_user", "", id="local_user-empty"),
        pytest.param("local_user", "   ", id="local_user-whitespace", marks=_HELD_ON_THE_BASE),
        pytest.param("writer", "", id="writer-empty"),
        pytest.param("writer", "   ", id="writer-whitespace"),
    ],
)
def test_every_kb_route_treats_a_blank_kb_as_no_kb(
    w, writer, echo_llm, principal, blank, monkeypatch
):
    cookies = writer if principal == "writer" else w.principals["local_user"].rest_cookies
    failures = []
    for method, path in _rest_cases(w):
        where = f"{method} {path} kb={blank!r}"
        status_a, text_a = _rest_answer(w, cookies, method, path, 0, monkeypatch, blank)
        status_b, text_b = _rest_answer(w, cookies, method, path, 1, monkeypatch, blank)
        if status_a >= 500:
            failures.append(f"{where}: {status_a}")
            continue
        try:
            _assert_no_private_content(where, text_a)
        except AssertionError as exc:
            failures.append(str(exc))
            continue
        if (status_a, _canonical(text_a, 0)) != (status_b, _canonical(text_b, 1)):
            failures.append(
                f"{where}: private id answers {status_a} {text_a[:200]} "
                f"but a missing id answers {status_b} {text_b[:200]}"
            )
    for prompt in echo_llm.prompts:
        try:
            _assert_no_private_content("a prompt sent to the LLM", prompt)
        except AssertionError as exc:
            failures.append(str(exc))
    assert not failures, "\n".join(failures)


def _mcp_answer(w, tool, readable, writable, variant, monkeypatch, blank):
    _patch_ids(monkeypatch, variant)
    args = mcp_calls.build_arguments(w, tool, blank, call_key=ID_PAIRS["call_key"][variant])
    result = w.dispatch_tool(
        tool, args, client_kind="local", readable_kbs=readable, writable_kbs=writable
    )
    body = normalize_mcp_result(tool, result, tmpdir=str(w.tmpdir))
    return json.dumps(body, sort_keys=True, default=str)


@pytest.mark.control(
    reason="a class sweep: no MCP tool leaked a blank KB on the base (the chokepoint "
    "fails closed); it pins that every KB-bearing tool keeps holding"
)
@pytest.mark.parametrize("blank", BLANKS, ids=["empty", "whitespace"])
def test_every_kb_tool_treats_a_blank_kb_as_no_kb(w, blank, monkeypatch):
    p = w.principals["local_user"]
    readable = set(p.readable_kbs)
    writable = {READABLE}  # a scoped writer, as for REST
    failures = []
    for tool in kb_bearing_mcp_tool_names(w.mcp_server):
        where = f"{tool} kb={blank!r}"
        a = _mcp_answer(w, tool, readable, writable, 0, monkeypatch, blank)
        b = _mcp_answer(w, tool, readable, writable, 1, monkeypatch, blank)
        try:
            _assert_no_private_content(where, a)
        except AssertionError as exc:
            failures.append(str(exc))
            continue
        if _canonical(a, 0) != _canonical(b, 1):
            failures.append(f"{where}: private id answers {a[:200]} but a missing id {b[:200]}")
    assert not failures, "\n".join(failures)


def test_private_rows_survive_the_sweep(w):
    """The sweep's writes named private ids with a blank KB; none landed."""
    svc = KBService(w.config, w.db)
    entry = svc.get_entry(PRIVATE_ENTRY, kb_name=PRIVATE, readable_kbs=None)
    assert entry is not None and "zebra behind the wall" in (entry.get("body") or "")
    assert svc.get_entry(PRIVATE_COLLECTION, kb_name=PRIVATE, readable_kbs=None) is not None


# -- blank means "no KB named", read the same way everywhere ------------------


@pytest.mark.parametrize("blank", BLANKS, ids=["empty", "whitespace"])
def test_blank_names_no_kb_in_the_resolver_the_chokepoint_and_the_lookups(w, blank):
    """`access_policy.named_kb` is the one reading of a blank name, used by
    the REST resolver, the MCP chokepoint and the service lookups alike: a
    blank or whitespace-only name answers exactly as no name does."""
    import asyncio

    from starlette.requests import Request

    from pyrite.server.api import _resolve_kb_names
    from pyrite.server.mcp_server import _kbs_named_in
    from pyrite.services.access_policy import named_kb
    from pyrite.services.task_service import TaskService
    from tests.characterization.world import READABLE_ENTRY

    assert named_kb(blank) is None

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/entries",
        "query_string": f"kb={blank}".encode(),
        "headers": [],
        "path_params": {},
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    assert asyncio.run(_resolve_kb_names(Request(scope, receive))) == []
    assert _kbs_named_in({"kb_name": blank, "kb": [blank]}) == []

    readable = set(w.principals["local_user"].readable_kbs)
    svc = KBService(w.config, w.db)
    entry = svc.get_entry(READABLE_ENTRY, kb_name=blank, readable_kbs=readable)
    assert entry is not None and entry["kb_name"] == READABLE
    assert svc.get_entry(PRIVATE_ENTRY, kb_name=blank, readable_kbs=readable) is None

    tasks = TaskService(w.config, w.db)
    assert tasks.get_task("blank-kb-task", blank, readable_kbs=readable) is not None
    assert tasks.get_task("blank-kb-task", blank, readable_kbs=set()) is None


def test_auto_tag_vocabulary_holds_only_readable_tags(w, writer, echo_llm):
    """With a readable entry and a blank KB the route reaches its tag
    vocabulary, which goes to the LLM provider: only readable KBs' tags."""
    from tests.characterization.world import READABLE_ENTRY

    echo_llm.prompts.clear()
    w.release_idle_connections()
    r = w.client.post(
        "/api/ai/auto-tag",
        json={"entry_id": READABLE_ENTRY, "kb_name": ""},
        cookies=writer,
    )
    assert r.status_code == 200, r.text
    assert echo_llm.prompts, "the route did not reach the LLM"
    assert not [p for p in echo_llm.prompts if PRIVATE_TAG in p]
