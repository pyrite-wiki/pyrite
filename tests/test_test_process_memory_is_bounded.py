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


def _live_apps():
    from fastapi import FastAPI

    return [o for o in gc.get_objects() if isinstance(o, FastAPI)]


def test_fastapi_caches_are_found(clear_fastapi_caches):
    """If a FastAPI release renames or moves the caches, the hook would clear
    nothing and the leak would return quietly; this fails instead."""
    import fastapi.dependencies.models  # noqa: F401

    assert clear_fastapi_caches() >= 1, (
        "the root conftest found no lru_cache in the loaded fastapi modules; "
        "FastAPI changed where it caches callables (see #1098)"
    )


def test_apps_a_test_builds_are_freed_after_its_teardown(request, make_client):
    from pyrite.server import api

    class _AfterRootTeardown:
        @pytest.hookimpl(wrapper=True)
        def pytest_runtest_teardown(self, item, nextitem):
            result = yield
            if item is request.node:
                gc.collect()
                strays = [a for a in _live_apps() if a is not api.app]
                assert len(strays) <= 1, (
                    f"{len(strays)} FastAPI apps are still alive after the test "
                    f"built {APPS_BUILT}: something caches their closures "
                    "(#1098; the root conftest should clear it)"
                )
            return result

    plugin = _AfterRootTeardown()
    request.config.pluginmanager.register(plugin)
    request.addfinalizer(lambda: request.config.pluginmanager.unregister(plugin))

    for _ in range(APPS_BUILT):
        client, _config, _db = make_client()
        assert client.get("/api/kbs").status_code == 200
        del client, _config, _db
