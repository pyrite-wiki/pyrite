"""An MCP call never succeeds with an argument key it did not read (#1758).

Maintainer decision on #1758: a key outside a tool's declared
``inputSchema.properties`` is **refused** on a write tool (any tool registered
above the read tier), before any lookup or write, and **reported** on a read
tool, which still runs and lists the key in ``ignored_arguments``. One check,
at the dispatcher chokepoint (``PyriteMCPServer._dispatch_tool_checked``),
driven by the tool's own schema and registered tier, so a new tool -- core or
plugin -- gets it by being registered.

Every tool is driven through the real ``_dispatch_tool`` on an admin-tier
server with every in-tree plugin loaded. ``test_every_tool_is_classified``
fails for a tool that neither refuses nor reports an unknown key and is not
named in ``FREE_FORM``.

Expected values here are written by hand from the decision, never derived
from the dispatcher's own accepted-key computation.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams

from tests._world import mcp_server as _make_mcp_server
from tests.mcp_seed import STANDARD_KB_DEFS
from tests.mcp_seed import populate_events_and_person

KB = "test-events"
UNKNOWN = "zz_not_a_parameter_1758"

#: Tools that take any top-level key, each with the reason. A tool lands here
#: only by declaring ``additionalProperties`` in its ``inputSchema``.
FREE_FORM: dict[str, str] = {
    "kb_create": "a type's own fields are top-level arguments (#994 decision A); "
    "they depend on entry_type, so the schema cannot list them",
    "kb_update": "stores an undeclared top-level key as a custom field, as the CLI and REST do",
}

#: Values the schema alone cannot make sensible (as the dispatch smoke test).
_OVERRIDES = {"kb_name": KB, "kb": KB}


def _minimal_value(name: str, spec: dict):
    if name in _OVERRIDES:
        return _OVERRIDES[name]
    if "enum" in spec:
        return spec["enum"][0]
    if "default" in spec:
        return spec["default"]
    kind = spec.get("type", "string")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "string")
    return {
        "string": "probe-1758",
        "integer": 1,
        "number": 1,
        "boolean": False,
        "array": [],
        "object": {},
    }.get(kind, "probe-1758")


def _minimal_arguments(schema: dict) -> dict:
    props = schema.get("properties", {})
    return {n: _minimal_value(n, props.get(n, {})) for n in schema.get("required", [])}


def _files(server) -> dict[str, bytes]:
    """Every file under every KB, path -> bytes."""
    out: dict[str, bytes] = {}
    for kb in server.config.knowledge_bases:
        for p in sorted(Path(kb.path).rglob("*")):
            if p.is_file():
                out[str(p)] = p.read_bytes()
    return out


def _call(server, name, args):
    return server._dispatch_tool(name, args, client_id="stdio", client_kind="local")


@pytest.fixture(scope="module")
def env():
    with _make_mcp_server(
        STANDARD_KB_DEFS, tier="admin", extra_setup=populate_events_and_person
    ) as e:
        yield e


@pytest.fixture
def server(env):
    return env["server"]


def _is_write(server, name) -> bool:
    return server._tool_tiers[name] != "read"


def _assert_refused_naming(result: dict, *keys: str) -> None:
    assert result.get("error_code") == "VALIDATION_FAILED", result
    assert result.get("retryable") is False, result
    for key in keys:
        assert key in result["error"], result
    rows = {row["field"]: row for row in result.get("errors", [])}
    assert set(rows) == set(keys), result
    assert all(row["rule"] == "unknown_field" for row in rows.values()), result


# ---------------------------------------------------------------------------
# Every registered tool, by iteration
# ---------------------------------------------------------------------------


@pytest.mark.control(reason="guards against a vacuous pass")
def test_the_surface_is_the_full_one(server):
    """Guard against a vacuous pass: core write and read tools and plugin tools."""
    assert len(server.tools) > 80, len(server.tools)
    assert {"task_create", "task_claim", "kb_search", "sw_claim"} <= set(server.tools)


def test_every_tool_is_classified(server):
    """Each tool refuses (write), reports (read) or is declared free-form."""
    wrong: list[str] = []
    for name, meta in sorted(server.tools.items()):
        if name in FREE_FORM:
            continue
        args = {**_minimal_arguments(meta["inputSchema"]), UNKNOWN: "x"}
        before = _files(server) if _is_write(server, name) else None
        result = _call(server, name, args)
        if not isinstance(result, dict):
            wrong.append(f"{name}: result is {type(result).__name__}, not a dict")
            continue
        if _is_write(server, name):
            rows = [r.get("field") for r in result.get("errors", [])]
            if result.get("error_code") != "VALIDATION_FAILED" or rows != [UNKNOWN]:
                wrong.append(f"{name} (write): not refused for {UNKNOWN}: {result}")
            elif _files(server) != before:
                wrong.append(f"{name} (write): refused but a KB file changed")
        elif result.get("ignored_arguments") != [UNKNOWN]:
            wrong.append(f"{name} (read): {UNKNOWN} not reported: {str(result)[:300]}")
    assert not wrong, "\n  ".join(["unknown key not refused or reported:", *wrong])


def test_free_form_table_is_what_the_schemas_declare(server):
    """FREE_FORM names exactly the tools whose schema takes any top-level key."""
    declared = {
        name
        for name, meta in server.tools.items()
        if meta["inputSchema"].get("additionalProperties") not in (None, False)
    }
    assert declared == set(FREE_FORM)


# ---------------------------------------------------------------------------
# The two hallway cases
# ---------------------------------------------------------------------------


def test_task_create_with_due_is_refused_and_points_at_due_date(server):
    before = _files(server)
    result = _call(
        server, "task_create", {"kb_name": KB, "title": "Hallway due 1758", "due": "2026-12-01"}
    )
    _assert_refused_naming(result, "due")
    (row,) = result["errors"]
    assert row["did_you_mean"] == ["due_date"], result
    assert "due_date" in result["suggestion"], result
    assert _files(server) == before, "a refused task_create wrote a file"
    assert "error_code" in _call(
        server, "task_status", {"kb_name": KB, "task_id": "hallway-due-1758"}
    )


def test_task_claim_with_agent_id_is_refused_and_the_task_stays_unclaimed(server):
    made = _call(server, "task_create", {"kb_name": KB, "title": "Hallway claim 1758"})
    assert made.get("created") is True, made
    before = _files(server)
    result = _call(
        server,
        "task_claim",
        {"kb_name": KB, "task_id": made["entry_id"], "assignee": "me", "agent_id": "agent:x"},
    )
    _assert_refused_naming(result, "agent_id")
    assert "assignee" in result["suggestion"], result
    assert _files(server) == before
    status = _call(server, "task_status", {"kb_name": KB, "task_id": made["entry_id"]})
    assert status["assignee"] == "" and status["status"] == "open", status


def test_every_unknown_key_is_named_at_once(server):
    result = _call(
        server, "task_create", {"kb_name": KB, "title": "T", "due": "x", "agent_id": "y"}
    )
    _assert_refused_naming(result, "agent_id", "due")


# ---------------------------------------------------------------------------
# Read tools: the call runs and reports
# ---------------------------------------------------------------------------


def test_a_misspelt_read_filter_runs_and_is_reported(server):
    result = _call(server, "kb_search", {"kb_name": KB, "query": "event", "entrytype": "event"})
    assert "error" not in result, result
    assert result["ignored_arguments"] == ["entrytype"]
    assert "results" in result


def test_a_read_refusal_also_reports_the_ignored_key(server):
    result = _call(server, "kb_get", {"kb_name": KB, "entry_id": "no-such-1758", "stray": 1})
    assert result.get("error_code") == "ENTRY_NOT_FOUND", result
    assert result["ignored_arguments"] == ["stray"]


@pytest.mark.control(reason="the report must never take a key from a read handler")
def test_a_read_call_runs_with_its_arguments_as_sent(server):
    """The report never takes a key away from a read handler.

    `task_status` reads `body_limit` though its schema does not declare it (a
    schema gap in the task entries, outside this change): the key is reported
    as undeclared, and the handler still honours it -- nothing is lost.
    """
    made = _call(
        server, "task_create", {"kb_name": KB, "title": "Body 1758", "body": "0123456789" * 5}
    )
    assert made.get("created") is True, made
    status = _call(
        server, "task_status", {"kb_name": KB, "task_id": made["entry_id"], "body_limit": 4}
    )
    assert status["body"] == "0123", status
    assert status["body_truncated"] is True, status


@pytest.mark.control(reason="known keys only behave as on dev")
def test_known_keys_only_carry_no_report(server):
    read = _call(server, "kb_search", {"kb_name": KB, "query": "event"})
    assert "ignored_arguments" not in read
    write = _call(server, "task_create", {"kb_name": KB, "title": "Known keys only 1758"})
    assert write.get("created") is True, write
    assert "ignored_arguments" not in write


# ---------------------------------------------------------------------------
# Free-form arguments are not broken
# ---------------------------------------------------------------------------


@pytest.mark.control(reason="free-form behaviour that must survive the check")
def test_kb_update_still_stores_an_undeclared_top_level_key(server):
    made = _call(server, "kb_create", {"kb_name": KB, "entry_type": "note", "title": "Free 1758"})
    assert made.get("created") is True, made
    upd = _call(
        server, "kb_update", {"kb_name": KB, "entry_id": made["entry_id"], "shoe_size": "42"}
    )
    assert "error" not in upd, upd
    got = _call(server, "kb_get", {"kb_name": KB, "entry_id": made["entry_id"]})
    assert got["entry"]["metadata"]["shoe_size"] == "42", got


@pytest.mark.control(reason="nested keys were never checked; must stay so")
def test_nested_free_form_keys_are_not_checked(server):
    """kb_create's `metadata` map takes any key; only top-level keys are checked."""
    made = _call(
        server,
        "kb_create",
        {
            "kb_name": KB,
            "entry_type": "note",
            "title": "Nested 1758",
            "metadata": {"anything_at_all": "kept"},
        },
    )
    assert made.get("created") is True, made
    got = _call(server, "kb_get", {"kb_name": KB, "entry_id": made["entry_id"]})
    assert got["entry"]["metadata"]["anything_at_all"] == "kept", got


