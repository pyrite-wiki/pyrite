"""Every /api route is scoped to the KBs its caller may read -- structurally.

Per-KB read scoping (`authorize(Action.KB_READ, KB | AnyKB)` in
`pyrite/server/authz.py`, which replaced `requires_kb_read` and
`get_readable_kbs` in ADR-0037 theme 3a) is what keeps a private KB
invisible to a caller without a grant. Nothing enforced that a *new*
route picked it up, so sixteen endpoint modules shipped without it and the
rule lived only in a docstring. This test is the enforcement: it walks the real app's routes
and fails for any `/api` route that is neither scoped nor explicitly
allowlisted with a reason.

**What this file proves is two things, not one.** A first version proved
only that a scoping dependency was *attached* to the route, and that was
not enough: the dependency and the handler can read the KB from different
places. `reviews.py` binds `Query(..., alias="kb_name")` while the
resolver looked at `kb` first, so a request naming both was checked
against one KB and served from the other. So the gate now also proves
that **the resolver looks everywhere the handler reads**:
`test_scoped_routes_declare_no_kb_parameter_the_resolver_ignores`
compares each scoped route's declared KB-bearing parameters -- query
names *and* aliases, path params, and `kb`/`kb_name` fields of a body
model -- against `RESOLVED_KB_LOCATIONS`, the set
`pyrite.server.api._resolve_kb_names` actually inspects. A route that
takes a *secondary* KB under a name the resolver does not read is listed
in `SECONDARY_KB_PARAMETERS` with what is known about it. The entries that
used to be listed there are resolved rather than recorded now: `source_kb`
and `target_kb` (`links.py`) joined `KB_PARAM_NAMES` in #186, and
`center_kb` (`/api/graph`) joined it when a private centre was found to
answer differently from a missing one (private #57).

**What the walk cannot see -- recorded, not reviewed.** It visits
`APIRoute`s under `/api` only. Two surfaces are therefore absent rather
than approved, and are listed in `UNREACHABLE_BY_THIS_WALK` below, each
with what covers it elsewhere: `/mcp` is a `Mount` (a whole
sub-application) and `/ws` is a `WebSocketRoute`. Neither is a `FastAPI`
route object with a dependant tree, so nothing *here* says anything about
them. `/mcp` is now scoped and covered by its own pair of tests (#201);
`/ws` is now authenticated and scoped per socket, covered by
`tests/test_websocket_scoping.py` (#218).

**How scoping is detected: the dependency tree, not the handler body.**
Every route's `route.dependant` is walked recursively and each
dependency's callable is matched by qualified name against
`SCOPING_DEPENDENCIES`. Reading the handler's source instead (grep for
`readable_kbs` in the function body) was rejected: it cannot see scoping
that lives in a helper the handler calls, it happily matches a comment or
a variable of the same name, and it gives no way to be sure the check
actually runs on every request. A FastAPI dependency does run on every
request, and the dependant tree is the framework's own record of that.
The cost is a real constraint on authors: **scope a route by declaring
`Depends(authorize(Action.KB_READ, KB))` or taking
`scope: ReadScope = Depends(authorize(Action.KB_READ, AnyKB))`**, never by
calling `readable_kbs()` ad hoc inside the handler. Both forms
appear in the tree; a bare call does not, and this test will fail --
correctly, because such a call is invisible to review.
"""

import inspect
import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from pyrite.server.api import create_app

pytestmark = pytest.mark.core  # the local smoke set; see scripts/test-affected

# Qualified names of the dependencies that scope a route's reads. A route
# whose dependant tree contains any of these is scoped (ADR-0037 theme 3a:
# `authz.authorize` replaced `requires_kb_read()` and `get_readable_kbs`):
#   - authorize(Action.KB_READ, KB)    -- 404s a named KB the caller may not
#                                         read, and hands over the ReadScope
#   - authorize(Action.KB_READ, AnyKB) -- hands the handler the ReadScope to
#                                         filter by
SCOPING_DEPENDENCIES = {
    "pyrite.server.authz.authorize.<locals>.authorize_kb_read_named",
    "pyrite.server.authz.authorize.<locals>.authorize_kb_read_any",
}

# The parameter names `_resolve_kb_names` inspects, in every location it
# looks (query, path, JSON body). A scoped route that declares a
# KB-bearing parameter outside this set reads its KB from somewhere the
# resolver does not look -- which is exactly the hole that let a request
# name two KBs and be checked against the wrong one.
RESOLVED_KB_LOCATIONS = {"kb", "kb_name", "source_kb", "target_kb", "center_kb"}

