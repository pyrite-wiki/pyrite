"""An unqualified entry id must identify one readable KB or be refused (#528).

The property, on every surface that resolves an id: with no KB named (absent
or blank), an id held by more than one KB the caller can read is refused with
``AMBIGUOUS`` and ``candidate_kbs``, never answered by config order. A KB the
caller cannot read is never a candidate and never the cause of a refusal
(ADR-0037 section 4). One readable match still resolves; a named KB still
resolves.

One seeded world (``tests/characterization/world.py``), no service mocks. The
id holds in ``READABLE``, ``PRIVATE`` and ``READ_ONLY``; a scoped caller reads
``READABLE`` and ``READ_ONLY``. The surface lists are walked from the code
(MCP tool schemas and prompts, REST routes, the Typer tree), and each found
surface is a case below or a named exclusion with its reason: a surface added
later fails ``test_every_*_surface_is_a_case_or_excluded`` until it is judged.
"""

from __future__ import annotations

import json

import pytest
import typer
from typer.testing import CliRunner

from pyrite.exceptions import PyriteError
from pyrite.read_cli import app as read_app
from pyrite.services.kb_service import KBService
from pyrite.services.task_service import TaskService
from tests.characterization.world import PRIVATE, READ_ONLY, READABLE, build_world

DUPLICATE = "duplicate-id-528"
HIDDEN_TWIN = "hidden-twin-528"
DUP_TASK = "duplicate-task-528"
DUP_COLLECTION = "duplicate-collection-528"

#: What a scoped caller (``local_user``) reads, in config order.
SCOPED = [READABLE, READ_ONLY]
#: What the local CLI (unscoped) reads that holds the duplicates, in config order.
UNSCOPED_CANDIDATES = [READABLE, PRIVATE, READ_ONLY]


def _write(world, kb: str, entry_id: str, entry_type: str, title: str, extra: str = "") -> None:
    (world.tmpdir / kb / f"{entry_id}.md").write_text(
        f"---\nid: {entry_id}\ntype: {entry_type}\ntitle: {title}\n{extra}---\n\n{kb} body\n"
    )


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    result = build_world(tmp_path_factory, label="ambiguous-id-528")
    svc = KBService(result.config, result.db)
    for kb in (READABLE, PRIVATE):
        svc.create_entry(kb, HIDDEN_TWIN, f"{kb} twin", "note", kb)
        svc.create_entry(kb, DUPLICATE, f"{kb} duplicate", "note", kb)
        svc.create_entry(kb, DUP_TASK, f"{kb} task", "task", kb)
        svc.create_entry(
            kb,
            DUP_COLLECTION,
            f"{kb} collection",
            "collection",
            kb,
            metadata={"source_type": "folder"},
        )
    # The read-only KB refuses writes: it is populated by indexing files.
    _write(result, READ_ONLY, DUPLICATE, "note", "Read-only duplicate")
    _write(result, READ_ONLY, DUP_TASK, "task", "Read-only task", "status: open\n")
    _write(
        result,
        READ_ONLY,
        DUP_COLLECTION,
        "collection",
        "Read-only collection",
        "source_type: folder\n",
    )
    result.index_worker.submit_sync(READ_ONLY)
    result.index_worker.wait_for_idle(timeout=10)
    try:
        yield result
    finally:
        result.close()


def _assert_ambiguous(body: dict, candidates: list[str], where: str) -> None:
    assert body.get("error_code") == "AMBIGUOUS", f"{where}: {body}"
    assert body["candidate_kbs"] == candidates, f"{where}: {body}"
    if PRIVATE not in candidates:
        assert PRIVATE not in json.dumps(body), f"{where} named the private KB: {body}"


# ---------------------------------------------------------------------------
# The service (the raise point) and the task read beside it
# ---------------------------------------------------------------------------


