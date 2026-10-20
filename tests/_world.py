"""The test world: production wiring for medium tests (#1077).

A medium test drives a real surface (REST, MCP, the CLI) against a real world:
a config directory on disk, KBs whose entries are files, an index the indexer
built from them, and the app exactly as `pyrite serve` builds it. This module
builds that world. `make_client`, `rest_api_env`, `_make_mcp_server` and
`cli_world` are thin wrappers over it; a new test should use one of them (the
"Which fixture?" table in `.claude/skills/pyrite-dev/testing.md`).

What "production wiring" means here, property by property (each pinned in
`tests/test_world.py`):

- **The config is read from disk.** `build_world` writes `config.yaml` with
  `save_config` and reads it back with `load_config`, so the app, the CLI and
  MCP all see what the loader parsed. A setting or KB field `config.yaml`
  cannot express is refused at build time rather than silently replaced by
  its default.
- **No dependency overrides.** The app is `create_app(config)` and nothing
  else: `get_db` is create_app's own `_app_get_db`, a per-request
  `request_handle()` session closed when the request ends (#131), on the
  shared `app.state.pyrite_db`, which ran `merge_registered_kbs`.
- **The lifespan runs.** The `TestClient` is entered as a context manager, so
  the startup hooks (embed-queue drain, websocket loop bind, credential sweep)
  and the shutdown hooks run.
- **The world owns its teardown**, including `app.state.pyrite_db`, which
  nothing in `pyrite/` closes: index workers are joined, the app is shut down,
  then every database is closed. `close()` is idempotent.

Why there is no `release_idle_connections()` here, unlike
`tests/characterization/world.py` before #1077. The pool exhaustion it worked
around had two causes, and both were in the harness. A bare `PyriteDB`
override sent every handler to `PyriteDB.session`'s per-thread *fallback*
session, which holds a pooled connection for as long as the `PyriteDB` lives
(it is tracked in `_fallback_sessions`, so it outlives its thread). And a
`TestClient` used outside `with` starts a fresh portal, so fresh anyio worker
threads, for every request. Together: one leaked connection per request, and
the pool (40 + 20 overflow) was gone after 60 requests -- measured, not
estimated. Either production wiring alone stops it: `request_handle()` closes
its session, and an entered client reuses its worker threads.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from pyrite.config import KBConfig, PyriteConfig, Settings

#: `Settings` fields `PyriteConfig.to_dict()` / `from_dict()` do not carry,
#: so a world cannot put them in `config.yaml`. Compared field by field
#: after the round trip; listed here only to make the refusal say why.
_ENV_ONLY_SETTINGS = {
    "workspace_path": "PYRITE_DATA_DIR",
    "strict_plugins": "PYRITE_STRICT_PLUGINS",
    "prewarm_embeddings": "PYRITE_PREWARM_EMBEDDINGS",
}


class World:
    """A config dir, KBs on disk, an index, and the surfaces over them.

    Build one with :func:`build_world`. ``client`` and ``app`` are ``None``
    for a world built with ``rest=False``.
    """

    def __init__(self, root: Path, config_dir: Path, config: PyriteConfig, rest: bool):
        self.root = root
        self.config_dir = config_dir
        self.config = config
        self.app: Any = None
        self.client: Any = None
        self._own_db = None
        self._mcp_servers: list[Any] = []
        self._extra_dbs: list[Any] = []
        self._closed = False
        if rest:
            self._start_app()
        else:
            from pyrite.storage.database import PyriteDB

            self._own_db = PyriteDB(config.settings.index_path)
            self._own_db.merge_registered_kbs(config)

    # -- construction ------------------------------------------------------

    def _start_app(self) -> None:
        from starlette.testclient import TestClient

        from pyrite.server.api import create_app

        self.app = _create_app_without_static(self.config, self.root)
        client = TestClient(self.app)
        # Entering runs the startup hooks; `close()` exits it, running shutdown.
        client.__enter__()
        self.client = client

    @property
    def db(self):
        """The world's one `PyriteDB`: the app's own when there is an app."""
        if self.app is not None:
            return self.app.state.pyrite_db
        return self._own_db

    def _index_mgr(self):
        if self.app is not None:
            from pyrite.server.api import get_index_mgr

            # create_app's own accessor, the one the index endpoints use.
            return self.app.dependency_overrides[get_index_mgr]()
        from pyrite.storage.index import IndexManager

        return IndexManager(self.db, self.config)

    def index(self, kb_name: str | None = None) -> None:
        """Index the KBs' files through the indexer (one KB, or all)."""
        mgr = self._index_mgr()
        if kb_name is None:
            mgr.index_all()
        else:
            mgr.index_kb(kb_name)

    # -- seeding -----------------------------------------------------------

    def kb_path(self, kb_name: str) -> Path:
        kb = self.config.get_kb(kb_name)
        if kb is None:
            raise KeyError(f"no KB {kb_name!r} in this world")
        return kb.path

    def add_entry(self, kb_name: str, text: str, *, filename: str | None = None) -> Path:
        """Write one entry file into a KB and index it through the indexer.

        ``text`` is the whole file, frontmatter and body. The file is the
        source of truth, as in production: a test that seeds index rows with
        no file cannot see file/index divergence (#1077, R9).
        """
        import yaml

        if filename is None:
            front = text.split("---", 2)[1] if text.startswith("---") else ""
            entry_id = (yaml.safe_load(front) or {}).get("id")
            if not entry_id:
                raise ValueError("add_entry needs an `id:` in the frontmatter or a filename")
            filename = f"{entry_id}.md"
        path = self.kb_path(kb_name) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self.index(kb_name)
        return path

    # -- the other surfaces ------------------------------------------------

    def pointed(self):
        """Point this process's config location at the world's config dir;
        see :func:`pointed`."""
        return pointed(self.config_dir)

    def cli(self, *args: str, input: str | None = None, app: Any = None):
        """Run a `pyrite` command in-process against this world's config dir."""
        from typer.testing import CliRunner

        if app is None:
            from pyrite.cli import app
        with self.pointed():
            return CliRunner().invoke(app, list(args), input=input)

    def mcp(self, tier: str = "admin"):
        """A `PyriteMCPServer` over this world's config; closed with the world."""
        from pyrite.server.mcp_server import PyriteMCPServer

        server = PyriteMCPServer(config=self.config, tier=tier)
        self._mcp_servers.append(server)
        return server

    def track_db(self, db) -> None:
        """Close ``db`` with the world (a database a test opened itself)."""
        self._extra_dbs.append(db)

    # -- teardown ----------------------------------------------------------

    def close(self) -> None:
        """Join workers, shut the app down, close every database. Idempotent.

        Each step is guarded so one failure cannot leak everything after it;
        the first error is re-raised at the end.
        """
        if self._closed:
            return
        self._closed = True
        errors: list[BaseException] = []

        def step(fn, *a):
            try:
                fn(*a)
            except BaseException as exc:  # noqa: BLE001 - re-raised below
                errors.append(exc)

        workers = [getattr(s, "_index_worker", None) for s in self._mcp_servers]
        if self.app is not None:
            workers.append(getattr(self.app.state, "pyrite_index_worker", None))
        for worker in workers:
            if worker is not None:
                step(worker.wait_for_idle, 10)
        if self.client is not None:
            step(self.client.__exit__, None, None, None)
        for server in self._mcp_servers:
            step(server.close)
        dbs = list(self._extra_dbs)
        if self.app is not None:
            dbs.extend(getattr(self.app.state, "pyrite_diff_db_cache", {}).values())
            dbs.append(self.app.state.pyrite_db)
        dbs.append(self._own_db)
        for db in dbs:
            if db is not None:
                step(db.close)
        if errors:
            raise errors[0]