# A route's *primary* KB is the one the resolver checks. A route can also
# take a **secondary** KB parameter -- "centre the graph there" -- which the
# resolver does not see, because it is not in `KB_PARAM_NAMES`. Each one is
# listed here with what is known about it, so the gate stays meaningful (it
# fires on any name not listed) without silently blessing the ones that
# exist.
#
# The `links.py` pair (`target_kb`, `source_kb`) used to be here as part 2
# of this work. They are gone because the resolver reads them now (#186).
SECONDARY_KB_PARAMETERS: dict[tuple[str, str], dict[str, str]] = {
    ("GET", "/api/search"): {
        "group_by_kb": "not a KB name: a bool controlling result grouping.",
        "limit_per_kb": "not a KB name: an int cap per KB.",
    },
}

# Surfaces this walk structurally cannot reach: not `APIRoute`s, so they
# have no dependant tree to inspect. Each entry says what covers it
# *elsewhere*, or that nothing does -- so their absence from the results
# above is never read as approval.
UNREACHABLE_BY_THIS_WALK = {
    "/mcp": (
        "Mount (a sub-application, mcp_routes.py), so this walk still cannot "
        "see it -- but it is no longer unreviewed. MCP over HTTP now resolves "
        "the caller's readable set per connection through the SAME helper the "
        "routes above use (api.readable_kbs_for_user -- one rule, two "
        "callers), and enforces it at its three content chokepoints: "
        "_dispatch_tool, _read_resource and _get_prompt. What covers it: "
        "tests/test_mcp_read_scoping.py (behavioural, driving real MCP "
        "sessions) and tests/test_mcp_tool_registry_is_scoped.py (structural "
        "-- it enumerates the tool registry, which is what a Mount has "
        "instead of a dependant tree). #201."
    ),
    "/ws": (
        "WebSocketRoute, so this walk still cannot see it -- but it is no "
        "longer unreviewed. The handshake is authenticated with the same "
        "credential resolver as /mcp (mcp_routes._resolve_credential) and a "
        "cross-origin handshake is refused; each socket stores the readable "
        "set from api.readable_kbs_for_user, resolved once at connect, and "
        "ConnectionManager.broadcast sends an event naming a KB only to "
        "sockets that may read it. What covers it: "
        "tests/test_websocket_scoping.py (behavioural, real sockets over "
        "TestClient, the private-KB event driven through POST /api/clip). #218."
    ),
}

