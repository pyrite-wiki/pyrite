---
id: adr-0002
type: adr
title: "Plugin System via Python Entry Points"
adr_number: 2
status: accepted
deciders: ["markr"]
date: "2025-10-01"
tags: [architecture, plugins]
links:
- target: adr-0040
  relation: amended_by
  kb: pyrite
---

> **Amended by [[adr-0040]] (2026-09-26).** Extensions move out of tree and
> the plugin contract becomes the public API (a `pyrite.plugin_api` facade,
> `PLUGIN_API_VERSION`, an operator allowlist). The entry-point mechanism and
> the capability declarations below stand. ADR-0040 decision 2 deletes the
> `cascade` extension rather than extracting it.

## Context

Pyrite needs extensibility for different KB use cases (zettelkasten, social, encyclopedia, software). We needed a mechanism for plugins to register entry types, validators, CLI commands, MCP tools, and workflows without modifying core.

## Decision

Use Python `importlib.metadata` entry points under the `pyrite.plugins` group. Each extension declares a plugin class in its `pyproject.toml`. The `PluginRegistry` discovers and loads plugins at startup.

## Consequences

- Extensions are pip-installable packages — standard Python tooling
- Plugins coexist without conflicts — tested with 6 concurrent extensions
- Extensions must be `pip install -e` in the venv for pre-commit hooks to pass

## Addendum (2026-03-26): Protocol Growth

The plugin protocol has grown from 5 integration points and 11 methods to **19 methods** covering:

1. `get_entry_types()` — custom entry type classes
2. `get_type_metadata()` — field definitions, AI instructions, presets
3. `get_collection_types()` — custom collection types
4. `get_mcp_tools(tier)` — per-tier MCP tools
5. `get_cli_commands()` — Typer sub-commands
6. `get_validators()` — entry validation rules
7. `get_relationship_types()` — semantic relationship definitions
8. `get_hooks()` — lifecycle hooks (before/after save/delete)
9. `get_db_columns()` — additional DB columns for entry table
10. `get_db_tables()` — custom DB tables
11. `get_migrations()` — schema migration functions
12. `get_kb_presets()` — KB template presets
13. `get_field_schemas()` — custom field type schemas
14. `get_protocols()` — structural protocol mixins
15. `get_orient_supplement()` — additional orient output
16. `get_rubric_checkers()` — QA rubric evaluation rules
17. `get_workflows()` — workflow definitions
18. `get_kb_types()` — KB type declarations
19. `set_context(ctx)` — receives config, db, and services at startup

Six extensions ship: software-kb, zettelkasten, encyclopedia, social, journalism-investigation, cascade.

## Addendum (2026-06-11): Capability Declarations (Tier A r1500, Option B)

The 19-method Protocol from the prior addendum was costing per-plugin
dispatch overhead and a silent-failure surface that grew with the
method count — every method is another `hasattr` guard, another
`getattr` call, and another silent-skip site if the method exists
but returns empty. The audit for r1500 documented two specific
patterns:

- ~12 of 19 methods return empty in any given extension. Pure cost,
  no value.
- "Does this plugin extend storage?" was a runtime question (does
  `get_db_columns` return non-empty?), not a structural one — making
  it impossible to reason about plugin scope without calling every
  method.

Option B chosen over Option A (splitting the Protocol into 5
capability protocols) because it ships in one commit with a small
diff per plugin, leaves the Protocol interface stable, and is a
strict subset of Option A's data: every Capability member can become
its own Protocol later without changing the declared `capabilities`
sets. Option A is reserved as a deprecation-cycle follow-up.

### Capability enum

`pyrite.plugins.capabilities.Capability(StrEnum)` declares the 5
subsystem groupings the 19 methods clustered into:

```python
class Capability(StrEnum):
    SCHEMA   = "schema"    # entry types, type metadata, collection types,
                           # field schemas, structural protocols
    STORAGE  = "storage"   # db columns, db tables, migrations, validators,
                           # hooks
    SURFACE  = "surface"   # CLI commands, MCP tools, KB presets, KB types
    DOMAIN   = "domain"    # relationship types, workflows, rubric checkers
    CONTEXT  = "context"   # set_context, get_orient_supplement
```

Names mirror the 5-subsystem split so the eventual move to Option A
is mechanical — each Capability becomes its own Protocol with the
same name.

### Declaration

Plugins declare a `capabilities: ClassVar[set[Capability]]` class
attribute. The registry consults this set before dispatching to each
method, skipping methods whose capability the plugin did not claim.

```python
class MyPlugin:
    name = "my-plugin"
    capabilities: ClassVar[set[Capability]] = {
        Capability.SCHEMA, Capability.SURFACE,
    }
```

### Empty-set default

A plugin with no `capabilities` attribute is treated as having ZERO
declared capabilities — the registry skips ALL its dispatch loops.
This is the safe failure mode: a plugin that forgets to declare
gets ignored entirely rather than silently half-loaded. Mitigated
by the same-commit migration of every in-tree plugin to declare its
real set, with a regression test
(`test_plugin_without_capabilities_attribute_is_skipped_for_all`)
that pins the contract.

### Method-to-capability map

`pyrite/plugins/registry.py:_METHOD_CAPABILITIES` hard-codes the
mapping from method name to Capability — 19 entries, one per
dispatched method. New methods added to the Protocol must be added
to this dict at the same time, enforced by
`test_every_dispatched_method_has_a_capability`.

### Drift behavior

When a plugin returns non-empty from a method whose capability it
did NOT declare:

- **Default (warn-and-skip):** the registry logs a WARNING with
  plugin name + method name + expected capability, and drops the
  return value from aggregation.
- **`strict_plugins=True`:** mirrors the existing strict-discover
  toggle behavior at the load level — raises `PluginError`.

### Migration of in-tree extensions

All 6 in-tree extensions migrated in the same commit
(`af38db4`):

- zettelkasten:              SCHEMA STORAGE SURFACE DOMAIN CONTEXT
- social:                    SCHEMA STORAGE SURFACE         CONTEXT
- encyclopedia:              SCHEMA STORAGE SURFACE DOMAIN CONTEXT
- cascade:                   SCHEMA STORAGE SURFACE DOMAIN CONTEXT
- journalism_investigation:  SCHEMA STORAGE SURFACE DOMAIN CONTEXT
- software_kb:               SCHEMA STORAGE SURFACE DOMAIN CONTEXT

Audited by a script that constructs each plugin, calls every
`get_*` method in `_METHOD_CAPABILITIES`, and infers the declared
set from which calls return non-empty data. The capabilities
listed above are the result of that audit, not aspirational.

### Reservation for Option A

When the cost-benefit of Option A becomes worth the migration
churn — i.e. when the dispatch-skip alone stops being enough and
plugin authors want compile-time guarantees that they're
implementing the right protocol — a successor ADR will document
the split into 5 Protocol classes. The Capability member names
above are reserved as the Protocol class names so the migration
is `git mv` plus moving each plugin's class definition through the
multi-inherit pattern.

### Related

- Tier A r1500 ticket: `split-plugin-protocol-into-capability-protocols`
- `admin-plugin-info-endpoint-should-expose-has-errors-error-list-when-partial-aggregation-occurred`
  (the partial-aggregation surface gets cleaner once dispatch-skip
  reduces the per-plugin call count)
