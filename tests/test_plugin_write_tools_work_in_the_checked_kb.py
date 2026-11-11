"""A plugin write tool's handler works only in a KB the dispatcher checked.

The rule, held at the one dispatcher: a plugin tool above the read tier is
handed ``kb_name`` set to a KB every check (read, write, read-only, landing)
ran on. A KB named under another argument (``kb``, ``source_kb``, ...) is
checked but never chooses the handler's KB. A tool whose KB may be omitted
declares ``default_kb`` and the dispatcher asks it before the checks, taking
only a KB the caller may read; with no KB named and no default, the call is
refused.

Generated from the registry, not a list: every plugin write tool, every KB
argument name (and none), three callers.
"""

from __future__ import annotations

import functools
import hashlib
import itertools
from pathlib import Path

import pytest

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.mcp_server import KB_ARGUMENT_NAMES, PyriteMCPServer
from pyrite.server.tool_schemas import CORE_TOOL_NAMES
from pyrite.services.access_policy import Principal

MINE = "mine"
_SERIAL = itertools.count()
WRITER: Principal | None = None  # the signed-in writer, made by `world`
# One KB of each plugin's type, so every plugin's default has somewhere else
# to fall back to.
OTHERS = {
    "inv": "journalism-investigation",
    "sw": "software",
    "wiki": "encyclopedia",
    "social": "social",
    "zettel": "zettelkasten",
}

# Who calls: a caller who may read and write only MINE; one who may read
# every KB and write only MINE; the unscoped operator (stdio).
CALLERS = {
    "scoped-to-mine": ({MINE}, {MINE}),
    "reads-all-writes-mine": ({MINE, *OTHERS}, {MINE}),
    "operator": (None, None),
}


def _tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _value(schema: dict):
    """A value of the schema's type: enough to get a call past the SDK's
    shape and into the dispatcher, not necessarily a useful write."""
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "string")
    if kind == "integer" or kind == "number":
        return 1
    if kind == "boolean":
        return False
    if kind == "array":
        return [_value(schema.get("items") or {"type": "string"})]
    if kind == "object":
        return {}
    return "x"


# Values a handler accepts, so the call is a real write rather than a
# validation error: the "files unchanged" check is only as good as the write.
_REAL = {"entity_type": "person", "event_type": "transaction", "date": "2026-01-01"}


def _args(tool: dict) -> dict:
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties", {})
    args = {
        k: _REAL.get(k, _value(props.get(k, {})))
        for k in schema.get("required", [])
        if k not in KB_ARGUMENT_NAMES
    }
    if "title" in args:
        args["title"] = f"made {next(_SERIAL)}"
    return args


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("checked-kb")
    kbs = [KBConfig(name=MINE, path=tmp / MINE, kb_type="generic")]
    kbs += [KBConfig(name=n, path=tmp / n, kb_type=t) for n, t in OTHERS.items()]
    for kb in kbs:
        kb.path.mkdir()
        (kb.path / "seed.md").write_text("---\nid: x\ntitle: X\ntype: note\n---\nseed\n")
    config = PyriteConfig(
        knowledge_bases=kbs,
        settings=Settings(
            index_path=tmp / "index.db",
            auth=AuthConfig(enabled=True),
            rate_limit_read="100000/minute",
            rate_limit_write="100000/minute",
            rate_limit_admin="100000/minute",
        ),
    )
    server = PyriteMCPServer(config=config, tier="admin")
    from pyrite.services.auth_service import AuthService

    user = AuthService(server.db, config.settings.auth).create_user("w", "password123", "write")
    global WRITER
    WRITER = Principal.user(user["id"], "write")
    try:
        yield server, {kb.name: kb.path for kb in kbs}
    finally:
        server.close()


def _plugin_write_tools() -> list[str]:
    from tests._surface_inventory import mcp_server

    with mcp_server() as server:
        return sorted(
            n
            for n in server.tools
            if n not in CORE_TOOL_NAMES and server._tool_tiers.get(n, "read") != "read"
        )


PLUGIN_WRITE_TOOLS = _plugin_write_tools()


@pytest.mark.control(reason="the plugins under test are installed; an empty list proves nothing")
def test_the_registry_has_plugin_write_tools():
    assert len(PLUGIN_WRITE_TOOLS) >= 10, PLUGIN_WRITE_TOOLS


def _spy(server, name: str) -> list[dict]:
    """Record every call's args to ``name``'s handler. ``functools.wraps``
    keeps the signature, which the dispatcher reads (``readable_kbs``)."""
    calls: list[dict] = []
    tool = server.tools[name]
    inner = tool["handler"]

    @functools.wraps(inner)
    def handler(args, **kwargs):
        calls.append(dict(args))
        return inner(args, **kwargs)

    server.tools[name] = {**tool, "handler": handler}
    server.__dict__.get("_handler_keywords", {}).pop(name, None)
    return calls


def _call(server, tool_name, args, caller):
    readable, writable = CALLERS[caller]
    if readable is None:
        return server._dispatch_tool(tool_name, args, client_kind="local")
    return server._dispatch_tool(
        tool_name,
        args,
        client_kind="user",
        client_id="1",
        readable_kbs=readable,
        writable_kbs=writable,
        principal=WRITER,
    )


