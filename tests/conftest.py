"""Shared test fixtures for pyrite tests.

Provides composable fixtures for KB setup, database, indexing, and sample data.
Individual test files build on these instead of duplicating setup code.
"""

import tempfile
from pathlib import Path

import pytest

_COLOR_ENV = pytest.StashKey[pytest.MonkeyPatch]()

# Ambient variables that switch Rich/Typer to terminal rendering.
_AMBIENT_RENDER_VARS = ("FORCE_COLOR", "TTY_COMPATIBLE", "PY_COLORS", "NO_COLOR")
# Pinned for the whole run, before CLI modules (and their Rich consoles) import.
_PINNED_RENDER_ENV = {
    "_TYPER_FORCE_DISABLE_TERMINAL": "1",  # no ANSI/boxes from Typer, even in CI
    "COLUMNS": "80",  # Rich Console width
    "TERMINAL_WIDTH": "80",  # Typer help width
    "TERM": "xterm-256color",
}


def pytest_configure(config):
    # Rich caches colour capability when module-level consoles are created.
    # Normalize before collection imports CLI modules, not only before tests.
    patch = pytest.MonkeyPatch()
    for name in _AMBIENT_RENDER_VARS:
        patch.delenv(name, raising=False)
    # Typer turns terminal mode on for GITHUB_ACTIONS, FORCE_COLOR or PY_COLORS
    # (typer/rich_utils.py FORCE_TERMINAL) and only this variable overrides it.
    # CI sets GITHUB_ACTIONS, so help and usage errors rendered as ANSI boxes
    # there and as plain text on a laptop (#884). Pin one rendering for everyone.
    for name, value in _PINNED_RENDER_ENV.items():
        patch.setenv(name, value)
    config.stash[_COLOR_ENV] = patch


def pytest_unconfigure(config):
    patch = config.stash.get(_COLOR_ENV, None)
    if patch is not None:
        patch.undo()


from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models import EventEntry
from pyrite.models.core_types import PersonEntry
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

try:
    import fastapi  # noqa: F401

    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False


@pytest.fixture(autouse=True)
def _plain_cli_environment(monkeypatch):
    """Keep raw CLI assertions independent of the caller's terminal settings.

    NO_COLOR alone does not prevent Rich from emitting style escapes when
    FORCE_COLOR wins. Tests that exercise styling can still opt in by setting
    FORCE_COLOR inside their own fixture/test, or using force_terminal=True.
    """
    for name in _AMBIENT_RENDER_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in _PINNED_RENDER_ENV.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def terminal_colour(monkeypatch):
    """Opt in to Typer's terminal rendering (ANSI, boxes) for one test.

    Typer decides once, at import, so the environment cannot reach it; this
    flips the decision it already made. Rich consoles you build yourself take
    `force_terminal=True`; a subprocess takes `FORCE_COLOR=1` in its own env.
    """
    from typer import rich_utils

    monkeypatch.setattr(rich_utils, "FORCE_TERMINAL", True)
    monkeypatch.delenv("_TYPER_FORCE_DISABLE_TERMINAL", raising=False)


