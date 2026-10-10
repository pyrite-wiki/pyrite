"""Shared test fixtures for pyrite tests.

Provides composable fixtures for KB setup, database, indexing, and sample data.
Individual test files build on these instead of duplicating setup code.
"""

import tempfile
from pathlib import Path

import pytest

_COLOR_ENV = pytest.StashKey[pytest.MonkeyPatch]()


def pytest_configure(config):
    # Rich caches colour capability when module-level consoles are created.
    # Normalize before collection imports CLI modules, not only before tests.
    patch = pytest.MonkeyPatch()
    patch.delenv("FORCE_COLOR", raising=False)
    patch.delenv("TTY_COMPATIBLE", raising=False)
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
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("TTY_COMPATIBLE", raising=False)


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
def rest_api_env(indexed_test_env):
    """Test environment for REST API tests with TestClient.

    Uses create_app() which stores state on app.state and sets up
    dependency_overrides for full per-app isolation.
    """
    from starlette.testclient import TestClient

    from pyrite.server.api import (
        create_app,
        get_config,
        get_db,
        get_index_mgr,
        get_index_worker,
    )

    config = indexed_test_env["config"]
    db = indexed_test_env["db"]
    index_mgr = indexed_test_env["index_mgr"]

    app = create_app(config)
    # Override DI to use pre-built test objects
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_index_mgr] = lambda: index_mgr

    from pyrite.services.index_worker import IndexWorker

    _index_worker = IndexWorker(db, config)
    app.dependency_overrides[get_index_worker] = lambda: _index_worker
    client = TestClient(app)

    yield {
        "client": client,
        "config": config,
        "db": db,
        "events_kb": indexed_test_env["events_kb"],
        "research_kb": indexed_test_env["research_kb"],
    }
    # Join any background sync/rebuild thread before this fixture's own
    # teardown (and pyrite_db/tmp_kb_dir's) runs -- same full-suite-only
    # flaky-test root cause as the `worker` fixture in test_index_worker.py.
    _index_worker.wait_for_idle(timeout=10)


@pytest.fixture
def make_client(tmp_path):
    """Factory fixture: build a `TestClient` + `PyriteConfig` + `PyriteDB`
    for a REST API test, replacing the hand-rolled `_make_client` helpers
    duplicated across test_api_tiers.py, test_api_wikilinks.py,
    test_api_security.py, and test_repo_endpoints.py.

    Owns every DB opened on its behalf -- its own, AND the second one
    `create_app()` opens on `application.state.pyrite_db` (see below) --
    plus the app's index worker. Workers are joined and DBs closed at
    teardown, in that order, before pytest removes `tmp_path`.
    This is what those hand-rolled helpers were missing --
    tests-leak-open-pyritedb-connections-into-temporarydirectory-teardown
    (an unclosed WAL connection) and GitHub #55 (an index-worker thread
    still writing when the directory is removed). Also used to build
    extra dbs/apps beyond the returned one; call it more than once per
    test and every db/worker it creates is tracked and cleaned up.

    Usage:
        client, config, db = make_client(api_key="secret", kb_name="test-kb")

    `tmp_path` (not `tempfile.TemporaryDirectory()`) is a second line of
    defence: pytest's default retention policy does not delete it during
    the run, so a late writer cannot fail this session. That is a
    mitigation, not the fix -- the fix is closing every connection below,
    because the retention policy is a default a project can change.
    """
    if not _HAS_FASTAPI:
        pytest.skip("fastapi not installed")

    from starlette.testclient import TestClient

    from pyrite.server.api import create_app, get_config, get_db, get_index_worker
    from pyrite.services.index_worker import IndexWorker

    created_dbs: list[PyriteDB] = []
    created_workers: list[IndexWorker] = []
    counter = {"n": 0}

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
        counter["n"] += 1
        work_dir = tmp_path / f"client-{counter['n']}"
        db_path = work_dir / "index.db"
        kb_path = work_dir / "kb"
        kb_path.mkdir(parents=True, exist_ok=True)

        settings_kwargs: dict = {"index_path": db_path, "api_key": api_key}
        if api_keys is not None:
            settings_kwargs["api_keys"] = api_keys
        if cors_origins is not None:
            settings_kwargs["cors_origins"] = cors_origins
        if auth is not None:
            settings_kwargs["auth"] = auth
        if extra_settings:
            settings_kwargs.update(extra_settings)

        config = PyriteConfig(
            knowledge_bases=[KBConfig(name=kb_name, path=kb_path, kb_type="generic")],
            settings=Settings(**settings_kwargs),
        )

        application = create_app(config=config)
        db = PyriteDB(db_path)
        created_dbs.append(db)
        application.dependency_overrides[get_config] = lambda: config
        application.dependency_overrides[get_db] = lambda: db

        # `create_app()` eagerly seeds the KB registry, which calls its own
        # `_app_get_db()` and opens a SECOND PyriteDB on the same file,
        # stored as `application.state.pyrite_db`. The overrides above
        # redirect dependency injection but do NOT close that connection,
        # and some routes read it directly rather than through DI (the
        # export path in pyrite/server/endpoints/kbs.py). Nothing in
        # pyrite/ ever closes `app.state.pyrite_db`, so unless this fixture
        # owns it too, it stays open in WAL mode with its -wal/-shm files
        # live -- the very leak this fixture exists to end, merely hidden
        # by tmp_path not being deleted during the run.
        app_state_db = getattr(application.state, "pyrite_db", None)
        if app_state_db is not None and app_state_db is not db:
            created_dbs.append(app_state_db)

        index_worker = IndexWorker(db, config)
        created_workers.append(index_worker)
        application.dependency_overrides[get_index_worker] = lambda: index_worker

        if dependency_overrides:
            for dep, override in dependency_overrides.items():
                application.dependency_overrides[dep] = override

        client = TestClient(application)

        if register_user is not None:
            # The operator path, not /auth/register: registration is closed
            # until an admin exists. See tests/auth_seed.py.
            from tests.auth_seed import seed_and_sign_in

            username, password = register_user
            seed_and_sign_in(client, username, password)

        return client, config, db

    yield _make

    # Teardown order matters: join/stop every index worker's background
    # threads BEFORE closing the DB connections they write through, and
    # both before pytest removes tmp_path.
    #
    # Each step is individually guarded: an unguarded loop lets one failing
    # join or close leak every DB after it, which is the same class of bug
    # this fixture exists to fix.
    errors: list[BaseException] = []
    for index_worker in created_workers:
        try:
            index_worker.wait_for_idle(timeout=10)
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            errors.append(exc)
    for db in created_dbs:
        try:
            db.close()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            errors.append(exc)
    if errors:
        raise errors[0]
