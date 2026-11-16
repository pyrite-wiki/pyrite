"""The read grant a surface binds is the caller's own: never wider (D4, ADR-0052 N3).

Text that quotes a KB's bytes reaches a network caller only when
``acting.may_read_kb`` says the caller reads that KB. The answer comes from the
grant a surface binds. This file holds each binding to the caller's own KBs,
deny side first: a caller without a grant on KB ``b`` is told no for ``b``,
whatever it may read elsewhere.

The bindings, every one (``GRANT_BINDINGS`` below lists them with the test
that fails without each, and ``test_every_grant_binding_is_listed`` fails on a
new one):

- the context default: nothing bound means no (``test_nothing_bound_reads_no_kb``);
- REST's per-request reset in ``NobodyActingMiddleware`` and the binding in
  ``bind_acting_account``: probed through a route that carries the app's own
  ``/api`` dependencies, copied from the app so they cannot drift;
- MCP's dispatcher: probed through every tool in the tool table, each with its
  handler swapped for a probe and its required arguments built from its own
  input schema, so a new tool is covered without editing this file.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pyrite.config import AuthConfig, KBConfig
from pyrite.services.acting import local_process, may_read_kb
from tests._world import build_world

KBS = ("a", "b")


#: One entry of each type a plugin tool looks an id up as, keyed by the tool
#: name's prefix: a tool's entry-id arguments name the entry of its type.
TYPED = {
    "sw_": ("bl-one", "backlog_item", "kind: feature\nstatus: proposed\n"),
    "investigation_": ("claim-one", "claim", "assertion: A\n"),
    "wiki_": ("art-one", "article", ""),
    "social_": ("wu-one", "writeup", ""),
}


def _kb_dirs(tmp_path):
    roots = {}
    for name in KBS:
        root = tmp_path / name
        (root / "notes").mkdir(parents=True)
        (root / "notes" / "one.md").write_text("---\nid: one\ntitle: One\ntype: note\n---\nb\n")
        for eid, kind, extra in TYPED.values():
            (root / "notes" / f"{eid}.md").write_text(
                f"---\nid: {eid}\ntitle: {eid}\ntype: {kind}\n{extra}---\nb\n"
            )
        roots[name] = root
    return roots


def test_nothing_bound_reads_no_kb():
    """A job, a hook, an embedded caller: no grant bound, so no KB text."""
    with local_process(False):
        assert [may_read_kb(name) for name in KBS] == [False, False]


# -- REST ----------------------------------------------------------------------


@pytest.fixture
def rest_world(tmp_path):
    """Auth on; ``a`` and ``b`` private (``default_role: none``); ``reader``
    holds an explicit read grant on ``a`` only."""
    from pyrite.services.auth_service import AuthService
    from tests.auth_seed import app_db, seed_user

    roots = _kb_dirs(tmp_path)
    w = build_world(
        tmp_path / "w",
        kbs=[
            KBConfig(name=name, path=roots[name], kb_type="generic", default_role="none")
            for name in KBS
        ],
        settings={"auth": AuthConfig(enabled=True)},
    )
    for name in KBS:
        w.index(name)
    db = app_db(w.client.app)
    admin = seed_user(db, "admin", role="admin")
    reader = seed_user(db, "reader", role="read")
    auth = AuthService(db, w.config.settings.auth)
    auth.grant_kb_permission(reader["id"], "a", "read", granted_by=admin["id"])
    tokens = {who: auth.login(who, "password123")[1] for who in ("admin", "reader")}
    _mount_grant_probe(w.client.app)
    yield w, tokens
    w.close()


def _api_router(app):
    """The app's own ``/api`` router (FastAPI includes routers lazily, so it is
    found by its prefix among the included routers, not in a flat route list)."""
    for route in app.routes:
        router = getattr(route, "original_router", None)
        if router is not None and getattr(router, "prefix", None) == "/api":
            return router
    raise AssertionError("the app has no /api router")


def _mount_grant_probe(app):
    """``GET /api/_grant_probe``: what ``may_read_kb`` answers inside a request,
    behind exactly the dependencies the app's own ``/api`` router carries (read
    off the live router, so the probe cannot drift from it)."""
    from fastapi import APIRouter

    def probe() -> dict:
        return {name: may_read_kb(name) for name in KBS}

    router = APIRouter(prefix="/api", dependencies=list(_api_router(app).dependencies))
    router.add_api_route("/_grant_probe", probe, methods=["GET"])
    app.include_router(router)


def test_the_probe_carries_the_binding(tmp_path):
    """The probe is only as good as its dependencies: they include the binding."""
    from pyrite.config import PyriteConfig, Settings
    from pyrite.server.api import bind_acting_account, create_app

    app = create_app(PyriteConfig(settings=Settings(index_path=tmp_path / "index.db")))
    deps = [d.dependency for d in _api_router(app).dependencies]
    assert bind_acting_account in deps, deps


def _probe(client, token=None) -> dict:
    client.cookies.clear()
    if token is not None:
        client.cookies.set("pyrite_session", token)
    r = client.get("/api/_grant_probe")
    assert r.status_code == 200, r.text
    return r.json()


def test_rest_binds_only_the_kbs_the_signed_in_caller_reads(rest_world):
    w, tokens = rest_world
    assert _probe(w.client, tokens["reader"]) == {"a": True, "b": False}


@pytest.mark.control(reason="an unscoped admin reads every KB: the grant is the routes' own rule")
def test_rest_binds_every_kb_for_an_unscoped_admin(rest_world):
    w, tokens = rest_world
    assert _probe(w.client, tokens["admin"]) == {"a": True, "b": True}


def test_rest_grant_does_not_outlive_its_request(rest_world):
    """Each request binds its own grant: a reader's request after an admin's
    is answered from the reader's grant alone."""
    w, tokens = rest_world
    _probe(w.client, tokens["admin"])
    assert _probe(w.client, tokens["reader"]) == {"a": True, "b": False}