@pytest.fixture(autouse=True)
def _isolate_global_config(tmp_path_factory, monkeypatch):
    """Redirect CONFIG_FILE and CONFIG_DIR to a temp directory.

    Prevents tests that call save_config() from clobbering ~/.pyrite/config.yaml.
    """
    import pyrite.config as config_module

    safe_dir = tmp_path_factory.mktemp("pyrite_config")
    monkeypatch.setattr(config_module, "CONFIG_DIR", safe_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", safe_dir / "config.yaml")


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Give each test its own rate-limit budget.

    `pyrite.server.api.limiter` is a module-level `Limiter` with in-process
    memory storage, so its counters are shared by every app every test in
    the process builds -- a fresh `create_app()` does not reset them. Routes
    limited at "100/minute" therefore start returning 429 once the tests in
    one worker have, between them, sent a hundred requests inside the same
    wall-clock minute.

    That makes any suite exercising those routes **load-sensitive**: how many
    requests land in a given minute depends on how fast everything else on
    the machine ran, so the tests pass on an idle box and fail in a batch
    under `-n auto`. `tests/test_private_kb_read_scoping.py` failed exactly
    that way -- 110 of 239 cases while another suite ran, all 239 green when
    the machine was idle. Resetting per test makes the count each test's own.
    """
    if not _HAS_FASTAPI:
        yield
        return
    from pyrite.server.api import limiter

    limiter.reset()
    yield


# Test files that exercise clone/fork/subscribe paths. A stub that stops being
# the method actually called -- the `@patch.object(GitService, "clone")` that
# went fail-open once `clone_with_code` became the real implementation -- made
# one of them reach out to github.com instead of erroring. This guard turns
# that silent network call into a failure.
_NO_NETWORK_CLONE_FILES = frozenset(
    {
        "test_repo_service.py",
        "test_repo_endpoints.py",
        "test_repo_error_disclosure.py",
        "test_git_service.py",
        "test_export_to_repo.py",
        "test_repository_rename.py",
    }
)


def _is_local_clone_url(url: str) -> bool:
    """A clone source that touches no network: a filesystem path, a file:// URL
    or a Windows drive path."""
    return url.startswith(("/", ".", "~", "file://")) or (
        len(url) > 1 and url[1] == ":" and url[0].isalpha()
    )


@pytest.fixture(autouse=True)
def _no_network_git_clone(request, monkeypatch):
    """Fail any test in the repo/git files that shells out to `git clone`
    against a non-local URL, rather than letting it hit the network."""
    if Path(request.node.fspath).name not in _NO_NETWORK_CLONE_FILES:
        return

    import subprocess

    real_run = subprocess.run

    def _guarded_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and len(cmd) >= 2:
            argv = [str(c) for c in cmd]
            if argv[0].endswith("git") and "clone" in argv[1:3]:
                targets = [a for a in argv[2:] if not a.startswith("-") and a != "--"]
                remote = targets[0] if targets else ""
                if remote and not _is_local_clone_url(remote):
                    raise AssertionError(
                        "test attempted a real `git clone` of a non-local URL "
                        f"({remote!r}) -- a clone stub stopped being the method "
                        "actually called (fail-open patch). Patch the method the "
                        "code under test invokes, or stub subprocess.run."
                    )
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _guarded_run)


@pytest.fixture
def tmp_kb_dir():
    """Temporary directory with KB subdirectories."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        events_path = tmpdir / "events"
        events_path.mkdir()

        research_path = tmpdir / "research"
        research_path.mkdir()
        (research_path / "actors").mkdir()

        yield {
            "tmpdir": tmpdir,
            "events_path": events_path,
            "research_path": research_path,
            "db_path": tmpdir / "index.db",
        }


@pytest.fixture
def kb_configs(tmp_kb_dir):
    """KBConfig objects for events and research KBs."""
    events_kb = KBConfig(
        name="test-events",
        path=tmp_kb_dir["events_path"],
        kb_type=KBType.EVENTS,
        description="Test events KB",
    )
    research_kb = KBConfig(
        name="test-research",
        path=tmp_kb_dir["research_path"],
        kb_type=KBType.RESEARCH,
        description="Test research KB",
    )
    return {"events_kb": events_kb, "research_kb": research_kb}


@pytest.fixture
def pyrite_config(tmp_kb_dir, kb_configs):
    """PyriteConfig with events and research KBs."""
    return PyriteConfig(
        knowledge_bases=[kb_configs["events_kb"], kb_configs["research_kb"]],
        settings=Settings(index_path=tmp_kb_dir["db_path"]),
    )


@pytest.fixture
def pyrite_db(pyrite_config):
    """PyriteDB instance. Closed automatically after test."""
    db = PyriteDB(pyrite_config.settings.index_path)
    yield db
    db.close()


@pytest.fixture
def index_mgr(pyrite_db, pyrite_config):
    """IndexManager instance."""
    return IndexManager(pyrite_db, pyrite_config)


@pytest.fixture
def kb_service(pyrite_config, pyrite_db):
    """KBService instance."""
    return KBService(pyrite_config, pyrite_db)


@pytest.fixture
def sample_events(kb_configs):
    """Create 3 sample event entries on disk."""
    events_repo = KBRepository(kb_configs["events_kb"])
    entries = []
    for i in range(3):
        event = EventEntry.create(
            date=f"2025-01-{10 + i:02d}",
            title=f"Test Event {i}",
            body=f"Body for event {i} about immigration policy.",
            importance=5 + i,
        )
        event.tags = ["test", "immigration"]
        event.participants = ["Stephen Miller", "Tom Homan"]
        events_repo.save(event)
        entries.append(event)
    return entries


@pytest.fixture
def sample_person(kb_configs):
    """Create a sample person entry on disk."""
    research_repo = KBRepository(kb_configs["research_kb"])
    actor = PersonEntry.create(
        name="Stephen Miller", role="Immigration policy architect", importance=9
    )
    actor.body = "Stephen Miller biography."
    actor.tags = ["trump-admin", "immigration"]
    research_repo.save(actor)
    return actor


@pytest.fixture
def indexed_test_env(pyrite_config, pyrite_db, index_mgr, kb_configs, sample_events, sample_person):
    """Fully indexed test environment with sample data.

    Returns dict with config, db, index_mgr, events_kb, research_kb.
    """
    index_mgr.index_all()
    return {
        "config": pyrite_config,
        "db": pyrite_db,
        "index_mgr": index_mgr,
        "events_kb": kb_configs["events_kb"],
        "research_kb": kb_configs["research_kb"],
    }


@pytest.fixture
def rest_api_env(tmp_kb_dir, kb_configs, sample_events, sample_person):
    """The events + research KBs, with their sample entries, served over REST.

    A `tests._world` world (#1077) over the same KB directories the
    `sample_events` / `sample_person` fixtures wrote into, so a test that takes
    those too sees the same entries: the config is read from the world's
    `config.yaml`, the files are indexed through the indexer, and the app runs
    create_app's own `get_db` and its lifespan. `db` is the app's own
    database. The world is closed (workers joined, app shut down, every DB
    closed) before `tmp_kb_dir` is removed.
    """
    from tests._world import build_world

    world = build_world(
        tmp_kb_dir["tmpdir"],
        kbs=[kb_configs["events_kb"], kb_configs["research_kb"]],
        settings={"index_path": tmp_kb_dir["db_path"]},
    )
    try:
        yield {
            "client": world.client,
            "config": world.config,
            "db": world.db,
            "events_kb": world.config.get_kb("test-events"),
            "research_kb": world.config.get_kb("test-research"),
        }
    finally:
        world.close()


@pytest.fixture
def make_client(tmp_path):
    """Factory fixture: a `TestClient` + `PyriteConfig` + `PyriteDB` for a
    REST API test, built as a `tests._world` world (#1077).

    The app is `create_app(config)` over a config the real loader read from
    the world's `config.yaml`, with create_app's own `get_db` (a per-request
    session) and the lifespan running. `db` is the app's own
    `app.state.pyrite_db` -- one database, not a second connection beside
    it. Every world this fixture builds is closed at teardown (index workers
    joined, the app shut down, every DB closed), before pytest removes
    `tmp_path`: the teardown the hand-rolled `_make_client` helpers were
    missing (tests-leak-open-pyritedb-connections-into-temporarydirectory-
    teardown, GitHub #55). Call it more than once per test for more apps.

    Usage:
        client, config, db = make_client(api_key="secret", kb_name="test-kb")

    `dependency_overrides` is for seams that are not the database (an
    external service a test stubs); a `get_db` key is refused.
    """
    if not _HAS_FASTAPI:
        pytest.skip("fastapi not installed")

    from tests._world import build_world

    worlds: list = []

    def _make(
        api_key: str = "",
        api_keys: list[dict] | None = None,
        kb_name: str = "test-kb",
        cors_origins: list[str] | None = None,
        auth=None,
        extra_settings: dict | None = None,
        register_user: tuple[str, str] | None = None,
        dependency_overrides: dict | None = None,
    ):
        if dependency_overrides:
            from pyrite.server.api import get_db

            if get_db in dependency_overrides:
                raise ValueError(
                    "make_client(dependency_overrides=...) refuses get_db: the world "
                    "runs the production per-request session. Seed the world's db "
                    "(the third value returned), or build_world over your config."
                )
        work_dir = tmp_path / f"client-{len(worlds) + 1}"
        settings: dict = {"index_path": work_dir / "index.db", "api_key": api_key}
        if api_keys is not None:
            settings["api_keys"] = api_keys
        if cors_origins is not None:
            settings["cors_origins"] = cors_origins
        if auth is not None:
            settings["auth"] = auth
        if extra_settings:
            settings.update(extra_settings)

        world = build_world(
            work_dir,
            kbs=[KBConfig(name=kb_name, path=work_dir / "kb", kb_type="generic")],
            settings=settings,
        )
        worlds.append(world)

        if dependency_overrides:
            for dep, override in dependency_overrides.items():
                world.app.dependency_overrides[dep] = override

        if register_user is not None:
            # The operator path, not /auth/register: registration is closed
            # until an admin exists. See tests/auth_seed.py.
            from tests.auth_seed import seed_and_sign_in

            username, password = register_user
            seed_and_sign_in(world.client, username, password)

        return world.client, world.config, world.db

    yield _make

    # Every world is closed even if one fails; the first error is re-raised.
    errors: list[BaseException] = []
    for world in worlds:
        try:
            world.close()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            errors.append(exc)
    if errors:
        raise errors[0]


@pytest.fixture
def cli_world(tmp_path):
    """Factory fixture: a `tests._world` world for driving the CLI (#1077).

    `cli_world()` builds a world with one generic KB, `test-kb`; pass `kbs=`
    and `settings=` as to `build_world`. The process's config location points
    at the world's config dir for the rest of the test, so every in-process
    `pyrite` command -- `world.cli(...)` or a `CliRunner` of your own -- reads
    the world's `config.yaml` through the real `load_config`, and a command
    that writes the config (`kb add`) is read back by the next one. Nothing
    patches `load_config`; `tests/test_production_wiring_guard.py` counts the
    tests that still do. Every world is closed at teardown.
    """
    import contextlib

    from tests._world import build_world

    stack = contextlib.ExitStack()
    count = {"n": 0}

    def _make(kbs=None, settings=None):
        count["n"] += 1
        root = tmp_path / f"cli-world-{count['n']}"
        if kbs is None:
            kbs = [KBConfig(name="test-kb", path=root / "kb", kb_type="generic")]
        world = build_world(root, kbs=kbs, settings=settings, rest=False)
        stack.callback(world.close)
        stack.enter_context(world.pointed())
        return world

    with stack:
        yield _make


@pytest.fixture
def console_width(monkeypatch):
    """Set the width of the Rich consoles the read commands print through.

    A `Console` reads `COLUMNS` and the terminal once, when it is made (at import),
    so `CliRunner(env={"COLUMNS": ...})` does not reach it, and neither does
    `terminal_width=` (that is Click's help width). Call `console_width(80)`
    before invoking; the fixture restores each console afterwards.
    """
    import importlib

    consoles = [
        importlib.import_module(name).console
        for name in (
            "pyrite.cli",
            "pyrite.cli.browse_commands",
            "pyrite.cli.entry_commands",
            "pyrite.cli.search_commands",
        )
    ]

    def set_width(width):
        for console in consoles:
            # Both, so Console.size takes its first branch and never consults the
            # terminal, TERM=dumb or COLUMNS.
            monkeypatch.setattr(console, "_width", width)
            monkeypatch.setattr(console, "_height", 25)

    return set_width