# Routes that serve no KB content, or whose scoping is part 2 of this work.
# EVERY entry carries a reason. A route that leaks KB content does not
# belong here -- scope it instead.
ALLOWLIST: dict[tuple[str, str], str] = {
    # -- serves no KB content -------------------------------------------
    (
        "GET",
        "/api/collections/types",
    ): "serves no KB content: static + plugin collection type descriptors",
    # -- write routes: guarded by requires_kb_tier('write')/requires_tier -
    # A write-tier caller on a KB can necessarily read it, so the per-KB
    # write guard subsumes the read guard. Listed rather than silently
    # skipped so that a write route losing its guard is still visible here.
    #
    # Every reason starting "write route" names the guard it relies on, and
    # `test_write_route_allowlist_reasons_name_the_guard_the_route_has`
    # checks that claim against the route's real dependant tree. The claim
    # used to be asserted only: `POST /api/clip` sat here as
    # "requires_kb_tier('write')" while it had only `requires_tier`.
    ("POST", "/api/entries"): "write route: requires_kb_tier('write') subsumes read",
    ("PUT", "/api/entries/{entry_id}"): "write route: requires_kb_tier('write') subsumes read",
    ("PATCH", "/api/entries/{entry_id}"): "write route: requires_kb_tier('write') subsumes read",
    ("DELETE", "/api/entries/{entry_id}"): "write route: requires_kb_tier('write') subsumes read",
    ("POST", "/api/entries/import"): "write route: requires_kb_tier('write') subsumes read",
    ("POST", "/api/clip"): "write route: requires_kb_tier('write') subsumes read",
    ("POST", "/api/collections"): "write route: requires_kb_tier('write') subsumes read",
    ("POST", "/api/daily/{date_str}"): "write route: requires_kb_tier('write') subsumes read",
    ("PUT", "/api/starred/reorder"): (
        "write route: requires_tier('write'); serves no KB content, and stars are "
        "per user, so it can only reorder the caller's own rows"
    ),
    ("POST", "/api/tasks/{task_id}/claim"): "write route: requires_kb_tier('write') subsumes read",
    ("POST", "/api/reviews"): "write route: requires_kb_tier('write') subsumes read",
    ("DELETE", "/api/reviews/{review_id}"): "write route: requires_kb_tier('write') subsumes read",
    # -- part 2: meta/admin surfaces ------------------------------------
    ("GET", "/api/plugins"): "part 2: admin.py",
    ("GET", "/api/plugins/{name}"): "part 2: admin.py",
    ("GET", "/api/ai/status"): "part 2: admin.py",
    ("POST", "/api/ai/test"): "part 2: admin.py",
    ("GET", "/api/usage/me"): "part 2: admin.py",
    ("GET", "/api/admin/usage"): "part 2: admin.py",
    ("GET", "/api/index/embed-status"): "part 2: admin.py",
    ("GET", "/api/index/jobs"): "part 2: admin.py",
    ("GET", "/api/index/jobs/{job_id}"): "part 2: admin.py",
    ("POST", "/api/index/sync"): "part 2: admin.py",
    ("POST", "/api/site/render"): "part 2: admin.py",
    ("POST", "/api/kbs"): "part 2: admin.py -- KB registration, admin tier",
    ("PUT", "/api/kbs/{name}"): "part 2: admin.py",
    ("DELETE", "/api/kbs/{name}"): "part 2: admin.py",
    ("PUT", "/api/kbs/{name}/default-role"): "part 2: admin.py",
    ("GET", "/api/kbs/{name}/permissions"): "part 2: admin.py",
    ("POST", "/api/kbs/{name}/permissions"): "part 2: admin.py",
    ("POST", "/api/kbs/{name}/reindex"): "part 2: admin.py",
    ("GET", "/api/kbs/ephemeral"): "part 2: admin.py",
    ("POST", "/api/kbs/ephemeral"): "part 2: admin.py",
    ("DELETE", "/api/kbs/ephemeral/{name}"): "part 2: admin.py",
    ("POST", "/api/kbs/gc"): "part 2: admin.py",
    ("GET", "/api/settings"): "part 2: settings_ep.py",
    ("PUT", "/api/settings"): "part 2: settings_ep.py",
    ("GET", "/api/settings/{key}"): "part 2: settings_ep.py",
    ("PUT", "/api/settings/{key}"): "part 2: settings_ep.py",
    ("DELETE", "/api/settings/{key}"): "part 2: settings_ep.py",
    ("POST", "/api/repos/fork"): "part 2: repos.py -- open PR #161 touches it",
    ("POST", "/api/repos/subscribe"): "part 2: repos.py -- open PR #161 touches it",
    ("GET", "/api/github/repos"): "part 2: repos.py -- GitHub account listing, not KB content",
    ("GET", "/api/worktree/status"): "part 2: worktree.py",
    ("GET", "/api/worktree/changes"): "part 2: worktree.py",
    ("POST", "/api/worktree/reset"): "part 2: worktree.py",
    ("POST", "/api/worktree/submit"): "part 2: worktree.py",
    ("GET", "/api/admin/merge-queue"): "part 2: worktree.py",
    ("GET", "/api/admin/merge-queue/{username}/diff"): "part 2: worktree.py",
    ("POST", "/api/admin/merge-queue/{username}/merge"): "part 2: worktree.py",
    ("POST", "/api/admin/merge-queue/{username}/reject"): "part 2: worktree.py",
    ("POST", "/api/kbs/{kb_name}/commit"): "part 2: git_ops.py",
    ("POST", "/api/kbs/{kb_name}/publish"): "part 2: git_ops.py",
    ("POST", "/api/kbs/{kb_name}/push"): "part 2: git_ops.py",
}

HOW_TO_FIX = """
Each route above serves data from a knowledge base without checking which
KBs the caller may read, so a private KB's content reaches a caller who
has no grant on it.

Scope it, in pyrite/server/endpoints/<module>.py:

  * names a KB (`kb` / `kb_name` in query, path or body) -->
        @router.get("/thing", dependencies=[Depends(authorize(Action.KB_READ, KB))])
    which answers 404 KB_NOT_FOUND -- never 403 -- for a KB the caller
    may not read, byte-identical to a KB that does not exist.

  * spans KBs (no kb parameter) -->
        scope: ReadScope = Depends(authorize(Action.KB_READ, AnyKB))
    and push `kb_names=scope.as_set()` into the service/query, as
    endpoints/search.py does. Filtering the rows in Python after the
    fact is not enough where a count, total or has_more would still
    reveal the private rows.

`authorize` lives in pyrite/server/authz.py. A route that genuinely serves
no KB content goes in ALLOWLIST in this file, with a reason.
"""


