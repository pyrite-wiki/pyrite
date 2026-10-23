"""A test process frees every FastAPI app a test builds (#1098).

FastAPI keeps a strong reference to each dependency and endpoint callable it
classifies, in module-level ``lru_cache``s in ``fastapi/dependencies/models.py``.
``create_app()`` defines its dependencies as closures over the app, and the
fixtures add ``lambda: db`` overrides, so every cached closure kept a whole app
(routes, pydantic schemas, both ``PyriteDB``s) alive. A worker ran hundreds of
apps before the caches evicted anything, and reached 4-5 GB.

The repo-root ``conftest.py`` clears those caches after every test. The check
has to run AFTER that teardown, and a fixture finalizes before it, so the test
registers a hook wrapper that asserts once every other teardown impl has run.

The apps are built in the test body, not through `make_client`: pytest keeps a
test's fixture values in `item.funcargs` until after the whole teardown phase,
so an app built by a fixture's factory is still reachable from the item when
the wrapper looks (every app "alive", #1110). The test builds worlds itself,
closes them, and keeps only weakrefs.
"""

import gc
import time
import weakref

import pytest

pytest.importorskip("fastapi")

APPS_BUILT = 30


@pytest.fixture
def clear_fastapi_caches(request):
    """The root conftest's function; `import conftest` is ambiguous with
    tests/conftest.py and fails under some rootdir/import modes."""
    for plugin in request.config.pluginmanager.get_plugins():
        fn = getattr(plugin, "clear_fastapi_caches", None)
        if callable(fn):
            return fn
    pytest.fail("the repo-root conftest.py does not define clear_fastapi_caches")


def _who_holds(target, depth=8):
    """A shortest chain of referrers from `target` to a module or class namespace,
    or to a live frame of another file (a running thread or generator).

    Only read when the canary fails, so CI says what kept an app alive instead
    of only that one was.
    """
    seen = {id(target)}
    level = [(target, [type(target).__name__])]
    skip = {id(seen)}
    for _hop in range(depth):
        nxt = []
        skip.add(id(level))
        for obj, path in level:
            for ref in gc.get_referrers(obj):
                if id(ref) in seen or id(ref) in skip:
                    continue
                seen.add(id(ref))
                if type(ref).__name__ == "frame":
                    if ref.f_code.co_filename == __file__:
                        continue
                    where = f"{ref.f_code.co_filename.rsplit('/', 1)[-1]}:{ref.f_lineno}"
                    return " <- ".join([*path, f"frame {ref.f_code.co_name} {where}"])
                if isinstance(ref, dict) and "__name__" in ref:
                    return " <- ".join([*path, f"namespace {ref['__name__']}"])
                step = [*path, str(getattr(ref, "__name__", None) or type(ref).__name__)[:60]]
                nxt.append((ref, step))
        level = nxt[:300]
    return f"no module, class or frame within {depth} hops"


def _collect_until_freed(refs, seconds=5.0):
    """gc until every weakref is dead or `seconds` pass; the apps still alive.

    A background thread of an app (an index worker, a sync) can still be
    winding down when teardown ends on a slow runner; a cache entry never
    lets go, so waiting does not hide the leak this guards.
    """
    deadline = time.monotonic() + seconds
    while True:
        gc.collect()
        alive = [app for ref in refs if (app := ref()) is not None]
        if not alive or time.monotonic() >= deadline:
            return alive
        time.sleep(0.1)


def test_fastapi_caches_are_found(clear_fastapi_caches):
    """If a FastAPI release renames or moves the caches, the hook would clear
    nothing and the leak would return quietly; this fails instead."""
    import fastapi.dependencies.models  # noqa: F401

    assert clear_fastapi_caches() >= 1, (
        "the root conftest found no lru_cache in the loaded fastapi modules; "
        "FastAPI changed where it caches callables (see #1098)"
    )


def _build_and_drop_app(tmp_path, n):
    """Build one world, hit it once, close it, and return a weakref to its app.

    Nothing here is returned but the weakref, and this frame ends on return.
    """
    from pyrite.config import KBConfig
    from tests._world import build_world

    work_dir = tmp_path / f"client-{n}"
    world = build_world(
        work_dir, kbs=[KBConfig(name="test-kb", path=work_dir / "kb", kb_type="generic")]
    )
    try:
        ref = weakref.ref(world.app)
        assert world.client.get("/api/kbs").status_code == 200
    finally:
        world.close()
    return ref


def test_apps_a_test_builds_are_freed_after_its_teardown(request, tmp_path):
    # Counts weakrefs to the apps THIS test built, not every FastAPI in the
    # process: another module may hold one of its own apps in a module-level
    # cache (tests/test_every_entry_point_passes_the_policy._real_operations),
    # which is not this test's leak (#1098).
    built: list[weakref.ref] = []

    class _AfterRootTeardown:
        @pytest.hookimpl(wrapper=True)
        def pytest_runtest_teardown(self, item, nextitem):
            result = yield
            if item is request.node:
                alive = _collect_until_freed(built)
                assert not alive, (
                    f"{len(alive)} of the {APPS_BUILT} FastAPI apps this test built are "
                    "still alive after its teardown: something caches their closures "
                    "(#1098; the root conftest should clear it). "
                    f"One is held by: {_who_holds(alive[0]) if alive else ''}"
                )
            return result

    plugin = _AfterRootTeardown()
    request.config.pluginmanager.register(plugin)
    request.addfinalizer(lambda: request.config.pluginmanager.unregister(plugin))

    for n in range(APPS_BUILT):
        built.append(_build_and_drop_app(tmp_path, n))
