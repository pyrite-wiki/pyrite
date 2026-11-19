"""A declared-boolean MCP argument reads through one helper (#1744).

Decision (#1744, following the `include_empty` precedent of #200): a boolean
argument accepts a JSON `true`/`false` or the string `"true"`/`"false"`;
any other value answers `INVALID_PARAMETER` naming the argument, before the
tool runs. The string `"false"` never reads as true.

Three guards, widest first:

- structural: `pyrite/server/mcp_server.py` holds no bare `bool()` read of
  an argument and no direct `args.get`/`args[...]` read of a declared-boolean
  name; every declared-boolean name is read by `_boolean_arg`.
- sweep: every core tool (the schemas in `pyrite/server/tool_schemas.py`)
  refuses a non-boolean value for each of its declared-boolean arguments,
  through the real dispatcher. Plugin tools declare their own schemas in
  their own packages and are out of this test's scope.
- the cell: `task_list` with `dispatchable: "false"` does not filter as
  true -- the exact report the issue was filed from.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import pyrite.server.mcp_server as mcp_server_module
from pyrite.server.tool_schemas import ADMIN_TOOLS, READ_TOOLS, WRITE_TOOLS
from tests._world import mcp_server as _make_mcp_server
from tests.mcp_seed import STANDARD_KB_DEFS

KB = "test-events"
BAD = "yes"


def _declared_booleans() -> dict[str, set[str]]:
    """tool name -> its declared-boolean argument names, core tools only."""
    out: dict[str, set[str]] = {}
    for schemas in (READ_TOOLS, WRITE_TOOLS, ADMIN_TOOLS):
        for tool, meta in schemas.items():
            props = meta["inputSchema"].get("properties", {})
            names = {n for n, spec in props.items() if spec.get("type") == "boolean"}
            if names:
                out[tool] = names
    return out


DECLARED = _declared_booleans()


# ---------------------------------------------------------------------------
# Structural: one helper reads every declared-boolean argument
# ---------------------------------------------------------------------------


def _mcp_server_tree() -> ast.Module:
    return ast.parse(Path(mcp_server_module.__file__).read_text())


def test_no_bare_bool_read_of_an_argument():
    offenders = []
    for node in ast.walk(_mcp_server_tree()):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "bool"
        ):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                    if isinstance(inner.func.value, ast.Name) and inner.func.value.id == "args":
                        offenders.append(node.lineno)
                if isinstance(inner, ast.Subscript) and isinstance(inner.value, ast.Name):
                    if inner.value.id == "args":
                        offenders.append(node.lineno)
    assert not offenders, f"bool() read of an argument at lines {offenders}"


def test_no_direct_read_of_a_declared_boolean_argument():
    names = set().union(*DECLARED.values())
    offenders = []
    for node in ast.walk(_mcp_server_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "args"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in names
            ):
                offenders.append((node.args[0].value, node.lineno))
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            if (
                node.value.id == "args"
                and isinstance(node.slice, ast.Constant)
                and node.slice.value in names
            ):
                offenders.append((node.slice.value, node.lineno))
    assert not offenders, f"declared-boolean arguments read directly: {offenders}"


def test_every_declared_boolean_name_goes_through_the_helper():
    names = set().union(*DECLARED.values())
    read = set()
    for node in ast.walk(_mcp_server_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "_boolean_arg" and len(node.args) >= 2:
                if isinstance(node.args[1], ast.Constant):
                    read.add(node.args[1].value)
    assert names <= read, f"never read by _boolean_arg: {sorted(names - read)}"


# ---------------------------------------------------------------------------
# Sweep: a non-boolean value is refused, naming the argument
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def env():
    with _make_mcp_server(STANDARD_KB_DEFS, tier="admin") as e:
        yield e


@pytest.fixture
def server(env):
    return env["server"]


def _call(server, name, args):
    return server._dispatch_tool(name, args, client_id="stdio", client_kind="local")


#: KB arguments must name a real KB: the dispatcher resolves them before
#: the handler (and its boolean reads) runs.
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


def _schema_of(server, tool: str) -> dict:
    return server.tools[tool]["inputSchema"]


@pytest.mark.parametrize("tool", sorted(DECLARED))
def test_a_non_boolean_value_is_refused_naming_the_argument(server, tool):
    schema = _schema_of(server, tool)
    for arg in sorted(DECLARED[tool]):
        args = {**_minimal_arguments(schema), arg: BAD}
        result = _call(server, tool, args)
        assert result.get("error_code") == "INVALID_PARAMETER", (tool, arg, result)
        assert arg in result["error"], (tool, arg, result)


# ---------------------------------------------------------------------------
# The cell: dispatchable: "false" does not filter as true
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

    refused = _call(server, "task_list", {"kb_name": KB, "dispatchable": BAD})
    assert refused.get("error_code") == "INVALID_PARAMETER", refused
    assert "dispatchable" in refused["error"], refused
