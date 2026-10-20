"""The test world runs the production wiring (#1077).

Each test names one property of `tests/_world.py`: what a medium test may rely
on when it asks for a world, and what the thin wrappers over it
(`make_client`, `rest_api_env`, `_make_mcp_server`, `cli_world`) inherit.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from pyrite.config import KBConfig, load_config
from pyrite.server.api import get_db
from tests._world import build_world

KB = "world-kb"


def _note(entry_id: str, title: str, body: str = "body") -> str:
    return f"---\nid: {entry_id}\ntype: note\ntitle: {title}\n---\n\n{body}\n"


@pytest.fixture
def world(tmp_path):
    w = build_world(tmp_path, kbs=[KBConfig(name=KB, path=tmp_path / "kbs" / KB)])
    yield w
    w.close()


# -- the config dir is real --------------------------------------------------


def test_the_world_writes_a_config_dir_the_real_loader_reads(world):
    config_file = world.config_dir / "config.yaml"
    assert config_file.is_file()
    with world.pointed():
        loaded = load_config()
    assert [kb.name for kb in loaded.knowledge_bases] == [KB]
    assert loaded.settings.index_path == world.config.settings.index_path


def test_the_app_is_given_the_config_the_loader_parsed_from_disk(world):
    # Not the object the caller built: a setting that does not survive
    # config.yaml cannot reach the app either.
    assert world.app.state.pyrite_config is world.config
    assert world.config._as_loaded is not None, "built in memory, not by load_config()"


def test_a_setting_config_yaml_cannot_express_is_refused(tmp_path):
    with pytest.raises(ValueError, match="branding_dir"):
        build_world(
            tmp_path,
            kbs=[KBConfig(name=KB, path=tmp_path / KB)],
            settings={"branding_dir": tmp_path / "brand"},
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("types", {"note": {"description": "a note"}}),
        ("policies", {"allow_other_types": False}),
    ],
)
def test_a_kb_field_config_yaml_cannot_express_is_refused(tmp_path, field, value):
    # `to_dict()` writes no `types`/`policies`; they would load back as None.
    kb = KBConfig(name=KB, path=tmp_path / KB, **{field: value})
    with pytest.raises(ValueError, match=rf"{KB}\.{field}"):
        build_world(tmp_path, kbs=[kb])


def test_a_kb_field_config_yaml_carries_is_accepted(tmp_path):
    kb = KBConfig(name=KB, path=tmp_path / KB, shortname="wk", default_role="read")
    w = build_world(tmp_path, kbs=[kb], rest=False)
    try:
        loaded = w.config.get_kb(KB)
        assert (loaded.shortname, loaded.default_role) == ("wk", "read")
    finally:
        w.close()


# -- KBs are on disk and indexed through the indexer -------------------------


def test_add_entry_writes_the_file_and_indexes_it(world):
    path = world.add_entry(KB, _note("hello", "Hello"))
    assert path.is_file() and path.parent == world.kb_path(KB)
    r = world.client.get(f"/api/entries/hello?kb={KB}")
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Hello"


def test_files_present_before_the_build_are_indexed(tmp_path):
    kb_dir = tmp_path / "pre"
    kb_dir.mkdir()
    (kb_dir / "early.md").write_text(_note("early", "Early"))
    w = build_world(tmp_path / "w", kbs=[KBConfig(name="pre", path=kb_dir)])
    try:
        assert w.client.get("/api/entries/early?kb=pre").status_code == 200
    finally:
        w.close()


# -- REST runs the production dependency and the lifespan --------------------


def test_rest_resolves_get_db_through_create_apps_own_dependency(world):
    dep = world.app.dependency_overrides[get_db]
    assert dep.__qualname__ == "create_app.<locals>._app_get_db"


def test_a_request_returns_its_session_and_parks_none_on_the_thread(world):
    """The #131 contract, seen from the test side: a request runs on its own
    session, closed when it ends. A bare `PyriteDB` override parks one
    per-thread fallback session per worker thread instead, holding a pooled
    connection for good -- the pool exhaustion `characterization/world.py`
    worked around with `release_idle_connections()`."""
    world.add_entry(KB, _note("probe", "Probe"))
    db = world.db
    fallbacks = len(db._fallback_sessions)
    checked_out = db.engine.pool.checkedout()
    for _ in range(5):
        assert world.client.get(f"/api/entries/probe?kb={KB}").status_code == 200
    assert len(db._fallback_sessions) == fallbacks
    assert db.engine.pool.checkedout() == checked_out


def test_the_lifespan_ran(world):
    # `_bind_websocket_loop` is a startup handler: it runs only when
    # TestClient is entered as a context manager.
    assert world.app.state.pyrite_ws_loop is not None


def test_the_world_db_is_the_apps_db(world):
    assert world.db is world.app.state.pyrite_db


# -- the world owns its teardown ---------------------------------------------


def test_close_shuts_the_app_down_and_closes_every_connection(tmp_path):
    from pyrite.server import websocket

    w = build_world(tmp_path, kbs=[KBConfig(name=KB, path=tmp_path / KB)])
    w.add_entry(KB, _note("x", "X"))
    assert w.client.get("/api/kbs").status_code == 200
    index_dir = w.config.settings.index_path.parent
    w.close()

    assert websocket._loop is None, "shutdown did not run (loop still bound)"
    live = sorted(p.name for p in index_dir.iterdir() if p.name.endswith(("-wal", "-shm")))
    assert not live, f"a connection survived close(): {live}"


def test_close_without_rest_closes_its_own_and_mcps_connections(tmp_path):
    # Asserted on the -wal/-shm files, not a ResourceWarning: an unclosed
    # pooled SQLite connection emits none while the test runs (tried, #1077).
    w = build_world(tmp_path, kbs=[KBConfig(name=KB, path=tmp_path / KB)], rest=False)
    w.add_entry(KB, _note("x", "X"))
    w.mcp(tier="read")._dispatch_tool("kb_get", {"entry_id": "x", "kb_name": KB})
    index_dir = w.config.settings.index_path.parent
    w.close()
    live = sorted(p.name for p in index_dir.iterdir() if p.name.endswith(("-wal", "-shm")))
    assert not live, f"a connection survived close(): {live}"


def test_close_is_idempotent(tmp_path):
    w = build_world(tmp_path, kbs=[KBConfig(name=KB, path=tmp_path / KB)])
    w.close()
    w.close()


def test_pointed_restores_what_it_found(world):
    import os

    import pyrite.config as config_module

    before = (
        config_module.CONFIG_DIR,
        config_module.CONFIG_FILE,
        os.environ.get("PYRITE_CONFIG_DIR"),
    )
    with world.pointed():
        assert config_module.CONFIG_DIR == world.config_dir
    after = (
        config_module.CONFIG_DIR,
        config_module.CONFIG_FILE,
        os.environ.get("PYRITE_CONFIG_DIR"),
    )
    assert after == before


# -- the other surfaces ------------------------------------------------------


def test_the_cli_reads_the_world_through_the_real_loader(world):
    world.add_entry(KB, _note("cli-note", "From the CLI"))
    result = world.cli("get", "cli-note", "-k", KB, "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["title"] == "From the CLI"


def test_mcp_dispatch_reads_the_world(world):
    world.add_entry(KB, _note("mcp-note", "From MCP"))
    server = world.mcp(tier="read")
    out = server._dispatch_tool("kb_get", {"entry_id": "mcp-note", "kb_name": KB})
    assert out["entry"]["title"] == "From MCP"


def test_a_world_without_rest_builds_no_app(tmp_path):
    w = build_world(tmp_path, kbs=[KBConfig(name=KB, path=tmp_path / KB)], rest=False)
    try:
        assert w.app is None
        w.add_entry(KB, _note("n", "N"))
        assert w.db.get_entry("n", KB)["title"] == "N"
    finally:
        w.close()


# -- cli_world: the CLI over the real config dir, with no load_config patch --


def test_cli_world_points_load_config_at_the_world(cli_world):
    import pyrite.config as config_module

    world = cli_world()
    assert config_module.load_config.__module__ == "pyrite.config"
    assert config_module.current_config_file() == world.config_dir / "config.yaml"
    assert [kb.name for kb in load_config().knowledge_bases] == ["test-kb"]


def test_a_kb_added_by_one_command_is_seen_by_the_next(cli_world, tmp_path):
    """The escape class of #448/#363 and #1061: a KB registered by `kb add`
    must reach later commands through the config the loader reads."""
    world = cli_world()
    new_kb = tmp_path / "added"
    new_kb.mkdir()
    (new_kb / "found.md").write_text(_note("found", "Found it"))

    added = world.cli("kb", "add", str(new_kb), "--name", "added")
    assert added.exit_code == 0, added.output
    listed = world.cli("kb", "list", "--format", "json")
    assert listed.exit_code == 0, listed.output
    assert "added" in listed.output


def test_cli_world_takes_its_own_kbs(cli_world, tmp_path):
    world = cli_world(kbs=[KBConfig(name="mine", path=tmp_path / "mine")])
    world.add_entry("mine", _note("m1", "Mine"))
    result = world.cli("get", "m1", "-k", "mine", "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["title"] == "Mine"


# -- make_client: overrides are for edges, never the database ---------------


def test_make_client_refuses_a_get_db_override(make_client):
    # `dict.fromkeys`, not a `{get_db: ...}` literal: the one override written
    # on purpose, to be refused, stays out of the wiring guard's count.
    with pytest.raises(ValueError, match="get_db"):
        make_client(dependency_overrides=dict.fromkeys([get_db], lambda: None))


def test_make_client_still_takes_an_edge_override(make_client):
    from pyrite.server.api import get_repo_service

    sentinel = object()
    client, _config, _db = make_client(dependency_overrides={get_repo_service: lambda: sentinel})
    assert client.app.dependency_overrides[get_repo_service]() is sentinel
