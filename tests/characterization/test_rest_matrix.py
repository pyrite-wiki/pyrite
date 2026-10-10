"""Golden (status, body) for every KB-bearing REST operation, across the ADR
§5 principal matrix and the {readable, private, missing, no_default_role} KB
axis (ADR-0037 theme 0).

Regenerate: ``PYRITE_CHARACTERIZATION_REGENERATE=1 .venv/bin/pytest
tests/characterization/test_rest_matrix.py -n 0``, then review the diff to
``tests/characterization/goldens/rest.*.json`` and commit it as its own
reviewed change. Never set in CI or the pre-push hook.

**What "every operation" means here.** `surfaces.kb_bearing_rest_operations`
narrows `tests/_surface_inventory.py`'s 144 REST operations to the 74 that a
read- or write-scoping dependency (or an explicit, individually-reasoned
`INLINE_ACCESS_DECIDING_ROUTES` entry) actually attaches to (instance
administration, `/auth/*`, and static/site routes serve no KB content and
have no per-KB principal matrix to characterize -- see that module's
docstring and `REST_ACCESS_EXCLUSIONS` below for the pinned, counted list of
what is deliberately out, and `test_surface_completeness.py` for the test
that every one of the 143 distinct (method, path) routes is in one list or
the other, no third possibility). `rest_calls.build_call` supplies one real HTTP
call per route, targeting a KB name; a route this harness cannot meaningfully
drive without new fixture machinery (a registered repo, a review row, ...)
returns a ``{"skip": reason}`` marker instead of request kwargs, recorded as
skipped rather than silently wrong -- see the report for the full skip list.

**Principals x KB states.** Every one of `world.py`'s seven principals is
run against `READABLE`, `PRIVATE`, `MISSING` and `NO_DEFAULT_ROLE` (28 cases
per route); a route's golden key is
``"{METHOD} {path} | {principal} | {kb_state}"``.

**The run set is the golden, not a live filter (#476 blocker 1).** A
migration that renames the scoping dependency `surfaces.py` matches
(`requires_kb_read`, `requires_kb_tier`) makes `kb_bearing_rest_operations`
return fewer routes -- silently, with no test failure, because the loop that
used to drive `ops = kb_bearing_rest_operations(...)` directly simply never
tried the dropped route again. Cold review (#476) proved it: renaming the
dependency set left all 8 REST tests green. So this file computes BOTH sets
per principal -- `live` (today's `kb_bearing_rest_operations`, i.e. what the
code currently scopes) and `golden` (what's on disk for this principal) --
and runs the UNION. A route in `live` but not `golden` is new and gets "no
golden recorded" (`assert_matches`, unchanged). A route in `golden` but not
`live` is the dangerous case: it dropped out of the scoping the ADR-0037
guard is meant to enforce, and `test_no_golden_key_is_orphaned` fails loudly
naming it, rather than the matrix quietly running one route fewer. The
overall counts (74 REST operations, 106 MCP tools) are pinned in
`test_surface_counts_are_pinned` below, in this file for REST.
"""

from __future__ import annotations

import pytest

from tests.characterization.golden_io import MismatchCollector, load, regenerating, save
from tests.characterization.normalize import normalize_rest_body
from tests.characterization.rest_calls import build_call
from tests.characterization.surfaces import kb_bearing_rest_operations
from tests.characterization.world import MISSING, NO_DEFAULT_ROLE, PRIVATE, READABLE

# Not @pytest.mark.core -- see test_global_access.py's comment: core is an
# exact, pinned smoke-set file list (tests/test_test_affected.py), and this
# suite is deliberately heavier than that set. test-affected's import walker
# still selects this file whenever a branch touches pyrite.server.api or a
# module it imports.

GOLDEN_NAME = "rest"
KB_STATES = (READABLE, PRIVATE, MISSING, NO_DEFAULT_ROLE)
PINNED_REST_OPERATION_COUNT = 74


