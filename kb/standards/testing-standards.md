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
- Tests of experimental surfaces carry the `experimental` marker, applied by
  path from `tests/experimental_surface.py` (never by hand). They do not gate a
  merge or a push; CI's `experimental` job runs them against
  `tests/experimental_known_failures.txt`, a list that can only shrink, and a
  new failure on `dev` opens an `experimental-broken` issue (#657). A test of a
  security property (authorization, read scoping, containment, credentials,
  the characterization oracle, escaping) is never experimental: list it in
  that file's `NEVER_EXPERIMENTAL` when its path is mapped experimental

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