def test_an_unknown_key_is_pointed_at_the_tools_one_free_form_map(server):
    """A key with no near miss, on a tool with one map of the caller's own
    keys, is pointed at that map: investigation_create_entity's `fields`."""
    before = _files(server)
    result = _call(
        server,
        "investigation_create_entity",
        {"kb_name": KB, "title": "Map 1758", "entity_type": "person", "jurisdiction": "x"},
    )
    _assert_refused_naming(result, "jurisdiction")
    (row,) = result["errors"]
    assert row["did_you_mean"] == ["fields"], result
    assert "fields" in result["suggestion"], result
    assert _files(server) == before


@pytest.mark.control(reason="documented kb_create behaviour that must survive the check")
def test_kb_create_takes_a_types_own_field_at_the_top_level(server):
    """kb_create is free-form at the top level: a type's own field is an
    argument (`metadata.k` is only a second spelling of `k`, #994 decision A)."""
    made = _call(
        server,
        "kb_create",
        {"kb_name": KB, "entry_type": "note", "title": "Top 1758", "shoe_size": "41"},
    )
    assert made.get("created") is True, made
    got = _call(server, "kb_get", {"kb_name": KB, "entry_id": made["entry_id"]})
    assert got["entry"]["metadata"]["shoe_size"] == "41", got