def test_service_refuses_two_readable_matches_without_exposing_private(world):
    svc = KBService(world.config, world.db)
    readable = {READABLE, READ_ONLY}
    with pytest.raises(PyriteError) as raised:
        svc.get_entry(DUPLICATE, readable_kbs=readable)
    assert raised.value.error_code == "AMBIGUOUS"
    assert raised.value.candidate_kbs == [READABLE, READ_ONLY]
    assert PRIVATE not in str(raised.value)


@pytest.mark.control(reason="Zero and one readable match retain their existing results")
def test_service_zero_one_and_hidden_twin(world):
    svc = KBService(world.config, world.db)
    readable = {READABLE, READ_ONLY}
    assert svc.get_entry("absent-528", readable_kbs=readable) is None
    assert svc.get_entry(HIDDEN_TWIN, readable_kbs={READABLE})["kb_name"] == READABLE
    assert svc.get_entry(DUPLICATE, readable_kbs={READ_ONLY})["kb_name"] == READ_ONLY


def test_service_blank_kb_is_unqualified_and_named_kb_resolves(world):
    svc = KBService(world.config, world.db)
    with pytest.raises(PyriteError):
        svc.get_entry(DUPLICATE, kb_name=" ", readable_kbs={READABLE, READ_ONLY})
    assert (
        svc.get_entry(DUPLICATE, kb_name=READABLE, readable_kbs={READABLE, READ_ONLY})["kb_name"]
        == READABLE
    )


def test_task_read_refuses_two_readable_matches(world):
    """``TaskService.get_task`` ended in ``LIMIT 1``: `task get`, `task status`
    and MCP's task tools answered from whichever KB SQLite returned first."""
    tasks = TaskService(world.config, world.db)
    with pytest.raises(PyriteError) as raised:
        tasks.get_task(DUP_TASK, readable_kbs={READABLE, READ_ONLY})
    assert raised.value.error_code == "AMBIGUOUS"
    assert raised.value.candidate_kbs == [READABLE, READ_ONLY]
    with pytest.raises(PyriteError):
        tasks.get_task(DUP_TASK, " ", readable_kbs={READABLE, READ_ONLY})


@pytest.mark.control(reason="one readable match, a named KB and a private twin keep answering")
def test_task_read_one_match_and_named_kb_resolve(world):
    tasks = TaskService(world.config, world.db)
    assert tasks.get_task(DUP_TASK, readable_kbs={READABLE})["kb_name"] == READABLE
    assert tasks.get_task(DUP_TASK, READ_ONLY, readable_kbs=None)["kb_name"] == READ_ONLY
    assert tasks.get_task("absent-task-528", readable_kbs=None) is None


# ---------------------------------------------------------------------------
# MCP: tools, prompts, resources
# ---------------------------------------------------------------------------

#: An argument that names an entry (or task) by id.
ID_ARGUMENTS = {"entry_id", "task_id", "collection_id", "entry_a", "entry_b"}

#: Every MCP tool that takes an id and does not require a KB, with the call
#: that drives it without one.
MCP_TOOL_CASES = {
    "kb_get": {"entry_id": DUPLICATE},
    "kb_read_body": {"entry_id": DUPLICATE},
    "task_status": {"task_id": DUP_TASK},
    "task_subtree": {"task_id": DUP_TASK},
    "task_ancestors": {"task_id": DUP_TASK},
    "task_blocked_by": {"task_id": DUP_TASK},
    "task_critical_path": {"task_id": DUP_TASK},
}
MCP_TOOL_EXCLUDED = {
    "kb_qa_validate": "without kb_name it validates every readable KB and never reads entry_id",
    "investigation_money_flow": "plugin query of its own (_readable_kb), not get_entry: #528's "
    "groom puts extension lookups out of scope; #911",
    "investigation_network": "as investigation_money_flow",
    "investigation_ownership_chain": "as investigation_money_flow",
}