def _iter_api_routes():
    """Every (method, path, route) under /api in the real application.

    FastAPI's `include_router(prefix=...)` records an `_IncludedRouter`
    wrapper rather than flattening, and a router declared with
    `APIRouter(prefix=...)` has already baked that prefix into each
    `route.path`. So an including prefix is added only when the child
    paths do not already carry it. Cross-checked against `app.openapi()`
    in `test_route_walk_matches_openapi` below.
    """
    app = create_app()

    def walk(routes, prefix=""):
        found = []
        for route in routes:
            if type(route).__name__ == "_IncludedRouter":
                sub = route.original_router
                own = sub.prefix or ""
                children = walk(sub.routes, "")
                add = "" if (own and all(p.startswith(own) for p, _ in children)) else own
                found += [(prefix + add + p, r) for p, r in children]
            elif isinstance(route, APIRoute):
                found.append((prefix + route.path, route))
        return found

    out = []
    for path, route in walk(app.routes):
        if not path.startswith("/api"):
            continue
        for method in sorted(route.methods or ()):
            if method in ("HEAD", "OPTIONS"):
                continue
            out.append((method, path, route))
    return sorted(out, key=lambda t: (t[1], t[0]))


def _dependency_names(dependant) -> set[str]:
    """Qualified names of every dependency in a route's dependant tree."""
    names: set[str] = set()
    for dep in dependant.dependencies:
        call = dep.call
        module = getattr(call, "__module__", "?")
        qualname = getattr(call, "__qualname__", repr(call))
        names.add(f"{module}.{qualname}")
        names |= _dependency_names(dep)
    return names


def _is_scoped(route: APIRoute) -> bool:
    return bool(_dependency_names(route.dependant) & SCOPING_DEPENDENCIES)


def test_route_walk_matches_openapi():
    """The walk sees the same /api routes the app actually serves.

    Guards the walk itself: if FastAPI changes how included routers are
    recorded, this fails loudly rather than silently shrinking the set of
    routes the scoping test checks.
    """
    app = create_app()
    served = {p for p in app.openapi()["paths"] if p.startswith("/api")}
    # `{name:path}` in a route is `{name}` in the OpenAPI document.
    walked = {path.replace(":path}", "}") for _, path, _ in _iter_api_routes()}
    assert walked == served, (
        f"route walk disagrees with the served app\n"
        f"  only in walk:     {sorted(walked - served)}\n"
        f"  only in openapi:  {sorted(served - walked)}"
    )


def test_every_api_route_is_scoped_or_allowlisted():
    """No /api route reads KB content without a read-scoping dependency."""
    unscoped = [
        (method, path, route)
        for method, path, route in _iter_api_routes()
        if not _is_scoped(route) and (method, path) not in ALLOWLIST
    ]
    if unscoped:
        listing = "\n".join(
            f"  {method:6} {path:52} ({route.endpoint.__module__.rsplit('.', 1)[-1]}.py:"
            f"{route.endpoint.__name__})"
            for method, path, route in unscoped
        )
        raise AssertionError(
            f"{len(unscoped)} /api route(s) are not read-scoped:\n{listing}\n{HOW_TO_FIX}"
        )


def _declared_kb_parameters(route: APIRoute) -> set[str]:
    """Every name by which this route's handler can receive a KB.

    Query parameters by both their Python name and their wire alias (a
    handler may bind `kb: str = Query(..., alias="kb_name")` -- the wire
    name is what the resolver must look for), path parameters, and the
    `kb`/`kb_name` fields of any Pydantic body model.
    """
    d = route.dependant
    names: set[str] = set()
    for p in (*d.query_params, *d.path_params):
        if _is_kb_parameter(p.name) or _is_kb_parameter(p.alias or ""):
            names |= {n for n in (p.name, p.alias) if n and _is_kb_parameter(n)}
    for p in d.body_params:
        annotation = getattr(p.field_info, "annotation", None)
        for field in getattr(annotation, "model_fields", {}) or {}:
            if _is_kb_parameter(field):
                names.add(field)
        if _is_kb_parameter(p.name):
            names.add(p.name)
    return names


