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
"""

import gc
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
    """A shortest chain of referrers from `target` to a module or class namespace.

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
                if id(ref) in seen or id(ref) in skip or type(ref).__name__ == "frame":
                    continue
                seen.add(id(ref))
                if isinstance(ref, dict) and "__name__" in ref:
                    return " <- ".join([*path, f"namespace {ref['__name__']}"])
                step = [*path, str(getattr(ref, "__name__", None) or type(ref).__name__)[:60]]
                nxt.append((ref, step))
        level = nxt[:300]
    return f"no module or class namespace within {depth} hops"


def test_fastapi_caches_are_found(clear_fastapi_caches):
    """If a FastAPI release renames or moves the caches, the hook would clear
    nothing and the leak would return quietly; this fails instead."""
    import fastapi.dependencies.models  # noqa: F401

    assert clear_fastapi_caches() >= 1, (
        "the root conftest found no lru_cache in the loaded fastapi modules; "
        "FastAPI changed where it caches callables (see #1098)"
    )


def test_apps_a_test_builds_are_freed_after_its_teardown(request, make_client):
    # Counts weakrefs to the apps THIS test built, not every FastAPI in the
    # process: another module may hold one of its own apps in a module-level
    # cache (tests/test_every_entry_point_passes_the_policy._real_operations),
    # which is not this test's leak and made the canary fail in CI (#1098).
    built: list[weakref.ref] = []

    class _AfterRootTeardown:
        @pytest.hookimpl(wrapper=True)
        def pytest_runtest_teardown(self, item, nextitem):
            result = yield
            if item is request.node:
                gc.collect()
                alive = [app for ref in built if (app := ref()) is not None]
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

    for _ in range(APPS_BUILT):
        client, _config, _db = make_client()
        built.append(weakref.ref(client.app))
        assert client.get("/api/kbs").status_code == 200
        del client, _config, _db