def _shapes():
    """(argument name, caller), one test each, every plugin write tool inside.

    Marked ``control`` where the old dispatcher already held the property, so
    the rest are exactly the instances this fix closes: a KB named under
    ``kb_name`` was always the handler's KB, and a scoped call naming no KB
    was always refused (fail-closed, or the write check's "name one")."""
    for arg_name in (None, *KB_ARGUMENT_NAMES):
        for caller in CALLERS:
            safe_before = arg_name == "kb_name" or (arg_name is None and caller != "operator")
            marks = (
                [pytest.mark.control(reason="the dispatcher held this before the fix")]
                if safe_before
                else []
            )
            yield pytest.param(arg_name, caller, marks=marks, id=f"{arg_name}-{caller}")


@pytest.mark.parametrize(("arg_name", "caller"), list(_shapes()))
def test_a_plugin_write_handler_works_only_in_a_checked_kb(world, arg_name, caller):
    server, paths = world
    writable = CALLERS[caller][1]
    wrong: list[str] = []
    for tool_name in PLUGIN_WRITE_TOOLS:
        args = _args(server.tools[tool_name])
        if arg_name is not None:
            args[arg_name] = [MINE] if arg_name == "kb_names" else MINE
        before = {kb: _tree(p) for kb, p in paths.items() if kb != MINE}
        original = server.tools[tool_name]
        calls = _spy(server, tool_name)
        try:
            result = _call(server, tool_name, args, caller)
        finally:
            server.tools[tool_name] = original
            server.__dict__.get("_handler_keywords", {}).pop(tool_name, None)
        for seen in calls:
            kb = seen.get("kb_name")
            if not (isinstance(kb, str) and kb.strip()):
                wrong.append(f"{tool_name} ran with no KB named ({kb!r}): {result}")
            elif writable is not None and kb not in writable:
                wrong.append(f"{tool_name} ran in {kb!r}, which {caller} may not write")
        if writable is not None:
            after = {kb: _tree(p) for kb, p in paths.items() if kb != MINE}
            if after != before:
                wrong.append(f"{tool_name} changed a KB {caller} may not write: {result}")
    assert not wrong, "\n".join(wrong)


def _non_locating_write_tools() -> list[str]:
    from tests._surface_inventory import mcp_server

    with mcp_server() as server:
        return [n for n in PLUGIN_WRITE_TOOLS if not server.tools[n].get("locates_entry")]


@pytest.mark.control(reason="the arguments reach the handler when kb_name names the KB")
@pytest.mark.parametrize("tool_name", _non_locating_write_tools())
def test_a_call_that_names_its_kb_reaches_the_handler(world, tool_name):
    """A handler that locates an id is reached only for an id it finds; the
    test above covers those tools' KB through the dispatcher's lookup."""
    server, _ = world
    args = {**_args(server.tools[tool_name]), "kb_name": MINE}
    original = server.tools[tool_name]
    calls = _spy(server, tool_name)
    try:
        result = server._dispatch_tool(
            tool_name,
            args,
            client_kind="user",
            client_id="1",
            readable_kbs={MINE},
            writable_kbs={MINE},
            principal=WRITER,
        )
    finally:
        server.tools[tool_name] = original
        server.__dict__.get("_handler_keywords", {}).pop(tool_name, None)
    assert [c.get("kb_name") for c in calls] == [MINE], result


@pytest.mark.control(reason="a write naming no KB still lands in the tool's default, now checked")
def test_a_write_naming_no_kb_lands_in_the_tools_default(world):
    """``default_kb`` keeps "auto-detected if omitted": the dispatcher names
    the default before its checks, and the handler writes there."""
    server, paths = world
    before = _tree(paths["inv"])
    result = server._dispatch_tool(
        "investigation_create_entity",
        {"entity_type": "person", "title": f"made {next(_SERIAL)}"},
        client_kind="local",
    )
    assert "error" not in result, result
    assert _tree(paths["inv"]) != before, "the default investigation KB took the write"


def test_a_default_the_caller_cannot_write_is_refused_by_the_write_check(world):
    """The default is checked like a named KB: a caller who may read the
    investigation KB but write only MINE is refused, and nothing is written."""
    server, paths = world
    before = _tree(paths["inv"])
    result = server._dispatch_tool(
        "investigation_create_entity",
        {"entity_type": "person", "title": f"made {next(_SERIAL)}", "kb": MINE},
        client_kind="user",
        client_id="1",
        readable_kbs={MINE, *OTHERS},
        writable_kbs={MINE},
        principal=WRITER,
    )
    assert result.get("error_code") == "FORBIDDEN", result
    assert _tree(paths["inv"]) == before


def test_a_default_the_caller_cannot_read_is_no_default(world, monkeypatch):
    """Whatever a plugin's ``default_kb`` answers, the dispatcher takes only a
    KB the caller may read; anything else is no default, and the refusal names
    no KB the caller cannot see."""
    server, paths = world
    name = "investigation_create_entity"
    monkeypatch.setitem(
        server.tools, name, {**server.tools[name], "default_kb": lambda args, readable: "inv"}
    )
    before = _tree(paths["inv"])
    result = server._dispatch_tool(
        name,
        {"entity_type": "person", "title": f"made {next(_SERIAL)}", "kb": MINE},
        client_kind="user",
        client_id="1",
        readable_kbs={MINE},
        writable_kbs={MINE},
        principal=WRITER,
    )
    assert result.get("error_code") == "VALIDATION_FAILED", result
    assert "'inv'" not in str(result), result
    assert _tree(paths["inv"]) == before