@contextlib.contextmanager
def pointed(config_dir: Path) -> Iterator[None]:
    """Point this process's config location at ``config_dir``.

    ``PYRITE_CONFIG_DIR`` is read once, when `pyrite.config` is imported,
    into ``CONFIG_DIR``/``CONFIG_FILE`` -- and the repo-wide
    ``_isolate_global_config`` fixture pins those per test -- so setting
    the variable after import does not move an in-process
    ``load_config()``. This sets both: the variable (for subprocesses and
    ``resolve_config_source``) and the two module globals, to what an
    import under that variable would have resolved. The loader itself is
    never replaced. Restored on exit.
    """
    import pyrite.config as config_module

    saved = (
        config_module.CONFIG_DIR,
        config_module.CONFIG_FILE,
        os.environ.get("PYRITE_CONFIG_DIR"),
    )
    config_module.CONFIG_DIR = config_dir
    config_module.CONFIG_FILE = config_dir / "config.yaml"
    os.environ["PYRITE_CONFIG_DIR"] = str(config_dir)
    try:
        yield
    finally:
        config_module.CONFIG_DIR, config_module.CONFIG_FILE = saved[0], saved[1]
        if saved[2] is None:
            os.environ.pop("PYRITE_CONFIG_DIR", None)
        else:
            os.environ["PYRITE_CONFIG_DIR"] = saved[2]


def _create_app_without_static(config: PyriteConfig, root: Path):
    """`create_app(config)` with static mounting pointed at an empty dir.

    `create_app` mounts the SPA when `web/dist/index.html` exists, so a world
    would change shape after `npm run build` (#504: the route-completeness
    tests went red on a tree with a built frontend). `PYRITE_STATIC_DIR` is the
    production lever for where static assets live; it is read once, during
    the call, and restored at once.
    """
    from pyrite.server.api import create_app

    empty = root / "empty-static-dir"
    empty.mkdir(parents=True, exist_ok=True)
    prior = os.environ.get("PYRITE_STATIC_DIR")
    os.environ["PYRITE_STATIC_DIR"] = str(empty)
    try:
        return create_app(config=config)
    finally:
        if prior is None:
            os.environ.pop("PYRITE_STATIC_DIR", None)
        else:
            os.environ["PYRITE_STATIC_DIR"] = prior