def _id_tools_with_optional_kb(server) -> set[str]:
    from pyrite.server.mcp_server import KB_ARGUMENT_NAMES

    out = set()
    for name, meta in server.tools.items():
        schema = meta.get("inputSchema") or {}
        props = set(schema.get("properties") or {})
        required = set(schema.get("required") or [])
        if props & ID_ARGUMENTS and not (props & set(KB_ARGUMENT_NAMES) & required):
            out.add(name)
    return out


@pytest.mark.control(
    reason="structural inventory: fails on an id-taking surface added later and not judged, not on this fix"
)
def test_every_mcp_id_tool_is_a_case_or_excluded(world):
    found = _id_tools_with_optional_kb(world.mcp_server)
    assert found == set(MCP_TOOL_CASES) | set(MCP_TOOL_EXCLUDED)


def _mcp(world, readable: set[str] | None, kind: str, *args) -> dict:
    """One MCP request over a real session (the SDK's in-memory transport, as
    `tests/test_mcp_resources_session.py` drives it), answered as the wire
    carries it: a tool's JSON result; a prompt's or resource's result, or the
    JSON-RPC error's ``data`` when it refuses (``{"error": message}`` if the
    error carried none)."""
    import asyncio

    from mcp import McpError
    from mcp.shared.memory import create_connected_server_and_client_session
    from pyrite.plugins import PluginContext, get_registry

    get_registry().set_context(PluginContext(config=world.config, db=world.db))
    sdk = world.mcp_server.build_sdk_server(
        client_id="ambiguous-528", client_kind="local", readable_kbs=readable
    )

    async def run():
        async with create_connected_server_and_client_session(sdk) as session:
            try:
                if kind == "tool":
                    result = await session.call_tool(*args)
                    return json.loads(result.content[0].text)
                if kind == "prompt":
                    result = await session.get_prompt(*args)
                    return {
                        "messages": [{"content": {"text": m.content.text}} for m in result.messages]
                    }
                result = await session.read_resource(*args)
                return {"contents": [{"text": c.text} for c in result.contents]}
            except McpError as err:
                return err.error.data or {"error": err.error.message}

    return asyncio.run(run())


@pytest.mark.parametrize("tool", sorted(MCP_TOOL_CASES))
def test_mcp_tool_refuses_an_ambiguous_id(world, tool):
    result = _mcp(world, {READABLE, READ_ONLY}, "tool", tool, MCP_TOOL_CASES[tool])
    _assert_ambiguous(result, SCOPED, f"MCP {tool}")


@pytest.mark.parametrize("tool", sorted(MCP_TOOL_CASES))
def test_mcp_tool_treats_a_blank_kb_name_as_none(world, tool):
    args = {**MCP_TOOL_CASES[tool], "kb_name": "  "}
    result = _mcp(world, {READABLE, READ_ONLY}, "tool", tool, args)
    _assert_ambiguous(result, SCOPED, f"MCP {tool} kb_name='  '")


@pytest.mark.control(reason="a named KB and a single readable match resolve as before")
@pytest.mark.parametrize("tool", sorted(MCP_TOOL_CASES))
def test_mcp_tool_resolves_with_a_named_kb(world, tool):
    args = {**MCP_TOOL_CASES[tool], "kb_name": READABLE}
    result = _mcp(world, {READABLE, READ_ONLY}, "tool", tool, args)
    assert "error_code" not in result, result
    single = _mcp(world, {READABLE}, "tool", tool, MCP_TOOL_CASES[tool])
    assert "error_code" not in single, single


def test_mcp_get_reports_ambiguity_and_scopes_candidates(world):
    result = _mcp(world, {READABLE, READ_ONLY}, "tool", "kb_get", {"entry_id": DUPLICATE})
    assert result["error_code"] == "AMBIGUOUS"
    assert READABLE in result["error"] and READ_ONLY in result["error"]
    assert PRIVATE not in json.dumps(result)
    unique = _mcp(world, {READABLE}, "tool", "kb_get", {"entry_id": HIDDEN_TWIN})
    assert unique["entry"]["kb_name"] == READABLE