def _default_role_world(tmp_path):
    """Auth on, anonymous visitors allowed to read; KB ``a`` readable through
    its ``default_role``, KB ``b`` private."""
    roots = _kb_dirs(tmp_path)
    return build_world(
        tmp_path / "w",
        kbs=[
            KBConfig(name="a", path=roots["a"], kb_type="generic", default_role="read"),
            KBConfig(name="b", path=roots["b"], kb_type="generic", default_role="none"),
        ],
        settings={"auth": AuthConfig(enabled=True, anonymous_tier="read")},
    )


def test_rest_anonymous_reads_no_kb_even_where_anonymous_may_read(tmp_path):
    """Maintainer decision on D4 (2026-11-16): an anonymous visitor never sees
    text quoted from a KB file, whatever the KB's ``default_role`` allows, so
    its grant covers no KB."""
    w = _default_role_world(tmp_path)
    try:
        _mount_grant_probe(w.client.app)
        assert _probe(w.client) == {"a": False, "b": False}
    finally:
        w.close()


def test_rest_signed_in_caller_keeps_a_grant_through_default_role(tmp_path):
    """Signed-in callers keep D4 as built: a reader with no explicit grant who
    reaches KB ``a`` only through its ``default_role`` is granted it."""
    from pyrite.services.auth_service import AuthService
    from tests.auth_seed import app_db, seed_user

    w = _default_role_world(tmp_path)
    try:
        db = app_db(w.client.app)
        seed_user(db, "admin", role="admin")
        seed_user(db, "reader", role="read")
        token = AuthService(db, w.config.settings.auth).login("reader", "password123")[1]
        _mount_grant_probe(w.client.app)
        assert _probe(w.client, token) == {"a": True, "b": False}
    finally:
        w.close()


# -- MCP -----------------------------------------------------------------------


