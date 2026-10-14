---
id: testing-standards
type: standard
title: "Testing Standards"
category: testing
enforced: true
tags: [testing, pytest]
---

## Framework
- pytest with rich output
- The commit hooks run no tests; the pre-push hook runs the core plus the
  affected tests (`scripts/test-affected --run`); CI is the authority
- The pre-push hook does not run when the pushed tree already passed here
  (a pass stamp) or the pushed tip commit is verified: the required check
  (`gate`) green on that exact SHA on GitHub, by the one definition in
  `scripts/verified_commit.py` that `scripts/release.py` also uses. That
  lookup fails closed: anything short of a readable green answer for that
  SHA means the tests run. A verified tip says CI accepted the commit, not
  that a given selection ran, so `PYRITE_PUSH_FULL=1` and
  `PYRITE_PUSH_EXPERIMENTAL=1` still run
- Tests of experimental surfaces carry the `experimental` marker, applied by
  path from `tests/experimental_surface.py` (never by hand). They do not gate a
  merge or a push; CI's `experimental` job runs them against
  `tests/experimental_known_failures.txt`, a list that can only shrink, and a
  new failure on `dev` opens an `experimental-broken` issue (#657). A test of a
  security property (authorization, read scoping, containment, credentials,
  the characterization oracle, escaping) is never experimental: list it in
  that file's `NEVER_EXPERIMENTAL` when its path is mapped experimental (a
  test fails on any experimental test touching security vocabulary until it
  is listed there or reviewed with a reason)

## Test Structure for Extensions (proven 8-section pattern)
1. **TestPluginRegistration** — verify name, all capabilities in registry (use `in` not `len ==`)
2. **TestEntryType** — defaults, to_frontmatter, from_frontmatter, roundtrip_markdown
3. **TestValidators** — one test per rule (positive + negative), test ignores-other-types
4. **TestHooks** — direct call + `HookRunner(plugin_registry=...)` tests (`PluginRegistry.run_hooks`/`run_hooks_for_kb` are gone -- `registry.get_hooks_for_kb` is a pure lookup, `HookRunner` is what runs hooks, #379)
5. **TestWorkflows** — each transition allowed/blocked, requires_reason
6. **TestDBTables** — definition checks + actual SQLite creation in tmpdir
7. **TestPreset** — structure, directories, validation rules
8. **TestCoreIntegration** — entry_class_resolution, entry_from_frontmatter, multi-plugin coexistence

## Key Rules
- Use `in` checks not exact counts for registry assertions (other installed plugins affect counts)
- Each test class should create a fresh PluginRegistry to avoid cross-test contamination
- Use try/finally when patching `reg_module._registry` to guarantee restoration
- A test that drives an entry point (`CliRunner`, `TestClient`, MCP dispatch, `PluginContext`) does not patch the service behind it. Seed the world (`tests/search_verdict_seed.py`: `make_cli_world`, `seed_entry_world`, `install_fake_model`; `tests/auth_seed.py`) and let the real service answer. `tests/test_tests_drive_the_surface_not_the_mock.py` enforces it: `file:line: <target> is the service behind <entry point>`. Existing violators are on its `ALLOWLIST`, which can only shrink.
- The process edge may be patched: `subprocess`, `httpx`, `requests`, `urllib`, `socket`, `smtplib`, `time`, `datetime`, `os`, `sys`, `shutil`, `webbrowser`, `_import_anthropic`/`_import_openai`, the embedding model (`embedding_service.is_available`, `_load_model`; a model failure is `FailingModel`), config loading (`load_config`), and module constants (an ALL_CAPS name, such as `_SQLITE_VEC_MAX_K`, by string or object target alike). A patch the walk cannot trace to a name (a local instance, a variable, an f-string) is reported as `<unresolved>` and listed, never ignored. The allowlist is per file, not per site.
- To model a different backend, hand the database one (`_with_backend` in `tests/test_search_filters_across_modes.py`: a subclass instance with its own `capabilities` or `search_semantic`); do not patch the shipped class.