def _requested(settings: Settings) -> dict[str, Any]:
    """The fields of ``settings`` that differ from a default `Settings()`."""
    default = Settings()
    return {
        f.name: getattr(settings, f.name)
        for f in dataclasses.fields(Settings)
        if getattr(settings, f.name) != getattr(default, f.name)
    }


def _requested_kb_fields(kb: KBConfig) -> dict[str, Any]:
    """The init fields of ``kb`` that differ from a bare `KBConfig(name, path)`."""
    default = KBConfig(name=kb.name, path=kb.path)
    return {
        f.name: getattr(kb, f.name)
        for f in dataclasses.fields(KBConfig)
        if f.init and getattr(kb, f.name) != getattr(default, f.name)
    }


def _check_round_trip(intended: PyriteConfig, loaded: PyriteConfig) -> None:
    """Every setting and KB field the caller asked for reached the loaded config.

    Only the requested ones: a default the loader derives differently
    (``workspace_path`` under the config dir, a KB's ``kb.yaml``) is
    production behaviour, not a loss.
    """
    lost = [k for k, v in _requested(intended.settings).items() if getattr(loaded.settings, k) != v]
    loaded_kbs = {kb.name: kb for kb in loaded.knowledge_bases}
    for kb in intended.knowledge_bases:
        got = loaded_kbs.get(kb.name)
        if got is None:
            lost.append(kb.name)
            continue
        lost += [
            f"{kb.name}.{k}" for k, v in _requested_kb_fields(kb).items() if getattr(got, k) != v
        ]
    lost.sort()
    if lost:
        detail = ", ".join(
            f"{k} (set by {_ENV_ONLY_SETTINGS[k]})" if k in _ENV_ONLY_SETTINGS else k for k in lost
        )
        raise ValueError(
            f"config.yaml cannot express {detail}: the world reads its config back "
            "from disk, as production does, and these did not survive. "
            "Set them through the environment, or teach PyriteConfig.to_dict/"
            "from_dict to carry them."
        )


def build_world(
    root: Path,
    *,
    kbs: Sequence[KBConfig] = (),
    settings: dict[str, Any] | None = None,
    config: PyriteConfig | None = None,
    rest: bool = True,
) -> World:
    """Build a world under ``root`` and return it; the caller calls ``close()``.

    ``kbs`` are written into the config as given; a KB directory that does not
    exist is created. Files already in a KB directory are indexed. ``settings``
    are `Settings` keyword arguments; ``index_path`` defaults to
    ``root/index.db``. Or pass a whole in-memory ``config`` instead of the
    two; either way it is written to ``config.yaml`` and the world runs on
    what `load_config` reads back. ``rest=False`` builds no app (for MCP and
    CLI worlds).
    """
    from pyrite.config import load_config, save_config

    root = Path(root)
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    if config is None:
        settings_kwargs = {"index_path": root / "index.db", **(settings or {})}
        config = PyriteConfig(knowledge_bases=list(kbs), settings=Settings(**settings_kwargs))
    elif kbs or settings:
        raise TypeError("pass config, or kbs/settings, not both")
    for kb in config.knowledge_bases:
        Path(kb.path).mkdir(parents=True, exist_ok=True)

    with pointed(config_dir):
        save_config(config)
        loaded = load_config()
    _check_round_trip(config, loaded)

    world = World(root, config_dir, loaded, rest=rest)
    try:
        world.index()
    except BaseException:
        world.close()
        raise
    return world


@contextlib.contextmanager
def mcp_server(kb_defs, *, tier="admin", extra_setup=None):
    """Context manager that creates a PyriteMCPServer with temp dirs.

    Parameters
    ----------
    kb_defs : list[dict]
        Each dict has keys: name, kb_type, and optionally description, subdirs
        (list of sub-directory names to create under the KB path), read_only.
    tier : str
        MCP tier — "read", "write", or "admin".
    extra_setup : callable or None
        Called with (config, kb_configs_by_name) before server creation, so
        callers can write kb.yaml files or create custom entries.

    Yields
    ------
    dict with "server", "config", and each KB name mapped to its KBConfig.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"

        kb_configs = []
        kb_map = {}
        for defn in kb_defs:
            kb_path = tmpdir / defn["name"]
            kb_path.mkdir()
            for sub in defn.get("subdirs", []):
                (kb_path / sub).mkdir(parents=True, exist_ok=True)
            kbc = KBConfig(
                name=defn["name"],
                path=kb_path,
                kb_type=defn["kb_type"],
                description=defn.get("description", ""),
                read_only=defn.get("read_only", False),
            )
            kb_configs.append(kbc)
            kb_map[defn["name"]] = kbc

        config = PyriteConfig(
            knowledge_bases=kb_configs,
            settings=Settings(index_path=db_path),
        )

        if extra_setup:
            extra_setup(config, kb_map)

        # A world (#1077): the config goes through config.yaml and the real
        # loader, the KB files are indexed through the indexer, and the world
        # closes the server and every database it opened.
        world = build_world(tmpdir, config=config, rest=False)
        try:
            server = world.mcp(tier=tier)
            result = {"server": server, "config": world.config}
            result.update({kb.name: kb for kb in world.config.knowledge_bases})
            yield result
        finally:
            world.close()