def _arguments(name: str, tool: dict) -> dict:
    """Arguments that satisfy ``tool``'s required fields: KB ``a`` for any KB
    field, the entry of the tool's type (``TYPED``, else ``one``) for any id
    field, and a plain value of the declared type for the rest."""
    schema = tool.get("inputSchema") or {}
    properties = schema.get("properties") or {}
    entry = next((eid for prefix, (eid, *_r) in TYPED.items() if name.startswith(prefix)), "one")
    args: dict = {"kb_name": "a"}
    for field in schema.get("required") or []:
        if field in args:
            continue
        kind = (properties.get(field) or {}).get("type")
        enum = (properties.get(field) or {}).get("enum")
        if field == "kb" or field.endswith("_kb"):
            args[field] = "a"
        elif field.endswith(("_id", "_ids")) and kind != "array":
            args[field] = entry
        elif field.endswith("_ids"):
            args[field] = [entry]
        elif enum:
            args[field] = enum[0]
        elif kind in ("integer", "number"):
            args[field] = 1
        elif kind == "boolean":
            args[field] = False
        elif kind == "array":
            args[field] = ["one"]
        elif kind == "object":
            args[field] = {}
        else:
            args[field] = "one"
    if not any(f.endswith(("_id", "_ids")) for f in args):
        # An id the schema leaves optional but the tool's locator asks for
        # (``sw_prioritize``: one of item_ids, item_id, after, before).
        for field in ("item_id", "entry_id"):
            if field in properties:
                args[field] = entry
                break
    return args


@pytest.fixture
def mcp_world(tmp_path):
    roots = _kb_dirs(tmp_path)
    w = build_world(
        tmp_path / "w",
        kbs=[KBConfig(name=name, path=roots[name], kb_type="generic") for name in KBS],
        rest=False,
    )
    for name in KBS:
        w.index(name)
    yield w
    w.close()


def _probe_every_tool(server, **dispatch) -> tuple[dict, dict]:
    """``(grant each handler saw, refusal of each tool that never reached it)``."""
    seen: dict[str, tuple[bool, bool]] = {}
    refused: dict[str, str] = {}
    for name, tool in server.tools.items():

        def probe(_arguments, _name=name, **_kwargs):
            seen[_name] = (may_read_kb("a"), may_read_kb("b"))
            return {"ok": True}

        tool["handler"] = probe
        # Its own client id: the write tier's rate limit is per client.
        result = server._dispatch_tool(
            name, _arguments(name, tool), client_id=f"probe-{name}", **dispatch
        )
        if name not in seen:
            refused[name] = str(result)[:160]
    return seen, refused


@pytest.mark.parametrize("tier", ["read", "write", "admin"])
def test_mcp_binds_only_the_kbs_the_caller_reads_for_every_tool(mcp_world, tier):
    from pyrite.services.access_policy import Principal

    server = mcp_world.mcp(tier)
    with local_process(False):
        # A global role that lets every tool past its role checks; the call is
        # still scoped to the KBs passed in, which is what the grant follows.
        seen, refused = _probe_every_tool(
            server,
            readable_kbs={"a"},
            writable_kbs={"a"},
            principal=Principal.user(7, "admin"),
        )
    assert refused == {}, f"these tools never reached their handler: {refused}"
    wider = {name: grant for name, grant in seen.items() if grant != (True, False)}
    assert wider == {}, f"a grant other than the caller's own: {wider}"


def test_mcp_anonymous_principal_binds_no_grant(mcp_world):
    """D4 over MCP: an anonymous principal reads no KB text, even where its
    readable set includes the KB."""
    from pyrite.services.access_policy import Principal

    server = mcp_world.mcp("read")
    with local_process(False):
        seen, refused = _probe_every_tool(
            server, readable_kbs={"a"}, principal=Principal.anonymous("read")
        )
    assert refused == {}, refused
    assert set(seen.values()) == {(False, False)}, seen