def _golden_routes_for(principal_name: str, golden: dict) -> set[tuple[str, str]]:
    """Every distinct (method, path) this principal has a golden case for,
    parsed back out of the golden's own key format."""
    routes: set[tuple[str, str]] = set()
    prefix_suffix = f" | {principal_name} | "
    for key in golden:
        if prefix_suffix not in key:
            continue
        route_part = key.split(prefix_suffix)[0]
        method, path = route_part.split(" ", 1)
        routes.add((method, path))
    return routes


_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# A GET route whose handler mutates as a side effect of reading -- HTTP
# method is otherwise a reliable enough proxy for "runs against `world`
# unmutated" (#476 round-2 blocker 1's fix), but this one is a documented
# exception: `GET /api/daily/{date_str}` auto-creates the day's note if it
# doesn't exist yet (`get_or_create_daily_note`, see rest_calls.py's own
# comment on `_unique_date`). Routed to `write_world` like any write method,
# so its creation can never permanently add a `daily-<date>` entry to the
# read-only world's listings -- which is exactly what happened before this
# override existed: the golden recorded during a serial regenerate carried
# every daily-note case's creation forward into every later listing case in
# the same session, and an `-n4` run (a fresh worker, fewer/different cases
# landing before a given listing) reproduced a different subset, so the
# comparison was nondeterministic depending on run order and worker count.
_MUTATING_GET_ROUTES = {("GET", "/api/daily/{date_str}")}

# The mirror case: a POST (or PUT/PATCH/DELETE) route that never mutates --
# HTTP method says "write", `_WRITE_METHODS` would route it to `write_world`,
# but its handler only reads, so it belongs in `world` instead. Each one
# below was read (not assumed): `preview_collection_query` and
# `resolve_batch`'s own docstrings/bodies call only read-side service
# methods, and `render_template` renders a string from existing content with
# no persistence call anywhere in its body -- none of the three has a write-
# tier dependency at all (only `requires_kb_read()`), unlike every other
# POST/PUT/PATCH/DELETE route in this surface, which all also depend on
# `requires_tier("write")`/`requires_kb_tier("write")`. Keeping these in
# `write_world` was the second half of the round-2 blocker-1 nondeterminism:
# a listing-shaped assertion (`entries`, identity-projected) run against a
# module-scoped world that every OTHER principal's write cases also mutate,
# in whatever order xdist happens to schedule them, can never be a stable
# golden -- moving these three back to the unmutated `world` is what makes
# them stable, the same as any other read case.
_NON_MUTATING_WRITE_METHOD_ROUTES = {
    ("POST", "/api/collections/query-preview"),
    ("POST", "/api/entries/resolve-batch"),
    ("POST", "/api/kbs/{kb_name}/templates/{template_name}/render"),
}


