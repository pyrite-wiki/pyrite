- **`scripts/test-affected` selects tests reaching plugins via helpers (#1886).**
  Changes under `extensions/*/src/` now select tests that reach the plugin
  registry through imported helpers (such as `world.py` in characterization
  tests) or conftest fixtures.
