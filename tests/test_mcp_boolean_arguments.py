"""A declared-boolean MCP argument is normalised once, at the dispatcher (#1744).

Decision (#1744, groom of 2026-11-25, following the `include_empty`
precedent of #200): in `_dispatch_tool_checked`, right after the
unknown-argument check, every key the tool's `inputSchema` declares
`"type": "boolean"` is read once. A JSON bool passes; `"true"`/`"false"`
(stripped, any case) become a bool; `null` is treated as absent, so the
argument's default applies (which keeps three-state arguments such as
`task_list`'s `parked` working); an absent key stays absent. Anything
else (an integer, `"yes"`, a list, an object) is refused with
`INVALID_PARAMETER` naming the argument, before the handler runs. Plugin
tools pass through the same dispatcher, so the rule covers them without
editing the plugin, and handlers read plain `args.get(k, default)`.

Guards, widest first:

- enumeration: the tool table of a live admin server (core tables plus
  every plugin's `get_mcp_tools()`) declares 26 booleans today, 21 on
  core tools and 5 on the journalism-investigation plugin's tools. The
  count is pinned: a new declared boolean must update it on purpose.
- structural: `_dispatch_tool_checked` calls the one normaliser.
- shapes: the normaliser itself, over every declared (tool, argument)
  pair and every shape the groom lists.
- sweep: through the real dispatcher, every declared boolean of every
  tool refuses every bad shape, naming the argument.
- cells: `task_list` with `dispatchable: "false"` does not filter as
  true (the exact report the issue was filed from); `task_park` reads
  `operator_override: "true"` as true; a refused boolean on `kb_create`
  writes no file and no index entry.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

import pyrite.server.mcp_server as mcp_server_module
from pyrite.exceptions import InvalidParameterError
from pyrite.server.mcp_server import _normalise_boolean_arguments
from tests._world import mcp_server as _make_mcp_server
from tests.mcp_seed import STANDARD_KB_DEFS

KB = "test-events"

#: Shapes that cannot be read as a boolean (the groom's list).
BAD_SHAPES = [0, 1, "yes", ["x"], {"x": 1}]
#: (sent, read) pairs for the shapes that can.
GOOD_SHAPES = [
    (True, True),
    (False, False),
    ("true", True),
    ("false", False),
    ("TRUE", True),
    (" false ", False),
]


@pytest.fixture(scope="module")
def env():
    with _make_mcp_server(STANDARD_KB_DEFS, tier="admin") as e:
        yield e


@pytest.fixture(scope="module")
def server(env):
    return env["server"]


def _declared_booleans(server) -> dict[str, set[str]]:
    """tool name -> its declared-boolean argument names, from the live
    tool table (core plus plugins), enumerated here, not by the code
    under test."""
    out: dict[str, set[str]] = {}
    for tool, meta in server.tools.items():
        props = (meta.get("inputSchema") or {}).get("properties", {})
        names = set()
        for n, spec in props.items():
            declared = (spec or {}).get("type")
            if declared == "boolean" or (isinstance(declared, list) and "boolean" in declared):
                names.add(n)
        if names:
            out[tool] = names
    return out


@pytest.fixture(scope="module")
def declared(server):
    return _declared_booleans(server)


def _pairs(declared) -> list[tuple[str, str]]:
    return [(tool, arg) for tool in sorted(declared) for arg in sorted(declared[tool])]


# ---------------------------------------------------------------------------
# Enumeration: the whole tool table, plugins included
# ---------------------------------------------------------------------------


def test_the_tool_table_declares_26_booleans(declared):
    assert sum(len(v) for v in declared.values()) == 26
    core = {t: v for t, v in declared.items() if not t.startswith("investigation_")}
    plugin = {t: v for t, v in declared.items() if t.startswith("investigation_")}
    assert sum(len(v) for v in core.values()) == 21
    assert sum(len(v) for v in plugin.values()) == 5
    assert declared["task_finish"] == {"walk", "operator_override"}
    assert declared["task_list"] == {"dispatchable", "parked"}


# ---------------------------------------------------------------------------
# Structural: one normaliser, called by the dispatcher
# ---------------------------------------------------------------------------


def test_the_dispatcher_calls_the_one_normaliser():
    source = textwrap.dedent(
        inspect.getsource(mcp_server_module.PyriteMCPServer._dispatch_tool_checked)
    )
    tree = ast.parse(source)
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_normalise_boolean_arguments" in calls


# ---------------------------------------------------------------------------
# Shapes: the normaliser over every declared (tool, argument) pair
# ---------------------------------------------------------------------------


def test_good_shapes_become_the_bool_sent(server, declared):
    for tool, arg in _pairs(declared):
        schema = server.tools[tool]["inputSchema"]
        for sent, read in GOOD_SHAPES:
            out = _normalise_boolean_arguments(schema, {arg: sent})
            assert out[arg] is read, (tool, arg, sent, out)


def test_bad_shapes_raise_naming_the_argument(server, declared):
    for tool, arg in _pairs(declared):
        schema = server.tools[tool]["inputSchema"]
        for sent in BAD_SHAPES:
            with pytest.raises(InvalidParameterError) as caught:
                _normalise_boolean_arguments(schema, {arg: sent})
            assert arg in str(caught.value), (tool, arg, sent)


def test_null_is_absent_and_absent_stays_absent(server, declared):
    for tool, arg in _pairs(declared):
        schema = server.tools[tool]["inputSchema"]
        assert arg not in _normalise_boolean_arguments(schema, {arg: None})
        assert _normalise_boolean_arguments(schema, {}) == {}


def test_undeclared_keys_pass_through_untouched(server):
    schema = server.tools["task_list"]["inputSchema"]
    out = _normalise_boolean_arguments(schema, {"status": "yes", "dispatchable": "true"})
    assert out == {"status": "yes", "dispatchable": True}


# ---------------------------------------------------------------------------
# Sweep: the dispatcher refuses every bad shape, naming the argument
# ---------------------------------------------------------------------------


def _call(server, name, args):
    return server._dispatch_tool(name, args, client_id="stdio", client_kind="local")


#: KB arguments must name a real KB: the dispatcher resolves them before
#: the handler runs.
_OVERRIDES = {"kb_name": KB, "kb": KB, "source_kb": KB, "target_kb": KB}


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
        "string": "probe-1744",
        "integer": 1,
        "number": 1,
        "boolean": False,
        "array": [],
        "object": {},
    }.get(kind, "probe-1744")


def _minimal_arguments(schema: dict) -> dict:
    props = schema.get("properties", {})
    return {n: _minimal_value(n, props.get(n, {})) for n in schema.get("required", [])}


def test_every_declared_boolean_refuses_every_bad_shape(server, declared):
    for tool, arg in _pairs(declared):
        schema = server.tools[tool]["inputSchema"]
        for sent in BAD_SHAPES:
            args = {**_minimal_arguments(schema), arg: sent}
            result = _call(server, tool, args)
            assert result.get("error_code") == "INVALID_PARAMETER", (tool, arg, sent, result)
            assert arg in result["error"], (tool, arg, sent, result)


# ---------------------------------------------------------------------------
# Cells: the readings a caller actually sees
# ---------------------------------------------------------------------------


def _task_ids(result: dict) -> set[str]:
    return {row["id"] for row in result["tasks"]}


def test_dispatchable_string_false_does_not_filter_as_true(server):
    made_a = _call(server, "task_create", {"kb_name": KB, "title": "Cell 1744 open"})
    assert made_a.get("created") is True, made_a
    made_b = _call(server, "task_create", {"kb_name": KB, "title": "Cell 1744 claimed"})
    assert made_b.get("created") is True, made_b
    claimed = _call(
        server,
        "task_claim",
        {"kb_name": KB, "task_id": made_b["entry_id"], "assignee": "me"},
    )
    assert "error_code" not in claimed, claimed
    a_id, b_id = made_a["entry_id"], made_b["entry_id"]

    unfiltered = _task_ids(_call(server, "task_list", {"kb_name": KB}))
    assert {a_id, b_id} <= unfiltered

    only_dispatchable = _task_ids(_call(server, "task_list", {"kb_name": KB, "dispatchable": True}))
    assert a_id in only_dispatchable
    assert b_id not in only_dispatchable

    as_string = _task_ids(_call(server, "task_list", {"kb_name": KB, "dispatchable": "false"}))
    assert as_string == _task_ids(
        _call(server, "task_list", {"kb_name": KB, "dispatchable": False})
    )
    assert {a_id, b_id} <= as_string, "the string 'false' filtered as true"

    as_true_string = _task_ids(_call(server, "task_list", {"kb_name": KB, "dispatchable": "true"}))
    assert as_true_string == only_dispatchable

    # null is absent: the default applies, exactly as if unsent.
    as_null = _task_ids(_call(server, "task_list", {"kb_name": KB, "dispatchable": None}))
    assert as_null == unfiltered

    refused = _call(server, "task_list", {"kb_name": KB, "dispatchable": "yes"})
    assert refused.get("error_code") == "INVALID_PARAMETER", refused
    assert "dispatchable" in refused["error"], refused


def test_operator_override_string_true_is_read_as_true(server):
    made = _call(server, "task_create", {"kb_name": KB, "title": "Cell 1744 park"})
    assert made.get("created") is True, made
    claimed = _call(
        server,
        "task_claim",
        {"kb_name": KB, "task_id": made["entry_id"], "assignee": "me"},
    )
    assert "error_code" not in claimed, claimed
    parked = _call(
        server,
        "task_park",
        {
            "kb_name": KB,
            "task_id": made["entry_id"],
            "by": "me",
            "reason": "waiting on a person",
            "operator_override": "true",
        },
    )
    assert parked.get("error_code") != "INVALID_PARAMETER", parked
    assert "error_code" not in parked, parked


def _files(server) -> dict[str, bytes]:
    """Every file under every KB, path -> bytes."""
    out: dict[str, bytes] = {}
    for kb in server.config.knowledge_bases:
        for p in sorted(Path(kb.path).rglob("*")):
            if p.is_file():
                out[str(p)] = p.read_bytes()
    return out


def test_a_refused_boolean_on_kb_create_writes_nothing(server):
    title = "Zzcell1744"
    before = _files(server)
    refused = _call(
        server,
        "kb_create",
        {"kb_name": KB, "entry_type": "note", "title": title, "validate": "yes"},
    )
    assert refused.get("error_code") == "INVALID_PARAMETER", refused
    assert "validate" in refused["error"], refused
    assert _files(server) == before, "the refusal wrote a file"
    found = _call(server, "kb_search", {"kb_name": KB, "query": title})
    assert title not in str(found.get("results", "")), found

    # The same call with a readable boolean goes through and writes.
    made = _call(
        server,
        "kb_create",
        {"kb_name": KB, "entry_type": "note", "title": title, "validate": "false"},
    )
    assert "error_code" not in made, made
    assert len(_files(server)) > len(before)