def _is_kb_parameter(name: str) -> bool:
    """Does this parameter name a knowledge base?

    Deliberately wider than `RESOLVED_KB_LOCATIONS`: the whole point of
    `test_scoped_routes_declare_no_kb_parameter_the_resolver_ignores` is to
    catch a handler that reads its KB from a name the resolver does *not*
    inspect, so this predicate must recognise such a name or the test can
    never fire. Anything that is `kb`, ends in `_kb`, or ends in `_kb_name`
    counts -- `target_kb`, `source_kb`, `center_kb`, `kb_name`.
    """
    return name == "kb" or name.endswith(("_kb", "kb_name"))


def test_scoped_routes_declare_no_kb_parameter_the_resolver_ignores():
    """A scoped route must read its KB from a place the resolver inspects.

    Attaching `authorize(Action.KB_READ, KB)` proves a check runs; it does not prove
    the check looked at the KB the handler will serve from. This closes
    that gap: for every route counted as scoped, each KB-bearing parameter
    the handler declares must be one `_resolve_kb_names` reads, or be
    listed in `SECONDARY_KB_PARAMETERS` with a reason.
    """
    offenders = []
    for method, path, route in _iter_api_routes():
        if not _is_scoped(route):
            continue
        allowed = RESOLVED_KB_LOCATIONS | set(SECONDARY_KB_PARAMETERS.get((method, path), ()))
        unseen = _declared_kb_parameters(route) - allowed
        if unseen:
            offenders.append((method, path, sorted(unseen), route))
    if offenders:
        listing = "\n".join(
            f"  {method:6} {path:52} reads its KB from {names} "
            f"({route.endpoint.__module__.rsplit('.', 1)[-1]}.py:{route.endpoint.__name__})"
            for method, path, names, route in offenders
        )
        raise AssertionError(
            f"{len(offenders)} scoped route(s) declare a KB parameter that "
            f"pyrite.server.api._resolve_kb_names does not inspect, so the "
            f"scoping check and the handler can read different KBs:\n{listing}\n"
            f"Resolver reads: {sorted(RESOLVED_KB_LOCATIONS)} in query (name and "
            f"alias), path and JSON body. Either teach the resolver the new "
            f"location or rename the parameter."
        )


def test_secondary_kb_parameters_all_carry_a_reason_and_still_exist():
    """An entry without a reason is a hole nobody can review; a stale one
    makes this list a worse inventory than no list at all."""
    missing = [
        (key, param)
        for key, params in SECONDARY_KB_PARAMETERS.items()
        for param, reason in params.items()
        if not (reason or "").strip()
    ]
    assert not missing, f"secondary KB parameters with no reason: {missing}"

    declared = {(m, p): _declared_kb_parameters(r) for m, p, r in _iter_api_routes()}
    stale = [
        (key, param)
        for key, params in SECONDARY_KB_PARAMETERS.items()
        for param in params
        if param not in declared.get(key, set())
    ]
    assert not stale, f"listed secondary KB parameters that the route no longer declares: {stale}"


def test_the_resolver_reads_every_location_this_file_claims():
    """`RESOLVED_KB_LOCATIONS` above is the contract the previous test
    enforces; here it is checked against the resolver itself, so the two
    cannot drift apart silently."""
    from pyrite.server.api import KB_PARAM_NAMES

    assert set(KB_PARAM_NAMES) == RESOLVED_KB_LOCATIONS


def test_surfaces_outside_the_walk_are_recorded_as_unreviewed():
    """The two non-APIRoute surfaces still exist and are still unreviewed.

    If `/mcp` or `/ws` ever disappears -- or a third such mount appears --
    this file's statement about what it does not cover has gone stale.
    """
    app = create_app()
    mounted = {
        getattr(r, "path", None)
        for r in app.routes
        if not isinstance(r, APIRoute) and getattr(r, "path", "").startswith(("/mcp", "/ws"))
    }
    assert mounted == set(UNREACHABLE_BY_THIS_WALK), (
        f"non-APIRoute surfaces changed: app has {sorted(mounted)}, this file "
        f"records {sorted(UNREACHABLE_BY_THIS_WALK)}"
    )