@pytest.mark.control(reason="no guess was ever made; must stay so with two maps")
def test_a_tool_with_two_free_form_maps_guesses_neither(server):
    """kb_manage takes `type_def` and `schema`: no map is offered for a stray key."""
    result = _call(server, "kb_manage", {"action": "discover", "kb_name": KB, "stray": 1})
    _assert_refused_naming(result, "stray")
    assert "did_you_mean" not in result["errors"][0], result


# ---------------------------------------------------------------------------
# Keys the dispatcher itself consumes
# ---------------------------------------------------------------------------


def _register(server, name, tier, schema_props, **extra):
    seen: dict = {}

    def handler(args):
        seen.update(args)
        return {"ok": True}

    server._register_tool(
        name,
        {
            "description": name,
            "inputSchema": {"type": "object", "properties": schema_props},
            "handler": handler,
            **extra,
        },
        tier,
    )
    return seen


@pytest.mark.control(reason="dispatcher-consumed key; must stay accepted")
def test_kb_name_is_accepted_on_a_tool_that_locates_an_entry(server):
    """`kb_name` is the dispatcher's on a `locates_entry` tool, listed or not."""
    made = _call(server, "kb_create", {"kb_name": KB, "entry_type": "note", "title": "Loc 1758"})
    name = "probe_locates_1758"
    seen = _register(
        server,
        name,
        "read",
        {"entry_id": {"type": "string"}},
        locates_entry={"id_args": ["entry_id"]},
    )
    try:
        result = _call(server, name, {"entry_id": made["entry_id"], "kb_name": KB})
        assert result == {"ok": True}, result
        assert seen["kb_name"] == KB
    finally:
        server.tools.pop(name)
        server._tool_tiers.pop(name)


