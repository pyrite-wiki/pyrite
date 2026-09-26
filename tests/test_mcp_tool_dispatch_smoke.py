"""
Dispatch smoke test: every registered MCP tool is called once.

Tests cover what the author of a change thought to test, and the tool surface
grows faster than that. A handler can be advertised in the schema, listed in
the README, and raise on every call: `_dispatch_tool` converts any exception
into a structured `INTERNAL` error, so nothing fails loudly. Four read-tier
task tools shipped that way (`self._task_svc` for `self.task_svc`) under a
green suite (backlog: mcp-tool-dispatch-smoke-test-every-registered-tool).

For each tool on an admin-tier server -- core and plugin-contributed alike --
this builds minimal arguments from the tool's own JSON schema, dispatches
through the path a real client uses, and asserts the result is not `INTERNAL`.
A domain error (`NOT_FOUND`, `VALIDATION_FAILED`, ...) is a pass: the handler
ran and answered. Completeness is pinned too: a tool is either exercised or
listed in SKIP with a reason.
"""

import pytest

from tests.test_mcp_server import (
    _STANDARD_KB_DEFS,
    _make_mcp_server,
    _populate_events_and_person,
)

pytestmark = pytest.mark.core  # the local smoke set; see scripts/test-affected

# Tools not dispatched here, each with the reason. Keep this short: a skip is
# a tool nothing proves can run.
SKIP: dict[str, str] = {
    "kb_push": "pushes to a git remote; needs a bare remote fixture",
    "kb_commit": "commits to a git repo; the temp KB is not one",
}

# Values the schema alone cannot make sensible. Keyed by property name; an
# unknown id is fine (NOT_FOUND is a pass) but a KB name must exist for the
# handler to get past its first line.
_PROPERTY_OVERRIDES = {
    "kb_name": "test-events",
    "kb": "test-events",
}


def _minimal_value(name: str, spec: dict):
    if name in _PROPERTY_OVERRIDES:
        return _PROPERTY_OVERRIDES[name]
    if "enum" in spec:
        return spec["enum"][0]
    if "default" in spec:
        return spec["default"]
    kind = spec.get("type", "string")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "string")
    return {
        "string": "smoke-test",
        "integer": 1,
        "number": 1,
        "boolean": False,
        "array": [],
        "object": {},
    }.get(kind, "smoke-test")


def _minimal_arguments(schema: dict) -> dict:
    props = schema.get("properties", {})
    return {name: _minimal_value(name, props.get(name, {})) for name in schema.get("required", [])}


@pytest.fixture(scope="module")
def server():
    with _make_mcp_server(
        _STANDARD_KB_DEFS, tier="admin", extra_setup=_populate_events_and_person
    ) as env:
        yield env["server"]


def test_tool_surface_is_enumerable(server):
    """Guard against the vacuous pass: no tools means nothing was checked."""
    assert len(server.tools) > 30, f"expected the full tool surface, got {len(server.tools)}"
    assert "test-events" in {kb.name for kb in server.config.knowledge_bases}


def test_every_tool_dispatches_without_an_unhandled_exception(server):
    crashed = []
    for name, meta in sorted(server.tools.items()):
        if name in SKIP:
            continue
        args = _minimal_arguments(meta["inputSchema"])
        result = server._dispatch_tool(name, args, client_id="stdio", client_kind="local")
        if isinstance(result, dict) and result.get("error_code") == "INTERNAL":
            crashed.append(f"{name}({args}) -> {result.get('error')}")
    assert not crashed, "MCP tools raised an unhandled exception:\n  " + "\n  ".join(crashed)


def test_skip_table_has_no_stale_entries(server):
    stale = sorted(set(SKIP) - set(server.tools))
    assert not stale, f"SKIP names tools that are no longer registered: {stale}"


def test_domain_errors_are_not_reported_as_retryable_internal_errors(server):
    """A refused request is not a crash, and must not invite a retry.

    The dispatcher used to turn every exception into `INTERNAL, retryable: true`.
    For a validation failure that tells an agent to repeat a call that can never
    succeed.
    """
    args = {"kb_name": "test-events", "title": "dispatch-collision-probe"}
    first = server._dispatch_tool("task_create", args, client_id="stdio", client_kind="local")
    assert "error" not in first, first
    second = server._dispatch_tool("task_create", args, client_id="stdio", client_kind="local")
    # An existing id is refused with its own code since #378.
    assert second.get("error_code") == "ENTRY_EXISTS", second
    assert second.get("retryable") is False
