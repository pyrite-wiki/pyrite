# Testing Conventions for Pyrite

**How to run the tests** -- everything, a subset, the core set,
`scripts/test-affected`, test sizes and when to run what -- is in
[CONTRIBUTING.md, "Running the tests"](../../../CONTRIBUTING.md#running-the-tests).
In short: medium tests through the real wiring are the default; run
`scripts/test-affected --run` while you work; the PR's CI runs the full suite.
Tests of experimental surfaces carry the `experimental` marker, applied from
`tests/experimental_surface.py`; they run in their own non-blocking job and
locally with `--experimental` (same section).

## Writing a medium test

A medium test drives one real surface (REST, MCP or the CLI) against a real
world, in-process. Build the world with `tests/_world.py`, or with one of the
fixtures over it, and the test runs the production wiring:

- the config is `config.yaml` in a real config dir, written by `save_config`
  and read back by `load_config`. A setting or KB field (`types`,
  `policies`) that file cannot express is refused when the world is built,
  so it never silently falls back to its default;
- a KB is a directory of files, and `world.add_entry(kb, text)` writes one
  file and indexes it through the indexer;
- REST is `create_app(config)` with no `dependency_overrides`. `get_db` is the
  app's own per-request session (#131), and the `TestClient` is entered, so
  the lifespan runs;
- the world closes everything it opened, including `app.state.pyrite_db`.

```python
def test_one_note_on_every_surface(tmp_path):
    from pyrite.config import KBConfig
    from tests._world import build_world

    world = build_world(tmp_path, kbs=[KBConfig(name="notes", path=tmp_path / "notes")])
    try:
        world.add_entry("notes", "---\nid: hello\ntype: note\ntitle: Hello\n---\n\nHi\n")
        assert world.client.get("/api/entries/hello?kb=notes").json()["title"] == "Hello"
        assert world.cli("get", "hello", "-k", "notes", "--format", "json").exit_code == 0
        assert world.mcp("read")._dispatch_tool("kb_get", {"entry_id": "hello"})["entry"]
    finally:
        world.close()
```

The rules:

1. **Seed files, not rows.** Do not seed with `db.upsert_entry` or a raw
   `INSERT` under a surface test. A row with no file behind it cannot show
   file/index divergence, and a `get` that should read the file passes on the
   row alone.
2. **Do not override `get_db` and do not patch `load_config`.** The world
   gives you both for real. `tests/test_production_wiring_guard.py` counts
   the sites still left (`tests/production_wiring_baseline.txt`). The count
   only shrinks: a new site fails, and so does a file that dropped below its
   line until you lower that line.
3. **Stub only the process edge.** An LLM provider, a git remote or an HTTP
   call is an edge. Override that one dependency (`make_client(...,
   dependency_overrides={get_repo_service: ...})`); never the database or the
   config. The edges allowed by name are listed in
   `tests/test_tests_drive_the_surface_not_the_mock.py`.
4. **Point the CLI, don't patch it.** `cli_world()` points this process's
   config location at the world for the rest of the test. Setting
   `PYRITE_CONFIG_DIR` alone with `monkeypatch.setenv` does not do that:
   `pyrite.config` reads the variable once, at import, and the repo-wide
   `_isolate_global_config` fixture pins `CONFIG_DIR` for every test.
5. **One world per write.** A test that writes gets its own world (a
   function-scoped fixture). A session- or module-scoped world is read-only
   by convention (`tests/characterization/conftest.py`).

### Which fixture?

| You are testing | Use | You get |
|---|---|---|
| a REST route, with your own KB/auth/API keys | `make_client(kb_name=, api_key=, api_keys=, auth=, register_user=)` | `(client, config, db)`; call it again for a second app |
| a REST route over the standard events + research sample data | `rest_api_env` (with `sample_events` for their ids) | `{"client", "config", "db", "events_kb", "research_kb"}` |
| an MCP tool | `mcp_server(kb_defs, tier=, extra_setup=)` from `tests/_world.py` (`_make_mcp_server` in `tests/test_mcp_server.py`; seed: `tests/mcp_seed.py`) | `{"server", "config", <kb name>: KBConfig}` |
| a CLI command | `cli_world(kbs=, settings=)` | a world: `.cli(*args)`, `.add_entry`, `.config_dir` |
| one property across REST, MCP and the CLI | `build_world(tmp_path, kbs=, settings=)` from `tests/_world.py` | `.client`, `.mcp(tier)`, `.cli(...)`; call `.close()` |
| the access matrix (principals x KB states) | `world` / `write_world` (`tests/characterization/conftest.py`) | principals, the ADR-0037 KB axis, goldens |
| a service or storage function, no surface | `kb_service`, `pyrite_db`, `indexed_test_env` | the objects, no app |

## Extension Tests

See [extensions.md](extensions.md#8-section-extension-test-structure) for the 8-section extension test template.

## Important Test Patterns

### Use `in` not `len` for registry assertions

Other plugins may be installed in the venv. Your tests should check that your types are present, not that the total count is exact.

```python
# Good — works regardless of what else is installed
assert "my_type" in registry.get_all_entry_types()

# Bad — breaks if another plugin is installed
assert len(registry.get_all_entry_types()) == 1
```

### Isolated plugin registry fixture

For core integration tests that swap the global registry:

```python
@pytest.fixture
def patched_registry():
    import pyrite.plugins.registry as reg_module
    from pyrite.plugins.registry import PluginRegistry

    old = reg_module._registry
    reg_module._registry = PluginRegistry()
    yield reg_module._registry
    reg_module._registry = old
```

Or inline in tests:

```python
def test_entry_class_resolution(self):
    registry = PluginRegistry()
    registry.register(MyExtensionPlugin())

    import pyrite.plugins.registry as reg_module
    old = reg_module._registry
    reg_module._registry = registry

    try:
        from pyrite.models.core_types import get_entry_class
        cls = get_entry_class("my_type")
        assert cls is MyEntry
    finally:
        reg_module._registry = old
```

### Temporary KB fixture (backend)

```python
@pytest.fixture
def tmp_kb(tmp_path):
    kb_dir = tmp_path / "test-kb"
    kb_dir.mkdir()
    (kb_dir / "kb.yaml").write_text("name: test\nkb_type: software\n")
    return kb_dir
```

### Validator test pattern

Always test: valid passes, invalid caught, **unrelated types return `[]`**.

```python
class TestValidators:
    def test_valid_data(self):
        errors = validate_my_type("my_type", {"custom_field": "value"}, {})
        assert errors == []

    def test_missing_required(self):
        errors = validate_my_type("my_type", {}, {})
        assert any(e["field"] == "custom_field" for e in errors)

    def test_ignores_unrelated_types(self):
        errors = validate_my_type("note", {"title": "whatever"}, {})
        assert errors == []

    def test_ignores_event_type(self):
        errors = validate_my_type("event", {"title": "whatever"}, {})
        assert errors == []
```

## Running Tests

### Backend

```bash
# Single test
.venv/bin/pytest tests/test_file.py::TestClass::test_name -v

# Extension tests
.venv/bin/pytest extensions/my-extension/tests/ -v

# All backend tests (the whole suite is a heavy run: take the lock)
scripts/heavy_lock.py -- .venv/bin/pytest tests/ extensions/*/tests/ -n 4 -q

# With print output visible
.venv/bin/pytest tests/test_file.py -v -s --tb=long
```

### Frontend Unit (Vitest)

```bash
cd web

# Single test file
npx vitest run src/lib/stores/entries.test.ts

# Single test by name
npx vitest run src/lib/stores/entries.test.ts -t "test name"

# All unit tests
npm run test:unit
```

**Svelte 5 config:** Vitest config must have `resolve.conditions: ['browser', 'svelte']` to avoid "mount not available from server-side bundle" errors.

### Frontend E2E (Playwright)

```bash
# All E2E (npm run test:e2e in web/ is the same script)
scripts/e2e.sh

# Single spec
scripts/e2e.sh e2e/file.spec.ts

# Headed (see the browser)
scripts/e2e.sh --headed

# Debug mode (step through)
scripts/e2e.sh --debug
```

`scripts/e2e.sh` runs `npx playwright test` in `web/` with your arguments,
under the machine-wide heavy-run lock (`scripts/heavy_lock.py`, #168): one
suite, verify-red or Playwright run at a time on the machine; a second waits
and says who holds the lock. Local workers: `PW_WORKERS=N`, else Playwright's
default (half the cores). It is not bounded lower yet: `e2e/seed.spec.ts`
times out at 1-2 workers (#906); bound it once that is fixed.

**Playwright config** starts four servers: FastAPI and Vite, and a second pair
with auth enabled. Their ports are derived per worktree (`web/e2e/ports.ts`),
so two worktrees can run Playwright at once; `cat .pyrite/e2e-ports` shows
this worktree's. `PLAYWRIGHT_E2E_PORT` and `PLAYWRIGHT_E2E_VITE_PORT` override
the first pair. Every server has `reuseExistingServer: false`: Playwright
never reuses a server already running, and a port in use fails the run.

## Pre-Commit Hooks

What each hook stage runs, and why: [CONTRIBUTING, "Branches, hooks and pull
requests"](../../../CONTRIBUTING.md#branches-hooks-and-pull-requests).

**Double-failure pattern:** If ruff-format reformats files, the first commit attempt fails. The reformatted files are unstaged. Re-stage those paths and commit again:

```bash
git add <the files ruff-format changed> && git commit -m "same message"
```

**Extension discovery:** the tests run from `.venv/`. If your extension isn't `pip install -e` in the venv, its tests will fail with `ModuleNotFoundError: No module named 'pyrite_my_extension'`.
