---
id: plugin-system
type: component
title: "Plugin System"
kind: module
path: "pyrite/plugins/"
owner: "markr"
dependencies: ["pyrite.models", "pyrite.schema"]
tags: [core, extensibility]
---

Lazy-loaded plugin system using Python entry points for discovery and a structural protocol for capabilities. Plugins are ordinary classes implementing any subset of 20 optional methods — no base class required. The registry aggregates contributions across all loaded plugins with collision detection.

## Architecture

- `protocol.py` — `PyritePlugin` @runtime_checkable protocol (20 methods)
- `registry.py` — `PluginRegistry` singleton, lazy discovery via `importlib.metadata.entry_points(group="pyrite.plugins")`
- `context.py` — `PluginContext` dataclass injected into plugins via `set_context()`
- `capabilities.py` — the `Capability` enum (schema, storage, surface, domain, context) a plugin declares in `capabilities`
- `removed.py` — extensions Pyrite once shipped and deleted, and the KB types they owned, so a stale `kb_type` gets a plain message
- `scoping.py` — `kb_scope_clause`: the one rule that narrows a plugin tool's optional KB parameter to the caller's readable KBs

## Plugin Lifecycle

1. `get_registry()` triggers lazy discovery of entry points
2. Each plugin class is instantiated and `set_context()` injects shared config/db
3. Registry exposes `get_all_*()` aggregation methods per capability axis, and skips each method whose `Capability` the plugin did not declare (`_METHOD_CAPABILITIES` in `registry.py`)
4. Dict capabilities warn on key collisions, list capabilities extend, dict-of-list merge by key
5. `get_hooks_for_kb()`/`get_validators_for_kb()` filter hooks/validators by KB type for domain-scoped plugins — pure lookups, not runners

## Validator and hook contract (checked once at registration)

- **Validators** bind `(entry_type: str, fields: dict, ctx: dict) -> list[dict]`.
  **Hooks** bind `(entry, ctx)`. There is exactly one contract each; no 2-argument
  validator fallback exists.
- The signature is checked with `inspect.signature(...).bind` the first time a
  plugin's validators/hooks are needed (`PluginRegistry._conformance_for`),
  and the result — including which `before_*` hook points had a non-conforming
  callable dropped — is cached for the registry's lifetime, not re-checked
  (and re-warned) on every call.
- `run_validators(kb_type, entry_type, fields, ctx)` is the single call site
  both `schema/kb_schema.py` and `storage/index.py` use to run plugin
  validators; it degrades per-validator (one raising validator does not
  suppress the rest) and drops any non-dict item a validator returns.
- **`HookRunner`** (`pyrite/services/hook_runner.py`), not the registry, runs
  hooks — one raise-before/swallow-after contract for both core and plugin
  hooks. A dropped `before_*` hook fails that KB's `before_*` dispatch
  closed (the write is refused, same as a raising hook); a dropped `after_*`
  hook only warns. `PluginRegistry.run_hooks`/`run_hooks_for_kb` (which used
  to run hooks directly) are gone.

## Hooks and writes

A `before_save` hook may refuse a write by raising. On update its changes are ignored (ADR-0042 decision 4); only create keeps them. An `after_save` hook never writes another entry.

## Capabilities

Entry types, CLI commands, MCP tools, DB schema (columns + tables), relationship types, workflows, hooks (before/after save/delete/index), KB presets, field schemas, type metadata, collection types, validators, migrations, protocols, orient supplements, rubric checkers

## Related

- [[pyrite-plugin-protocol]] — the protocol definition
- [[mcp-server]] — MCP tool registration from plugins
- [[software-kb-extension]] — canonical plugin implementation