class TestRestPrincipalMatrix:
    """One test per principal keeps a failure's pytest id naming exactly
    who was refused (or let through) wrongly, without a huge parametrize id
    blowing up -- each principal's test parametrizes over routes x KB state
    only.

    **Read cases run against `world`; write cases run against `write_world`**
    (#476 round-2 blocker 1): a GET route only ever reads, so it is safe
    against the shared, session-scoped, never-mutated `world` fixture,
    where every KB's content is exactly what `world.py` seeded and NOTHING
    else -- which is what lets `normalize_rest_body` keep a listing's real
    `(kb_name, id)` identity instead of blanking it (a leaked private-KB row
    now shows up as a real mismatch, not laundered into the same
    `<ENUMERATION_CONTENT>` marker every case produces). A POST/PUT/PATCH/
    DELETE route mutates content, so it runs against `write_world` instead,
    a SEPARATE, module-scoped world write cases share with each other
    (round 1's per-case identity uniqueness already keeps them from
    colliding) but never with `world`. One GET route is an exception to the
    method-based split: `_MUTATING_GET_ROUTES` names it explicitly and
    routes it to `write_world` too, because HTTP method alone is not a safe
    proxy for "this route never mutates" -- see that constant's comment.
    """

    @staticmethod
    def _run(world, route_filter, principal_name, golden, collector):
        principal = world.principals[principal_name]
        live_routes = {
            (op.method, op.path)
            for op in kb_bearing_rest_operations(world.app)
            if route_filter((op.method, op.path))
        }
        golden_routes = {
            (m, p) for m, p in _golden_routes_for(principal_name, golden) if route_filter((m, p))
        }
        # The union, not just `live_routes`: a route only in `golden_routes`
        # dropped out of today's scoping (a renamed dependency, say) and
        # MUST still be driven -- that is exactly the case #476 blocker 1
        # found silently passing. `test_no_golden_key_is_orphaned` below
        # additionally fails by name on every such route; running it here
        # too means its (status, body) is captured in the failure output,
        # not just its existence.
        all_routes = live_routes | golden_routes
        skipped = []
        for method, path in sorted(all_routes):
            for kb_state in KB_STATES:
                # Releases this world's DB's per-thread fallback SQLAlchemy
                # sessions before every call (see
                # `World.release_idle_connections`'s docstring) -- without
                # it, this many `TestClient` requests against the shared
                # `get_db` override exhausts the connection pool partway
                # through one principal's route sweep, and the request that
                # finds it empty hangs forever rather than erroring
                # (confirmed with faulthandler while building this harness;
                # a sparser interval was not reliably enough headroom for a
                # write-tier principal's heavier per-request session use).
                world.release_idle_connections()
                key = f"{method} {path} | {principal_name} | {kb_state}"
                try:
                    kwargs = build_call(
                        world, method, path, kb_state, call_key=f"{principal_name}-{kb_state}"
                    )
                except KeyError:
                    # A route with no REST_CALL_SPECS entry at all (should
                    # not happen for a live route -- rest_calls.py's own
                    # docstring says every one of the 74 gets an entry --
                    # but a golden-only, dropped route's PATH may no longer
                    # resolve to anything rest_calls.py recognises if the
                    # route itself was removed, not just re-scoped). Record
                    # it as its own failure shape rather than crashing the
                    # whole principal's run.
                    collector.check(
                        GOLDEN_NAME,
                        key,
                        {"no_call_spec": True},
                        golden,
                    )
                    continue
                if "skip" in kwargs:
                    skipped.append((key, kwargs["skip"]))
                    continue
                url = kwargs.pop("url")
                try:
                    resp = world.client.request(
                        method,
                        url,
                        headers=principal.rest_headers or None,
                        cookies=principal.rest_cookies or None,
                        **kwargs,
                    )
                except Exception as exc:  # noqa: BLE001
                    # An unhandled server-side exception (TestClient's
                    # default re-raises it rather than turning it into a
                    # 500 body) -- a real production bug this call tripped
                    # over, not an authorization behaviour. Recorded as its
                    # own golden shape so the matrix does not abort and the
                    # bug is pinned rather than silently retried away; see
                    # the report for what this found and #<issue>.
                    actual = {"unhandled_exception": f"{type(exc).__name__}: {exc}"}
                    collector.check(GOLDEN_NAME, key, actual, golden)
                    continue
                try:
                    body = resp.json()
                except ValueError:
                    body = resp.text
                # normalize_rest_body blanks only this route's VOLATILE
                # fields (a count, a timestamp, a generated id) -- see its
                # docstring and REST_ENUMERATION_SENSITIVE_FIELDS in
                # normalize.py, which keeps the sorted set of (kb, id) pairs
                # or KB names a listing/search body actually returns, so a
                # scoping leak (a private KB's row appearing where it must
                # not) still shows up as a mismatch. `status` is untouched.
                normalised_body = normalize_rest_body(method, path, body, tmpdir=str(world.tmpdir))
                actual = {"status": resp.status_code, "body": normalised_body}
                collector.check(GOLDEN_NAME, key, actual, golden)
        return skipped

    @pytest.mark.parametrize(
        "principal_name",
        [
            "anonymous",
            "read_key",
            "write_key",
            "admin_key",
            "global_user",
            "local_user",
            "granted_user",
        ],
    )
    def test_principal(self, world, write_world, principal_name):
        golden = load(GOLDEN_NAME)
        collector = MismatchCollector()
        skipped = self._run(
            world,
            lambda route: (
                (route[0] == "GET" and route not in _MUTATING_GET_ROUTES)
                or route in _NON_MUTATING_WRITE_METHOD_ROUTES
            ),
            principal_name,
            golden,
            collector,
        )
        skipped += self._run(
            write_world,
            lambda route: (
                (route[0] in _WRITE_METHODS or route in _MUTATING_GET_ROUTES)
                and route not in _NON_MUTATING_WRITE_METHOD_ROUTES
            ),
            principal_name,
            golden,
            collector,
        )
        if regenerating():
            save(GOLDEN_NAME, golden, principal_scope=principal_name)
        # Recorded via a module-level accumulator so the report's skip list
        # is the union across principals (a route's skip reason does not
        # depend on the principal), deduplicated by route.
        _SKIPPED.update(dict(skipped))
        collector.assert_clean()