def test_mcp_with_no_principal_binds_no_grant(mcp_world):
    """An embedded caller (no principal) keeps its own binding: nothing, so no KB."""
    server = mcp_world.mcp("read")
    with local_process(False):
        seen, refused = _probe_every_tool(server, readable_kbs={"a"})
    assert refused == {}, refused
    assert set(seen.values()) == {(False, False)}, seen


# -- every binding, listed -------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent
#: Every place outside ``pyrite/services/acting.py`` (which defines the context
#: and its default, None) that binds the read grant, keyed ``(module,
#: function)``, with its count and how it is held: the name of the test that
#: fails without it, or ``reading: ...`` where no outcome changes without it.
GRANT_BINDINGS: dict[tuple[str, str], tuple[int, str]] = {
    ("pyrite/server/api.py", "bind_acting_account"): (
        1,
        "test_rest_binds_only_the_kbs_the_signed_in_caller_reads",
    ),
    # The caller's readable set, and None for an anonymous principal (D4).
    ("pyrite/server/mcp_server.py", "PyriteMCPServer._dispatch_tool_checked"): (
        2,
        "test_mcp_binds_only_the_kbs_the_caller_reads_for_every_tool",
    ),
    ("pyrite/server/api.py", "NobodyActingMiddleware.__call__"): (
        1,
        "reading: resets to None, the deny value, at the start of every request; the "
        "serving process binds no grant (this list holds every binding), so without "
        "it the default None stands and no outcome changes",
    ),
}
_BINDERS = {"read_grant", "bind_read_grant"}


def _grant_bindings() -> tuple[dict[tuple[str, str], int], list[str]]:
    found: dict[tuple[str, str], int] = {}
    bans: list[str] = []
    roots = [REPO / "pyrite", *sorted((REPO / "extensions").glob("*/src"))]
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            rel = str(path.relative_to(REPO))
            if "__pycache__" in path.parts or rel == "pyrite/services/acting.py":
                continue

            def walk(node, stack, rel=rel):
                for child in ast.iter_child_nodes(node):
                    named = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    if named:
                        stack.append(child.name)
                    where = ".".join(stack) or "<module>"
                    func = getattr(child, "func", None)
                    name = (
                        func.id
                        if isinstance(func, ast.Name)
                        else func.attr
                        if isinstance(func, ast.Attribute)
                        else ""
                    )
                    if isinstance(child, ast.Call) and name in _BINDERS:
                        found[(rel, where)] = found.get((rel, where), 0) + 1
                    if isinstance(child, ast.Name) and child.id == "_READ_GRANT":
                        bans.append(f"{rel}: {where}: reaches the grant's context directly")
                    if isinstance(child, ast.Constant) and child.value == "pyrite_read_grant":
                        bans.append(f"{rel}: {where}: names the grant's context")
                    walk(child, stack)
                    if named:
                        stack.pop()

            walk(ast.parse(path.read_text(encoding="utf-8")), [])
    return found, bans


def test_every_grant_binding_is_listed():
    found, bans = _grant_bindings()
    assert not bans, bans
    unlisted = {k: n for k, n in found.items() if n > GRANT_BINDINGS.get(k, (0, ""))[0]}
    assert not unlisted, (
        "the read grant is bound here, unlisted: bind the caller's own KBs, add a "
        f"deny-side test, and list it in GRANT_BINDINGS: {unlisted}"
    )
    stale = sorted(k for k, (n, _how) in GRANT_BINDINGS.items() if found.get(k, 0) < n)
    assert not stale, f"no longer bound there: lower or remove the entry: {stale}"


@pytest.mark.control(reason="the list's bookkeeping: it guards the next binding, not this change")
@pytest.mark.parametrize("site", sorted(GRANT_BINDINGS), ids=lambda s: f"{s[0]}:{s[1]}")
def test_every_grant_binding_names_how_it_is_held(site):
    from tests.test_response_text_is_pyrites import _test_names

    how = GRANT_BINDINGS[site][1]
    assert how.startswith("reading: ") or how in _test_names(), f"{site}: {how}"