@pytest.mark.control(reason="dispatcher-consumed key; must stay accepted")
def test_kb_name_is_accepted_on_a_plugin_write_tool(server):
    """A plugin write tool works in `kb_name`, so the dispatcher accepts it, listed or not."""
    name = "probe_plugin_write_1758"
    seen = _register(server, name, "write", {"title": {"type": "string"}})
    try:
        result = _call(server, name, {"title": "t", "kb_name": KB})
        assert result == {"ok": True}, result
        assert seen["kb_name"] == KB
    finally:
        server.tools.pop(name)
        server._tool_tiers.pop(name)


@pytest.mark.control(reason="a key the dispatcher reads; must stay accepted (#1758)")
@pytest.mark.parametrize(
    "key", ["kb_name", "kb", "source_kb", "target_kb", "center_kb", "kb_names", "kb_a", "kb_b"]
)
def test_a_kb_argument_is_the_dispatchers_on_every_tool(server, key):
    """Every KB argument name is read by the dispatcher on every call (the
    resolver, the read and write checks, the read-only check, the landing
    rule), so it is never an unknown key: on a write tool that does not list
    it (`kb_registry_health`, admin) it is not refused as one, and on a read
    tool that does not list it (`kb_list`) it is not reported."""
    value = [KB] if key == "kb_names" else KB
    write = _call(server, "kb_registry_health", {"name": KB, key: value})
    assert not any(r.get("rule") == "unknown_field" for r in write.get("errors", [])), write
    read = _call(server, "kb_list", {key: value})
    assert "ignored_arguments" not in read, read


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("sw_claim", {"kb_name": KB, "item_id": "probe-1758", "assignee": "me"}),
        (
            "investigation_create_entity",
            {"kb_name": KB, "title": "Kb 1758", "entity_type": "person"},
        ),
        ("task_create", {"kb_name": KB, "title": "Kb task 1758"}),
    ],
)
def test_a_kb_argument_a_write_tool_does_not_take_is_refused(server, tool, args):
    """A write tool that declares a KB argument of its own works in that one:
    an extra `kb` would be access-checked and then dropped, so it is refused
    and nothing is written (round 1 on #1774)."""
    before = _files(server)
    result = _call(server, tool, {**args, "kb": KB})
    _assert_refused_naming(result, "kb")
    assert _files(server) == before


def test_every_write_tool_with_its_own_kb_argument_refuses_another(server):
    """By iteration: a write tool that declares a KB argument refuses a KB
    argument name it does not declare; one that declares none (the
    `kb_registry_*` tools) takes any, since the dispatcher's write check needs
    one named."""
    kb_names = {"kb_name", "kb", "source_kb", "target_kb", "center_kb", "kb_names", "kb_a", "kb_b"}
    wrong = []
    for name, meta in sorted(server.tools.items()):
        if not _is_write(server, name) or name in FREE_FORM:
            continue
        props = set(meta["inputSchema"].get("properties") or {})
        own = props & kb_names
        extra = sorted(kb_names - props - {"kb_name"})[0]  # never kb_name: the dispatcher's
        value = [KB] if extra == "kb_names" else KB
        args = {**_minimal_arguments(meta["inputSchema"]), extra: value}
        result = _call(server, name, args)
        refused = [r.get("field") for r in result.get("errors", [])] == [extra]
        if own and not refused:
            wrong.append(f"{name} (declares {sorted(own)}): {extra} not refused: {result}")
        if not own and refused:
            wrong.append(f"{name} (declares no KB argument): {extra} refused")
    assert not wrong, "\n  ".join(wrong)


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        (
            "kb_link",
            {"source_id": "a-1781", "target_id": "b-1781", "relation": "related_to"},
        ),
        ("kb_commit", {"kb": KB, "message": "probe 1781"}),
        ("kb_push", {"kb": KB}),
    ],
)
def test_kb_name_is_refused_on_a_write_tool_that_declares_another_kb_argument(server, tool, args):
    """`kb_name` is a KB argument like any other: a tool whose own KB argument
    is `source_kb`/`target_kb` (`kb_link`) or `kb` (`kb_commit`, `kb_push`)
    does not read it, so it is refused, not access-checked and dropped (#1781)."""
    assert "kb_name" not in server.tools[tool]["inputSchema"]["properties"]
    before = _files(server)
    result = _call(server, tool, {**args, "kb_name": KB})
    _assert_refused_naming(result, "kb_name")
    assert _files(server) == before