_SKIPPED: dict[str, str] = {}


def test_skip_list_is_reported(world, write_world):
    """Not a golden assertion: prints the skip list to the pytest report
    (`-s` or a failure) so a human reads it, per the theme's "list and skip
    with a reason" acceptance. Runs last (name sorts after test_principal's
    module, and pytest here runs classes before a trailing module-level
    def in source order) -- if this shows 0 the matrix test above did not
    run first; check test order.

    Probes write routes against `write_world`, not `world`: `build_call`
    itself can have side effects for a write spec (creating a disposable
    entry via `_writable_target`), and this probe must never touch the
    read-only world even when it isn't making a real HTTP request.
    """
    ops = kb_bearing_rest_operations(world.app)
    reasons: dict[str, str] = {}
    for op in ops:
        route = (op.method, op.path)
        is_write = (
            op.method in _WRITE_METHODS or route in _MUTATING_GET_ROUTES
        ) and route not in _NON_MUTATING_WRITE_METHOD_ROUTES
        target = write_world if is_write else world
        for kb_state in KB_STATES:
            kwargs = build_call(target, op.method, op.path, kb_state)
            if "skip" in kwargs:
                reasons[f"{op.method} {op.path}"] = kwargs["skip"]
    print(f"\n{len(reasons)} REST route(s) skipped (not driven by this harness):")
    for route, reason in sorted(reasons.items()):
        print(f"  {route}: {reason}")


def test_no_golden_key_is_orphaned(world):
    """A route in the golden but no longer in the LIVE surface fails here,
    by name (#476 blocker 1). This is the direct proof cold review demanded:
    renaming the scoping dependency `surfaces.py` matches makes
    `kb_bearing_rest_operations` return fewer routes, and a loop driven only
    by that live set would just try one route fewer -- silently green.
    Comparing against the GOLDEN's own route set (parsed back out of its
    keys, not re-derived from today's code) is what makes the drop visible:
    a route the golden still has a case for, that the live surface no
    longer includes, is exactly what happened when the dependency renamed.
    """
    golden = load(GOLDEN_NAME)
    live_routes = {(op.method, op.path) for op in kb_bearing_rest_operations(world.app)}
    all_golden_routes: set[tuple[str, str]] = set()
    for principal_name in (
        "anonymous",
        "read_key",
        "write_key",
        "admin_key",
        "global_user",
        "local_user",
        "granted_user",
    ):
        all_golden_routes |= _golden_routes_for(principal_name, golden)
    orphaned = sorted(all_golden_routes - live_routes)
    assert not orphaned, (
        f"{len(orphaned)} route(s) have a golden case but no longer appear in "
        f"kb_bearing_rest_operations -- a scoping dependency likely renamed or a "
        f"route's guard was removed, and this run set no longer covers it: {orphaned}"
    )


def test_surface_counts_are_pinned(world):
    """The number of KB-bearing REST operations, pinned (#476 blocker 1): a
    change to this number -- growing OR shrinking -- means the surface
    filter's idea of "takes a KB or row resource" changed, and a reviewer
    should see that as a diff to this constant, not discover it by accident.
    """
    live_routes = {(op.method, op.path) for op in kb_bearing_rest_operations(world.app)}
    assert len(live_routes) == PINNED_REST_OPERATION_COUNT, (
        f"kb_bearing_rest_operations now returns {len(live_routes)} distinct routes, "
        f"pinned at {PINNED_REST_OPERATION_COUNT} -- update PINNED_REST_OPERATION_COUNT "
        f"deliberately (with a reason for the change) rather than letting it drift."
    )