#: Every prompt that takes an id, with the arguments that name no KB, and the
#: arguments that name one for each id.
MCP_PROMPT_CASES = {
    "summarize_entry": ({"entry_id": DUPLICATE}, {"kb_name": READABLE}),
    "find_connections": (
        {"entry_a": DUPLICATE, "entry_b": HIDDEN_TWIN},
        {"kb_a": READ_ONLY, "kb_b": READABLE},
    ),
}


@pytest.mark.control(
    reason="structural inventory: fails on an id-taking surface added later and not judged, not on this fix"
)
def test_every_mcp_id_prompt_is_a_case(world):
    found = {
        name
        for name, prompt in world.mcp_server.prompts.items()
        if {a["name"] for a in prompt.get("arguments", [])} & ID_ARGUMENTS
    }
    assert found == set(MCP_PROMPT_CASES)


@pytest.mark.parametrize("prompt", sorted(MCP_PROMPT_CASES))
def test_mcp_prompt_refuses_an_ambiguous_id_and_can_name_the_kb(world, prompt):
    args, naming = MCP_PROMPT_CASES[prompt]
    refused = _mcp(world, {READABLE, READ_ONLY}, "prompt", prompt, args)
    _assert_ambiguous(refused, SCOPED, f"prompt {prompt}")
    # The refusal can be complied with: the prompt takes the KB.
    declared = {a["name"] for a in world.mcp_server.prompts[prompt]["arguments"]}
    assert set(naming) <= declared, f"{prompt} cannot name the KB: {declared}"
    answered = _mcp(world, {READABLE, READ_ONLY}, "prompt", prompt, {**args, **naming})
    assert "error_code" not in answered, answered
    text = answered["messages"][0]["content"]["text"]
    assert f"{READABLE} duplicate" in text or "Read-only duplicate" in text


def test_find_connections_kb_argument_is_scoped(world):
    """A KB named in the new kb_a/kb_b arguments is refused like any other
    KB argument when the caller cannot read it (KB_ARGUMENT_NAMES)."""
    refused = _mcp(
        world,
        {READABLE, READ_ONLY},
        "prompt",
        "find_connections",
        {"entry_a": DUPLICATE, "entry_b": HIDDEN_TWIN, "kb_a": PRIVATE},
    )
    assert refused["error_code"] == "KB_NOT_FOUND"
    assert "behind" not in json.dumps(refused)


def test_mcp_entry_resource_refuses_and_a_kb_resource_resolves(world):
    from pyrite.server.mcp_server import URI_SCHEME

    refused = _mcp(world, {READABLE, READ_ONLY}, "resource", f"{URI_SCHEME}entries/{DUPLICATE}")
    _assert_ambiguous(refused, SCOPED, "resource entries/{id}")
    named = _mcp(
        world, {READABLE, READ_ONLY}, "resource", f"{URI_SCHEME}kbs/{READ_ONLY}/entries/{DUPLICATE}"
    )
    assert json.loads(named["contents"][0]["text"])["kb_name"] == READ_ONLY
    hidden = _mcp(
        world, {READABLE, READ_ONLY}, "resource", f"{URI_SCHEME}kbs/{PRIVATE}/entries/{DUPLICATE}"
    )
    assert hidden["error_code"] == "KB_NOT_FOUND"
    unscoped_unknown = _mcp(world, None, "resource", f"{URI_SCHEME}kbs/no-such-kb/entries/x")
    assert unscoped_unknown["error_code"] == "KB_NOT_FOUND"
    templates = {t["uriTemplate"] for t in world.mcp_server.resource_templates}
    assert f"{URI_SCHEME}kbs/{{name}}/entries/{{id}}" in templates


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------

#: Path parameters that name an entry.
ID_PATH_PARAMS = ("{entry_id}", "{collection_id}", "{task_id}")

