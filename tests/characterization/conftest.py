"""Two worlds for the ADR-0037 theme-0 characterization harness.

`world` (session-scoped -- once per xdist worker, since xdist gives each
worker its own process) is READ-ONLY by convention: every read/list/search
case runs against it, and nothing may write through it, so its content is
exactly what `world.py`'s seeding put there for the life of the worker.

`write_world` is a MODULE-scoped world (one fresh `build_world` call per
test *module* that asks for it, never the same object as `world`). The MCP
matrix uses `mcp_write_world` once per principal because saved ADR numbers
depend on earlier writes. Round 1 already
solved individual write CASES colliding with each other inside one world
(unique `call_key`-derived identity fields, disposable entries for a tool
that mutates an existing row); module-level isolation is the next
granularity up -- it keeps a write module's OWN listing verifications
(does the entry I just wrote actually show up) free of whatever a
DIFFERENT module's write cases already added, while still building only a
handful of worlds total rather than one per individual case (which would
also work, just at a much higher, unnecessary cost: this harness's write
surface is a small minority of its whole matrix).
"""

from __future__ import annotations

import pytest

from tests.characterization.world import World, build_world


@pytest.fixture(scope="session")
def world(tmp_path_factory) -> World:
    w = build_world(tmp_path_factory, label="adr0037-read")
    try:
        yield w
    finally:
        w.close()


@pytest.fixture(scope="module")
def write_world(tmp_path_factory) -> World:
    w = build_world(tmp_path_factory, label="adr0037-write")
    try:
        yield w
    finally:
        w.close()


@pytest.fixture
def mcp_write_world(tmp_path_factory) -> World:
    """Keep file-based ADR numbering independent of principal test order."""
    w = build_world(tmp_path_factory, label="adr0037-mcp-write")
    try:
        yield w
    finally:
        w.close()


def _world_kb_names(world: World) -> set[str]:
    """The KB names `world` currently carries, in every place a test could
    leave one behind: `PyriteConfig`'s own list (what `AccessPolicy.kbs_at_tier`
    walks -- #491's repro test leaked here, invisible to any check that only
    reads the DB `kb` table), the registry/DB rows `seed_from_config` and a
    sync create, and `PyriteConfig._db_kb_cache` (#509 round 2 cold read: a
    THIRD place, populated by `KBRegistryService.add_kb`'s call to
    `config.register_db_kbs` -- `get_kb()`/`all_kbs()`/`AccessPolicy.kbs_at_tier`
    all read it too, but it is neither `knowledge_bases` nor a DB row a plain
    `SELECT * FROM kb` sees. A test that registers a KB through
    `KBRegistryService.add_kb` -- REST's `POST /api/kbs`, MCP's
    `kb_registry_add` -- and cleans up only the DB row (`world.db.unregister_kb`)
    leaves this cache populated forever, invisible to this function until this
    fix, so `_world_is_immutable` below never saw the leak: the guard's
    before/after snapshots were identical because neither one ever read this
    cache, not because nothing changed).

    Read together so a leak in any one place is caught, not just the one
    #491's repro happened to leave self-cleaning (its DB row: the very bug it
    pins flips the row's `source` to "user", so the repro's own `DELETE` --
    expected to 403, and asserted as much by its `xfail` -- actually succeeds
    and removes the DB row as a side effect; `world.config`'s list and
    `_kb_by_name` cache are untouched by that DELETE and keep the KB)."""
    config_names = {kb.name for kb in world.config.knowledge_bases}
    db_names = {row["name"] for row in world.db.execute_sql("SELECT name FROM kb")}
    db_kb_cache_names = set(world.config._db_kb_cache)
    return config_names | db_names | db_kb_cache_names


# (nodeid, kb-names-snapshot-BEFORE-it-ran) of the most recent test that
# requested `world` -- the "pending offender" -- checked against world's
# CURRENT state at the START of the NEXT such test, rather than at the
# pending offender's own teardown. Module-global, not a fixture value,
# because pytest's own xfail(strict=True) handling
# (`_pytest.skipping.pytest_runtest_makereport`) re-applies the SAME xfail
# outcome to ANY exception raised in ANY phase of that test item -- setup,
# call, AND teardown -- when the marker has no `raises=` (read directly:
# `elif not rep.skipped and xfailed:` does not branch on `call.when` before
# checking `call.excinfo`). An assertion raised from this fixture's own
# post-yield code during an xfail(strict=True) test's own teardown is
# therefore swallowed as a second, identical XFAIL instead of failing the
# run -- confirmed empirically against `test_kb_protected_live_over_rest`
# (`xfail(strict=True)`, no `raises=`) while writing this guard: the leak was
# real (proven directly against `world.config.knowledge_bases`) but a
# teardown-phase assert on that same item was invisible in the test report.
# Deferring the check to the NEXT world-using item's setup lands the
# assertion in a DIFFERENT item -- one `xfailed` is not stashed for -- so it
# fails for real, while still naming the true offender by nodeid (the
# "before" snapshot is captured at the offender's OWN setup, i.e. before it
# runs, precisely so the comparison is "what did THIS test change", not
# "what changed since the test before it").
_pending_offender: tuple[str, set[str]] | None = None


@pytest.fixture(autouse=True)
def _world_is_immutable(request) -> None:
    """Fails the test BY NAME when a PRIOR test changed which KBs `world`
    (the session-scoped, read-only world every read/list/search case shares)
    knows about -- in `world.config.knowledge_bases`/`_kb_by_name` or the
    registry's DB rows.

    `world` is built once per xdist worker and read by every later
    read-scoped test on that worker: a test that adds a KB to it and does not
    remove it changes what every later test on the same worker sees in a
    principal's readable/writable set, which is exactly the class of
    order-dependent leak this harness exists to eliminate elsewhere (#491).
    Checked on the NEXT world-using test's setup, not this test's own
    teardown -- see `_pending_offender`'s comment for why.

    Only checks tests that actually request `world` (directly or through a
    fixture that does) -- `request.node.fixturenames` names every fixture in
    the test's graph, so this never forces `world` to be built for a test
    that deliberately avoids it (e.g. a bare-config case building its own
    throwaway `PyriteConfig`/`PyriteDB`), and never touches `write_world`,
    which every write case is already free to mutate for its own module's
    life.
    """
    global _pending_offender
    if "world" not in request.fixturenames:
        yield
        return
    world = request.getfixturevalue("world")
    before = _world_kb_names(world)
    if _pending_offender is not None:
        offender_nodeid, offender_before = _pending_offender
        added = before - offender_before
        removed = offender_before - before
        assert not added and not removed, (
            f"{offender_nodeid} changed world's KB set (added={sorted(added)}, "
            f"removed={sorted(removed)}) -- world is session-scoped and shared "
            "by every later read-scoped test on this worker; clean up in the "
            "test itself (try/finally), or use write_world instead."
        )
    _pending_offender = (request.node.nodeid, before)
    yield
