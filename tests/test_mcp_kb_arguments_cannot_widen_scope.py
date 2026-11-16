"""No shape of a KB argument gets a KB the caller may not read to a handler.

The property, for a scoped caller, over every registered tool (core and
plugin, every tier), every name in ``KB_ARGUMENT_NAMES`` and every value
shape (strings, lists, tuples, objects, nested lists): the dispatcher refuses
the call, or the handler runs with no KB the caller cannot read anywhere in
its KB arguments. And ``kb_timeline`` intersects ``kb_names`` with the
readable set itself, so it does not rest on the dispatcher alone.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

import pytest

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.server.mcp_server import KB_ARGUMENT_NAMES, PyriteMCPServer
from pyrite.services.access_policy import Principal

PUBLIC, PRIVATE = "public", "private"
MARKER = "zebra-behind-the-wall"

# Every shape a client (or a direct caller) can send that names PRIVATE.
SHAPES = {
    "string": PRIVATE,
    "padded": f" {PRIVATE} ",
    "upper": PRIVATE.upper(),
    "list": [PRIVATE],
    "tuple": (PRIVATE,),
    "list-with-readable": [PUBLIC, PRIVATE],
    "object": {PRIVATE: True},
    "nested-list": [[PRIVATE]],
    "list-of-objects": [{"kb_name": PRIVATE}],
    "list-with-non-string": [PRIVATE, 1],
}


def _value(schema: dict):
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "string")
    if kind in ("integer", "number"):
        return 1
    if kind == "boolean":
        return False
    if kind == "array":
        return [_value(schema.get("items") or {"type": "string"})]
    if kind == "object":
        return {}
    return "x"


def _args(tool: dict) -> dict:
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties", {})
    return {
        k: _value(props.get(k, {}))
        for k in schema.get("required", [])
        if k not in KB_ARGUMENT_NAMES
    }


def _strings(value) -> list[str]:
    """Every string anywhere in ``value``: keys, items, nested."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(value, list | tuple | set | frozenset):
        return [s for v in value for s in _strings(v)]
    return []


def _entry(kb_dir: Path, entry_id: str, body: str) -> None:
    (kb_dir / f"{entry_id}.md").write_text(
        f"---\nid: {entry_id}\ntitle: {entry_id}\ntype: event\ndate: '2026-01-02'\n"
        f"importance: 5\ntags: [shared]\n---\n{body}\n"
    )


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("widen")
    kbs = []
    for name in (PUBLIC, PRIVATE):
        (tmp / name).mkdir()
        kbs.append(KBConfig(name=name, path=tmp / name, kb_type="generic"))
    _entry(tmp / PUBLIC, "x", "public body")
    _entry(tmp / PRIVATE, "secret", MARKER)
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
    from pyrite.services.auth_service import AuthService
    from pyrite.storage.database import PyriteDB
    from pyrite.storage.index import IndexManager

    with PyriteDB(config.settings.index_path) as db:
        IndexManager(db, config).index_all()
    server = PyriteMCPServer(config=config, tier="admin")
    user = AuthService(server.db, config.settings.auth).create_user("r", "password123", "write")
    try:
        yield server, Principal.user(user["id"], "write")
    finally:
        server.close()


def _tool_names() -> list[str]:
    from tests._surface_inventory import mcp_server

    with mcp_server() as server:
        return sorted(server.tools)


TOOLS = _tool_names()


# The shapes the old dispatcher already read (a string, or the strings of a
# list or tuple): it refused PRIVATE in each of them before the fix.
_READ_BEFORE = {"string", "padded", "upper", "list", "tuple", "list-with-readable",
                "list-with-non-string"}  # fmt: skip


def _shape_params():
    for shape in SHAPES:
        marks = (
            [pytest.mark.control(reason="the dispatcher's check already read this shape")]
            if shape in _READ_BEFORE
            else []
        )
        yield pytest.param(shape, marks=marks, id=shape)


@pytest.mark.parametrize("shape", list(_shape_params()))
def test_no_shape_reaches_a_handler_with_an_unreadable_kb(world, shape):
    """One shape, every tool, every KB argument name."""
    server, writer = world
    value = SHAPES[shape]
    wrong: list[str] = []
    for tool_name in TOOLS:
        tool = server.tools[tool_name]
        inner = tool["handler"]
        seen: list[dict] = []

        @functools.wraps(inner)
        def handler(args, _inner=inner, _seen=seen, **kwargs):
            _seen.append(dict(args))
            return _inner(args, **kwargs)

        server.tools[tool_name] = {**tool, "handler": handler}
        server.__dict__.get("_handler_keywords", {}).pop(tool_name, None)
        try:
            for arg_name in KB_ARGUMENT_NAMES:
                seen.clear()
                result = server._dispatch_tool(
                    tool_name,
                    {**_args(tool), arg_name: value},
                    client_kind="user",
                    client_id="reader",
                    readable_kbs={PUBLIC},
                    writable_kbs={PUBLIC},
                    principal=writer,
                )
                for call in seen:
                    named = [
                        s
                        for key in KB_ARGUMENT_NAMES
                        for s in _strings(call.get(key))
                        if s.strip().casefold() == PRIVATE
                    ]
                    if named:
                        wrong.append(f"{tool_name} ran with {arg_name}={shape}, handed {named}")
                if MARKER in json.dumps(result, default=str):
                    wrong.append(f"{tool_name} served the private KB through {arg_name}={shape}")
        finally:
            server.tools[tool_name] = tool
            server.__dict__.get("_handler_keywords", {}).pop(tool_name, None)
    assert not wrong, "\n".join(wrong)


def test_kb_timeline_intersects_the_named_kbs_with_the_readable_set(world):
    """The handler, called directly, narrows `kb_names` to what the caller may
    read: it does not rely on the dispatcher having refused the rest."""
    server, _ = world
    out = server._kb_timeline({"kb_names": [PUBLIC, PRIVATE]}, readable_kbs={PUBLIC})
    assert MARKER not in json.dumps(out), out
    assert {e["kb_name"] for e in out["events"]} == {PUBLIC}


def test_kb_timeline_naming_only_unreadable_kbs_reads_nothing(world):
    server, _ = world
    out = server._kb_timeline({"kb_names": [PRIVATE]}, readable_kbs={PUBLIC})
    assert out["events"] == [], out


@pytest.mark.control(reason="an unscoped caller names any KB, as before")
def test_kb_timeline_unscoped_reads_what_it_names(world):
    server, _ = world
    out = server._kb_timeline({"kb_names": [PRIVATE]}, readable_kbs=None)
    assert [e["kb_name"] for e in out["events"]] == [PRIVATE]


@pytest.mark.control(reason="a well-formed call naming a readable KB is served, as before")
def test_a_readable_kb_named_in_a_list_is_served(world):
    server, writer = world
    out = server._dispatch_tool(
        "kb_timeline",
        {"kb_names": [PUBLIC]},
        client_kind="user",
        client_id="reader",
        readable_kbs={PUBLIC},
        principal=writer,
    )
    assert [e["kb_name"] for e in out["events"]] == [PUBLIC], out
