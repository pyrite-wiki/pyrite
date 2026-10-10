"""KB names must not collide with fixed API routes (#565)."""

from unittest.mock import patch
import pytest
from starlette.routing import Route
from typer.testing import CliRunner
from pyrite.cli import app
from pyrite.config import PyriteConfig, Settings
from pyrite.exceptions import ConfigError
from pyrite.services.kb_registry_service import KBRegistryService
from pyrite.services.ephemeral_service import EphemeralKBService, InvalidEphemeralKBNameError
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager


@pytest.fixture
def env(tmp_path):
    config = PyriteConfig(
        knowledge_bases=[],
        settings=Settings(index_path=tmp_path / "i.db", workspace_path=tmp_path / "ws"),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        yield config, db, tmp_path
    finally:
        db.close()


@pytest.mark.parametrize("name", ["ephemeral", "gc"])
def test_registry_refuses_reserved_name_before_creating_directory(env, name):
    config, db, root = env
    path = root / name
    svc = KBRegistryService(config, db, IndexManager(db, config))
    with pytest.raises(ConfigError, match="reserved"):
        svc.add_kb(name, str(path))
    assert not path.exists()
    assert not db.execute_sql("SELECT name FROM kb WHERE name = :name", {"name": name})


@pytest.mark.parametrize("name", ["ephemeral", "gc"])
def test_ephemeral_refuses_reserved_name_before_creating_directory(env, name):
    config, db, root = env
    with pytest.raises(InvalidEphemeralKBNameError, match="reserved"):
        EphemeralKBService(config, db).create_ephemeral_kb(name)
    assert not (root / "ws" / "ephemeral" / name).exists()
    assert not db.execute_sql("SELECT name FROM kb WHERE name = :name", {"name": name})


@pytest.mark.parametrize("name", ["ephemeral", "gc"])
def test_init_refuses_reserved_name_before_writing(env, name):
    config, db, root = env
    path = root / name
    with (
        patch("pyrite.config.load_config", return_value=config),
        patch("pyrite.config.save_config"),
    ):
        result = CliRunner().invoke(app, ["init", "-t", "empty", "--path", str(path)])
    assert result.exit_code != 0
    assert "reserved" in result.output
    assert not path.exists()


def flattened_routes(routes):
    for route in routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if contexts is not None:
            yield from contexts()
        else:
            yield route


def shadowed_fixed_routes(routes):
    routes = list(flattened_routes(routes))
    problems = []
    for i, route in enumerate(routes):
        path = getattr(route, "path", "")
        if "{" in path or not getattr(route, "methods", None):
            continue
        for earlier in routes[:i]:
            if "{" not in getattr(earlier, "path", ""):
                continue
            if earlier.methods & route.methods and earlier.path_regex.fullmatch(path):
                problems.append((earlier.path, path))
    return problems


@pytest.mark.control(
    reason="current route order already puts fixed routes before matching parameter routes"
)
def test_fixed_routes_are_not_shadowed_by_earlier_parameters():
    from pyrite.server.api import create_app

    assert shadowed_fixed_routes(create_app().routes) == []


@pytest.mark.control(
    reason="test-infrastructure control proving the guard detects a future route-order regression"
)
def test_route_guard_detects_matching_methods_and_ignores_other_methods():
    async def endpoint(request):
        pass

    fixed = Route("/api/kbs/ephemeral", endpoint, methods=["GET"])
    dynamic = Route("/api/kbs/{name}", endpoint, methods=["GET"])
    other = Route("/api/kbs/{name}", endpoint, methods=["POST"])
    assert shadowed_fixed_routes([dynamic, fixed]) == [(dynamic.path, fixed.path)]
    assert shadowed_fixed_routes([fixed, dynamic]) == []
    assert shadowed_fixed_routes([other, fixed]) == []


@pytest.mark.control(
    reason="ordinary KB names remain available and route literals are case-sensitive"
)
@pytest.mark.parametrize("name", ["project", "Ephemeral", "gc-notes"])
def test_registry_keeps_non_colliding_names(env, name):
    config, db, root = env
    result = KBRegistryService(config, db, IndexManager(db, config)).add_kb(name, str(root / name))
    assert result["name"] == name


@pytest.mark.control(reason="pins the reserved inventory to the API's actual fixed first segments")
def test_reserved_names_match_fixed_kb_route_segments():
    from pyrite.server.api import create_app
    from pyrite.services import kb_names

    names = set()
    for route in flattened_routes(create_app().routes):
        parts = getattr(route, "path", "").split("/")
        if len(parts) > 3 and parts[1:3] == ["api", "kbs"] and "{" not in parts[3]:
            names.add(parts[3])
    assert names == getattr(kb_names, "RESERVED_KB_NAMES", frozenset({"ephemeral", "gc"}))