def test_a_exhausting_the_rate_limit_does_not_leak_into_the_next_test():
    """Half one: spend the whole per-minute budget on a limited route.

    `pyrite.server.api.limiter` is a process-global `Limiter` with in-memory
    storage that no `create_app()` clears, so without a per-test reset the
    429s this produces are inherited by whatever runs next -- which is why
    both scoping files failed in a batch under load and passed on an idle
    machine (the wall-clock minute, not the test, decided). The pair of
    tests here is the regression: this one burns the budget, and the next
    asserts it came back.
    """
    client = TestClient(create_app())
    codes = {client.get("/api/kbs").status_code for _ in range(130)}
    assert 429 in codes, (
        "the limiter did not engage at all -- this pair of tests no longer "
        "proves anything about the reset; check the limit on /api/kbs"
    )


def test_b_the_next_test_gets_a_fresh_rate_limit_budget():
    """Half two: the budget the previous test spent is back.

    Fails without the autouse `_reset_rate_limiter` fixture in
    tests/conftest.py. Named to sort after its partner, which the walk and
    pytest both run in file order.
    """
    client = TestClient(create_app())
    codes = {client.get("/api/kbs").status_code for _ in range(30)}
    assert 429 not in codes, (
        "rate-limit state leaked from the previous test -- the autouse "
        "_reset_rate_limiter fixture in tests/conftest.py is missing or no "
        "longer applies here, and the scoping suites are load-sensitive again"
    )


def test_allowlist_entries_all_carry_a_reason():
    """An allowlist entry without a reason is a hole nobody can review."""
    missing = [key for key, reason in ALLOWLIST.items() if not (reason or "").strip()]
    assert not missing, f"allowlist entries with no reason: {missing}"


def test_allowlist_has_no_stale_entries():
    """An allowlisted route that no longer exists (or has since been scoped)
    must leave the allowlist, so the list stays an honest inventory of what
    part 2 still owes."""
    live = {(m, p) for m, p, _ in _iter_api_routes()}
    scoped = {(m, p) for m, p, r in _iter_api_routes() if _is_scoped(r)}
    gone = sorted(key for key in ALLOWLIST if key not in live)
    now_scoped = sorted(key for key in ALLOWLIST if key in scoped)
    assert not gone, f"allowlisted routes that no longer exist: {gone}"
    assert not now_scoped, (
        f"allowlisted routes that are now scoped -- remove them from ALLOWLIST: {now_scoped}"
    )


# `requires_kb_tier('write')` or `requires_tier('write')`, as a reason names it.
_GUARD_CLAIM = re.compile(r"\b(requires_kb_tier|requires_tier)\('(\w+)'\)")


def _write_guards(dependant) -> set[tuple[str, str]]:
    """(factory, tier) for every tier guard in a route's dependant tree.

    A guard is the closure `requires_tier(t)` / `requires_kb_tier(t, ...)`
    returns; its qualname names the factory and its closure holds `tier`.
    """
    found: set[tuple[str, str]] = set()
    for dep in dependant.dependencies:
        call = dep.call
        qualname = getattr(call, "__qualname__", "")
        factory = qualname.split(".<locals>", 1)[0]
        if (
            getattr(call, "__module__", "") == "pyrite.server.api"
            and factory in ("requires_tier", "requires_kb_tier")
            and ".<locals>." in qualname
        ):
            tier = inspect.getclosurevars(call).nonlocals.get("tier")
            found.add((factory, tier))
        found |= _write_guards(dep)
    return found


def test_write_route_allowlist_reasons_name_the_guard_the_route_has():
    """An allowlisted write route's reason is checked, not trusted.

    Each "write route" entry must name its guard, and the route's dependant
    tree must contain exactly that guard at that tier. A reason that claims
    `requires_kb_tier('write')` for a route guarded only by `requires_tier`
    -- `POST /api/clip`, until it was fixed -- fails here.
    """
    routes = {(m, p): r for m, p, r in _iter_api_routes()}
    wrong = []
    for key, reason in ALLOWLIST.items():
        if not reason.startswith("write route") or key not in routes:
            continue
        claims = set(_GUARD_CLAIM.findall(reason))
        actual = _write_guards(routes[key].dependant)
        if not claims or not claims <= actual:
            wrong.append(
                f"  {key[0]:6} {key[1]:40} claims {sorted(claims) or 'no guard'}, "
                f"has {sorted(actual) or 'none'}"
            )
    assert not wrong, "write-route allowlist reasons that do not match the route:\n" + "\n".join(
        wrong
    )