#: Query parameters and body fields (besides `entry_id`) that carry an entry id
#: or a link target, so a route taking its id in the query is inventoried too.
ID_QUERY_PARAMS = ("entry_id", "target", "center", "parent")
ID_BODY_FIELDS = ("entry_id", "targets")

#: Every REST route that takes an id and a `kb`, driven with a blank `kb`.
REST_CASES = {
    ("GET", "/api/entries/resolve"): f"/api/entries/resolve?target={DUPLICATE}&kb=",
    ("GET", "/api/links/discover-neighbors"): (
        f"/api/links/discover-neighbors?entry_id={DUPLICATE}&kb="
    ),
    ("GET", "/api/entries/{entry_id}"): f"/api/entries/{DUPLICATE}?kb=",
    ("GET", "/api/entries/{entry_id}/blocks"): f"/api/entries/{DUPLICATE}/blocks?kb=",
    ("GET", "/api/collections/{collection_id}"): f"/api/collections/{DUP_COLLECTION}?kb=",
    ("GET", "/api/collections/{collection_id}/entries"): (
        f"/api/collections/{DUP_COLLECTION}/entries?kb="
    ),
}
_NAMED_ONLY = (
    "reads only the KB it is given: a blank kb is a KB named '' holding nothing, so "
    "the answer is a miss, never a pick (test_rest_named_kb_routes_never_pick_for_a_blank_kb)"
)
_QUERY_NAMED = (
    "kb required (Query(...)): reads the rows of that (entry, kb) pair only, "
    "resolves no id across KBs"
)
REST_EXCLUDED = {
    ("POST", "/api/entries/resolve-batch"): "answers whether each target exists in any "
    "readable KB (a bool per target); picks and returns no entry",
    ("GET", "/api/reviews"): _QUERY_NAMED,
    ("GET", "/api/reviews/latest"): _QUERY_NAMED,
    ("GET", "/api/reviews/status"): _QUERY_NAMED,
    ("GET", "/api/graph"): "`center` is read only with `center_kb` (no centre without it): "
    "the walk starts from a named (id, kb) pair or from none",
    ("GET", "/api/tasks"): "`parent` filters rows by a parent id; it selects no entry to read",
    ("DELETE", "/api/entries/{entry_id}"): "kb required and checked by the write guard "
    "(requires_kb_tier on the named KB); deletes only in that KB",
    ("DELETE", "/api/starred/{entry_id}"): "removes the caller's own star rows; resolves no entry",
    ("POST", "/api/starred"): "records the (entry_id, kb_name) pair it is given; resolves no entry",
    ("POST", "/api/reviews"): "kb_name required by the write guard; records the pair it is given",
    ("GET", "/api/entries/{entry_id}/versions"): _NAMED_ONLY,
    ("GET", "/api/entries/{entry_id}/versions/{commit_hash}"): _NAMED_ONLY,
    ("GET", "/api/qa/validate/{entry_id}"): _NAMED_ONLY,
    ("POST", "/api/tasks/{task_id}/claim"): "kb required by the write guard; claims in "
    "the named KB only",
    ("PUT", "/api/entries/{entry_id}"): "kb in the body, required by the write guard",
    ("PATCH", "/api/entries/{entry_id}"): "as PUT",
}
#: Body-addressed routes (an `entry_id` and a KB in the JSON body).
REST_BODY_CASES = {
    ("POST", "/api/ai/summarize"): {"entry_id": DUPLICATE, "kb_name": " "},
    ("POST", "/api/ai/auto-tag"): {"entry_id": DUPLICATE, "kb_name": " "},
    ("POST", "/api/ai/suggest-links"): {"entry_id": DUPLICATE, "kb_name": " "},
    ("POST", "/api/ai/chat"): {
        "messages": [{"role": "user", "content": "hi"}],
        "entry_id": DUPLICATE,
        "kb": " ",
    },
}


