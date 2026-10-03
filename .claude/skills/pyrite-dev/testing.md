# Testing Conventions for Pyrite

**How to run the tests** -- everything, a subset, the core set,
`scripts/test-affected`, test sizes and when to run what -- is in
[CONTRIBUTING.md, "Running the tests"](../../../CONTRIBUTING.md#running-the-tests).
In short: medium tests through the real wiring are the default; run
`scripts/test-affected --run` while you work; the PR's CI runs the full suite.
Tests of experimental surfaces carry the `experimental` marker, applied from
`tests/experimental_surface.py`; they run in their own non-blocking job and
locally with `--experimental` (same section).

## 8-Section Extension Test Structure

Every extension test file follows this structure. Not every section is required — only include sections for capabilities your extension implements.

```python
"""Tests for the my_extension extension."""

from pyrite_my_extension.entry_types import MyEntry
from pyrite_my_extension.plugin import MyExtensionPlugin
from pyrite_my_extension.preset import MY_PRESET
from pyrite_my_extension.validators import validate_my_type

from pyrite.plugins.registry import PluginRegistry

# =========================================================================
# 1. Plugin registration
# =========================================================================

class TestPluginRegistration:
    def test_plugin_has_name(self):
        plugin = MyExtensionPlugin()
        assert plugin.name == "my_extension"

    def test_register_with_registry(self):
        registry = PluginRegistry()
        registry.register(MyExtensionPlugin())
        assert "my_extension" in registry.list_plugins()

    def test_entry_types_registered(self):
        registry = PluginRegistry()
        registry.register(MyExtensionPlugin())
        types = registry.get_all_entry_types()
        assert "my_type" in types  # Use `in`, not len()

    # ... validators, commands, presets, kb_types, mcp_tools

# =========================================================================
# 2. Entry types
# =========================================================================

class TestMyEntry:
    def test_default_values(self): ...
    def test_entry_type_property(self): ...
    def test_to_frontmatter(self): ...
    def test_to_frontmatter_omits_defaults(self): ...
    def test_from_frontmatter(self): ...
    def test_from_frontmatter_generates_id(self): ...
    def test_roundtrip_markdown(self): ...

# =========================================================================
# 3. Validators
# =========================================================================

class TestValidators:
    def test_valid_data_passes(self): ...
    def test_required_field_missing(self): ...
    def test_invalid_enum_value(self): ...
    def test_ignores_unrelated_types(self): ...  # CRITICAL: always include

# =========================================================================
# 4. Hooks (if applicable)
# =========================================================================

class TestHooks:
    def test_before_save_modifies_entry(self): ...
    def test_before_save_raises_on_violation(self): ...
    def test_after_save_side_effect(self): ...
    def test_hooks_ignore_unrelated_types(self): ...

# =========================================================================
# 5. Workflows (if applicable)
# =========================================================================

class TestWorkflows:
    def test_valid_transitions(self): ...
    def test_invalid_transition_rejected(self): ...
    def test_initial_state(self): ...

# =========================================================================
# 6. DB Tables (if applicable)
# =========================================================================

class TestDBTables:
    def test_table_definitions_valid(self): ...
    def test_table_names_prefixed(self): ...

# =========================================================================
# 7. Preset
# =========================================================================

class TestPreset:
    def test_preset_structure(self): ...
    def test_preset_contains_expected_types(self): ...
    def test_preset_directories(self): ...

# =========================================================================
# 8. Core integration
# =========================================================================

class TestCoreIntegration:
    def test_entry_class_resolution(self): ...
    def test_entry_from_frontmatter_resolution(self): ...
    def test_relationship_types_merged(self): ...
```

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

# All backend tests
.venv/bin/pytest tests/ -v

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
cd web

# All E2E
npm run test:e2e

# Single spec
npx playwright test e2e/file.spec.ts

# Headed (see the browser)
npx playwright test --headed

# Debug mode (step through)
npx playwright test --debug
```

**Playwright config** starts both FastAPI (:8088) and Vite (:5173) dev servers. Uses `reuseExistingServer` in dev so you can keep servers running.

## Pre-Commit Hooks

The `.pre-commit-config.yaml` runs:

1. **ruff** — lint check
2. **ruff-format** — auto-format
3. **pytest** — runs in `.venv/`

**Double-failure pattern:** If ruff-format reformats files, the first commit attempt fails. The reformatted files are unstaged. Re-stage and commit again:

```bash
git add -u && git commit -m "same message"
```

**Extension discovery:** pytest pre-commit uses `.venv/`. If your extension isn't `pip install -e` in the venv, its tests will fail with `ModuleNotFoundError: No module named 'pyrite_my_extension'`.