def _probe_with_own_kb_argument(server, name, **extra):
    """A write tool that declares `kb` (so the "declares no KB argument"
    branch does not accept `kb_name` for it) and not `kb_name`."""
    seen = _register(
        server, name, "write", {"kb": {"type": "string"}, "entry_id": {"type": "string"}}, **extra
    )
    return seen


@pytest.mark.control(reason="dispatcher-consumed key; must stay accepted (#1781)")
def test_kb_name_is_accepted_on_a_core_write_tool_that_locates_an_entry(server, monkeypatch):
    """No in-tree core write tool is `locates_entry` without declaring
    `kb_name`, so the branch is driven by a probe registered under a core
    name, with its own `kb` argument and no `kb_name`: only the
    `locates_entry` clause can accept `kb_name` for it."""
    import pyrite.server.mcp_server as mod

    name = "probe_core_locates_1781"
    monkeypatch.setattr(mod, "CORE_TOOL_NAMES", frozenset(mod.CORE_TOOL_NAMES) | {name})
    made = _call(server, "kb_create", {"kb_name": KB, "entry_type": "note", "title": "Loc 1781"})
    seen = _probe_with_own_kb_argument(server, name, locates_entry={"id_args": ["entry_id"]})
    try:
        result = _call(server, name, {"entry_id": made["entry_id"], "kb_name": KB})
        assert result == {"ok": True}, result
        assert seen["kb_name"] == KB
    finally:
        server.tools.pop(name)
        server._tool_tiers.pop(name)


@pytest.mark.control(reason="dispatcher-consumed key; must stay accepted (#1781)")
def test_kb_name_is_accepted_on_a_plugin_write_tool_that_declares_a_different_kb_argument(
    server,
):
    """A plugin write tool (not a core name) that declares `kb`: `kb_name` is
    still the dispatcher's, since it sets the handler's KB (`DEFAULT_KB`)."""
    name = "probe_plugin_kb_1781"
    seen = _probe_with_own_kb_argument(server, name)
    try:
        result = _call(server, name, {"kb_name": KB})
        assert result == {"ok": True}, result
        assert seen["kb_name"] == KB
    finally:
        server.tools.pop(name)
        server._tool_tiers.pop(name)


@pytest.mark.control(reason="ADR-0034 marker; must keep its own refusal")
def test_a_truncation_marker_on_a_write_meets_the_truncation_refusal(server):
    """ADR-0034 markers are read by the dispatcher's truncation guard, not unknown."""
    result = _call(
        server,
        "task_create",
        {"kb_name": KB, "title": "Trunc 1758", "body": "part", "body_truncated": True},
    )
    assert result.get("error_code") == "VALIDATION_FAILED", result
    assert "body_truncated" in result["error"], result
    assert "errors" not in result, result  # not an unknown-key refusal


@pytest.mark.control(reason="ADR-0034 marker; must stay accepted")
def test_a_false_truncation_marker_on_a_write_is_accepted(server):
    result = _call(
        server,
        "task_create",
        {"kb_name": KB, "title": "Whole 1758", "body": "all", "body_truncated": False},
    )
    assert result.get("created") is True, result


def test_the_server_instructions_state_the_rule():
    from pyrite.server.mcp_server import MCP_SERVER_INSTRUCTIONS

    text = MCP_SERVER_INSTRUCTIONS.format(tier="write")
    assert "refused on a write tool" in text
    assert "ignored_arguments" in text
    assert "kb_create" in text and "kb_update" in text  # the two free-form exceptions


# ---------------------------------------------------------------------------
# Through the SDK transport
# ---------------------------------------------------------------------------


async def _sdk_call(sdk, name, arguments):
    req = CallToolRequest(
        method="tools/call", params=CallToolRequestParams(name=name, arguments=arguments)
    )
    result = (await sdk.request_handlers[CallToolRequest](req)).root
    return json.loads(result.content[0].text)


def test_the_sdk_path_refuses_and_reports_the_same(server):
    sdk = server.build_sdk_server()
    refused = asyncio.run(
        _sdk_call(sdk, "task_create", {"kb_name": KB, "title": "SDK 1758", "due": "x"})
    )
    assert refused.get("error_code") == "VALIDATION_FAILED", refused
    assert [r["field"] for r in refused["errors"]] == ["due"]
    reported = asyncio.run(_sdk_call(sdk, "kb_list", {"stray": 1}))
    assert reported["ignored_arguments"] == ["stray"], reported