def _id_routes(app) -> set[tuple[str, str]]:
    from fastapi.routing import APIRoute

    from tests._surface_inventory import _walk_routes

    out = set()
    for path, route in _walk_routes(app.routes):
        if not isinstance(route, APIRoute):
            continue
        queries = {p.name for p in route.dependant.query_params}
        bodies = route.dependant.body_params
        body_fields: set[str] = set()
        for b in bodies:
            model = getattr(b.field_info, "annotation", None) or getattr(b, "type_", None)
            body_fields |= set(getattr(model, "model_fields", {}) or {})
        by_path = any(p in path for p in ID_PATH_PARAMS) and (
            "kb" in queries or path.startswith("/api/entries/{entry_id}")
        )
        by_body = bool(set(ID_BODY_FIELDS) & body_fields) and ({"kb", "kb_name"} & body_fields)
        by_query = bool(set(ID_QUERY_PARAMS) & queries) and bool(
            {"kb", "center_kb", "target_kb"} & queries or path == "/api/tasks"
        )
        if by_path or by_body or by_query:
            for method in route.methods or ():
                if method not in ("HEAD", "OPTIONS"):
                    out.add((method, path))
    return out


@pytest.mark.control(
    reason="structural inventory: fails on an id-taking surface added later and not judged, not on this fix"
)
def test_every_rest_id_route_is_a_case_or_excluded(world):
    found = _id_routes(world.app)
    known = set(REST_CASES) | set(REST_EXCLUDED) | set(REST_BODY_CASES)
    assert found - known == set(), f"unjudged id-taking routes: {sorted(found - known)}"
    assert known - found == set(), f"stale cases: {sorted(known - found)}"


def _rest(world, method, url, **kwargs):
    principal = world.principals["local_user"]
    world.release_idle_connections()
    return world.client.request(
        method, url, headers=principal.rest_headers, cookies=principal.rest_cookies, **kwargs
    )


@pytest.mark.parametrize("case", sorted(REST_CASES), ids=lambda c: f"{c[0]} {c[1]}")
def test_rest_route_refuses_an_ambiguous_id_with_a_blank_kb(world, case):
    method, _ = case
    response = _rest(world, method, REST_CASES[case])
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "AMBIGUOUS"
    assert detail["candidate_kbs"] == SCOPED
    assert PRIVATE not in response.text


@pytest.mark.control(reason="a named kb resolves as before")
@pytest.mark.parametrize("case", sorted(REST_CASES), ids=lambda c: f"{c[0]} {c[1]}")
def test_rest_route_resolves_with_a_named_kb(world, case):
    method, _ = case
    response = _rest(world, method, REST_CASES[case] + READABLE)
    assert response.status_code == 200, response.text


@pytest.mark.control(reason="pins an exclusion's reason: these routes never resolved across KBs")
@pytest.mark.parametrize(
    "path", [p for (_, p), why in REST_EXCLUDED.items() if why == _NAMED_ONLY and "commit" not in p]
)
def test_rest_named_kb_routes_never_pick_for_a_blank_kb(world, path):
    """The exclusions that read only the KB they are given: with a blank kb
    none answers from a KB it chose (no title of any copy in the answer)."""
    response = _rest(world, "GET", path.replace("{entry_id}", DUPLICATE) + "?kb=")
    assert response.status_code < 500, response.text
    for title in (f"{READABLE} duplicate", "Read-only duplicate", f"{PRIVATE} duplicate"):
        assert title not in response.text, response.text


class _EchoLLM:
    """A configured LLM that echoes: the AI routes check configuration before
    they read the entry, so the read is only reached with one."""

    def status(self):
        return {"configured": True, "provider": "echo", "model": "echo"}

    def with_user_key(self, **_kwargs):
        return self

    async def complete(self, prompt, system=None, **_kwargs):
        return "tags: a"


@pytest.fixture(scope="module")
def ai_writer(world):
    from pyrite.server.api import get_llm_service
    from pyrite.services.auth_service import AuthService
    from tests.auth_seed import SESSION_COOKIE, seed_user, sign_in

    name = "ambiguous-528-writer"
    row = seed_user(world.db, name, role="write")
    for kb in (READABLE, READ_ONLY):
        AuthService(world.db, world.config.settings.auth).grant_kb_permission(
            row["id"], kb, "write", granted_by=row["id"]
        )
    world.app.dependency_overrides[get_llm_service] = lambda: _EchoLLM()
    try:
        yield {SESSION_COOKIE: sign_in(world.app, name)}
    finally:
        world.app.dependency_overrides.pop(get_llm_service, None)


@pytest.mark.parametrize("case", sorted(REST_BODY_CASES), ids=lambda c: c[1])
def test_rest_ai_route_refuses_an_ambiguous_id_with_a_blank_kb(world, ai_writer, case):
    method, path = case
    world.release_idle_connections()
    response = world.client.request(method, path, cookies=ai_writer, json=REST_BODY_CASES[case])
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "AMBIGUOUS"
    assert PRIVATE not in detail["candidate_kbs"]
    assert PRIVATE not in response.text


def test_rest_resolve_with_a_kb_prefix_or_param_picks_that_kb(world):
    """`![[kb-b:dup]]` must resolve to kb-b: the prefix names the KB (#912)."""
    by_prefix = _rest(world, "GET", f"/api/entries/resolve?target={READABLE}:{DUPLICATE}")
    by_param = _rest(world, "GET", f"/api/entries/resolve?target={DUPLICATE}&kb={READ_ONLY}")
    assert by_prefix.json()["entry"]["kb_name"] == READABLE, by_prefix.text
    assert by_param.json()["entry"]["kb_name"] == READ_ONLY, by_param.text


def test_rest_get_without_kb_refuses_an_ambiguous_id(world):
    """The web client's entry page sends no `kb` only for an id it could not
    place; REST answers it as CLI and MCP do, never from the first KB (#910)."""
    result = _rest(world, "GET", f"/api/entries/{DUPLICATE}")
    assert result.status_code == 409, result.text
    detail = result.json()["detail"]
    assert detail["code"] == "AMBIGUOUS"
    assert detail["candidate_kbs"] == SCOPED
    assert PRIVATE not in result.text


# ---------------------------------------------------------------------------
# CLI: `pyrite` and `pyrite-read`, through the real root and a real config file
# ---------------------------------------------------------------------------

#: Every command with an id argument and an optional KB, driven without one;
#: and commands whose KB is required, driven with a blank one.
CLI_CASES = {
    "pyrite get": ["get", DUPLICATE, "--format", "json"],
    "pyrite task get": ["task", "get", DUP_TASK, "--format", "json"],
    "pyrite task status": ["task", "status", DUP_TASK, "--format", "json"],
    "pyrite-read get": ["get", DUPLICATE, "--format", "json"],
}
CLI_BLANK_KB_CASES = {
    "pyrite update": ["update", DUPLICATE, "-k", "", "--title", "x"],
    "pyrite links suggest": ["links", "suggest", DUPLICATE, "-k", ""],
    "pyrite links discover": ["links", "discover", DUPLICATE, "-k", ""],
}
_EXTENSION = (
    "extension command resolving through its own query, not get_entry: out of scope "
    "per #528's groom; #911"
)
CLI_EXCLUDED = {
    "pyrite export collection": "without -k it loads from no KB (`kb or ''`): a miss, never a pick",
    "pyrite wiki review": _EXTENSION,
    "pyrite wiki quality": _EXTENSION,
    "pyrite social vote": _EXTENSION,
    "pyrite sw epic": _EXTENSION,
    "pyrite sw claim": _EXTENSION,
    "pyrite sw submit": _EXTENSION,
    "pyrite sw transition": _EXTENSION,
    "pyrite sw review": _EXTENSION,
    "pyrite sw log": _EXTENSION,
    "pyrite sw check-ready": _EXTENSION,
}


def _walk(cmd, path):
    if hasattr(cmd, "list_commands"):
        ctx = cmd.make_context(path[-1], [], resilient_parsing=True)
        for name in cmd.list_commands(ctx):
            yield from _walk(cmd.get_command(ctx, name), [*path, name])
    else:
        yield " ".join(path), cmd


def _id_commands_with_optional_kb() -> set[str]:
    from pyrite.cli import app

    out = set()
    for prog, root in (("pyrite", app), ("pyrite-read", read_app)):
        for name, cmd in _walk(typer.main.get_command(root), [prog]):
            params = {p.name: p for p in cmd.params}
            has_id = any(
                p.param_type_name == "argument" and n.endswith("_id") for n, p in params.items()
            )
            kb = [params[k] for k in ("kb_name", "kb") if k in params]
            if has_id and kb and not kb[0].required:
                out.add(name)
    return out


@pytest.mark.control(
    reason="structural inventory: fails on an id-taking surface added later and not judged, not on this fix"
)
def test_every_cli_id_command_is_a_case_or_excluded():
    found = _id_commands_with_optional_kb()
    unjudged = found - set(CLI_CASES) - set(CLI_EXCLUDED)
    assert not unjudged, f"id-taking commands with an optional KB, not judged: {sorted(unjudged)}"
    # Core cases must exist. Extension exclusions are not required to: an
    # extension's group can be absent from `pyrite.cli.app` in a worker that
    # imported the extension's CLI module first (#900), which says nothing
    # about this property.
    assert set(CLI_CASES) <= found | {"pyrite-read get"}, sorted(set(CLI_CASES) - found)


@pytest.fixture
def cli_world(world, tmp_path, monkeypatch):
    """Point the real CLI at the world through a config file, as a user would."""
    import pyrite.config as config_module
    from pyrite.utils.yaml import dump_yaml_file

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.yaml"
    dump_yaml_file(world.config.to_dict(), config_file)
    monkeypatch.setenv("PYRITE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("PYRITE_DATA_DIR", str(world.tmpdir))
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)
    return world


def _cli(name: str, args: list[str]):
    from pyrite.cli import app

    root = read_app if name.startswith("pyrite-read") else app
    return CliRunner().invoke(root, args)


@pytest.mark.parametrize("name", sorted(CLI_CASES) + sorted(CLI_BLANK_KB_CASES))
def test_cli_command_refuses_an_ambiguous_id(cli_world, name):
    args = CLI_CASES.get(name) or CLI_BLANK_KB_CASES[name]
    result = _cli(name, args)
    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
    _assert_ambiguous(json.loads(result.stdout), UNSCOPED_CANDIDATES, name)


@pytest.mark.control(reason="-k names the KB and the command answers from it, as before")
@pytest.mark.parametrize("name", sorted(CLI_CASES))
def test_cli_command_resolves_with_k(cli_world, name):
    result = _cli(name, [*CLI_CASES[name], "-k", READ_ONLY])
    assert result.exit_code == 0, result.output
    assert READ_ONLY in result.stdout


def test_cli_get_rich_names_the_kbs_and_the_way_out(cli_world):
    result = _cli("pyrite get", ["get", DUPLICATE, "--format", "rich"])
    assert result.exit_code == 1
    assert "AMBIGUOUS" in result.output
    assert all(kb in result.output for kb in UNSCOPED_CANDIDATES)
    assert "-k" in result.output
    read = _cli("pyrite-read get", ["get", DUPLICATE, "--format", "rich"])
    assert read.exit_code == 1
    assert all(kb in read.output for kb in UNSCOPED_CANDIDATES)


def test_read_only_cli_get_reports_ambiguity(cli_world):
    result = CliRunner().invoke(read_app, ["get", DUPLICATE, "--format", "json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "AMBIGUOUS"
    assert payload["candidate_kbs"] == UNSCOPED_CANDIDATES
    assert "-k" in payload["suggestion"]
