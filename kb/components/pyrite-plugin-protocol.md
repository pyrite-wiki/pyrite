---
id: pyrite-plugin-protocol
title: PyritePlugin Protocol
type: component
kind: protocol
path: pyrite/plugins/protocol.py
owner: core
dependencies:
- pyrite.plugins.context
tags:
- core
- plugins
- protocol
---

The single extension point for all domain-specific capabilities. A `@runtime_checkable` protocol with 20 optional methods — plugins only implement what they need. Covers entry types, CLI commands, MCP tools, DB schema, relationships, workflows, hooks, KB presets, validators, rubric checkers, and orient supplements.

## Key Methods

**Registration**

- `get_entry_types()` — register custom entry type classes with the schema layer
- `get_cli_commands()` — contribute typer sub-apps to the `pyrite` CLI
- `get_mcp_tools(tier)` — contribute MCP tools at read/write/admin tiers
- `get_relationship_types()` — named link types with inverses (e.g. `cites` ↔ `cited-by`)

**Behaviour**

- `get_workflows()` — state machine definitions for entry lifecycle
- `get_hooks()` — before/after save/delete/index lifecycle hooks
- `get_validators()` — entry validation functions run on save
- `get_rubric_checkers()` — named QA checker functions used by the QA service

**Configuration**

- `get_kb_presets()` — preset configs, used by `pyrite init --template <name>`
- `set_context(ctx)` — receive shared config/db from the plugin registry at startup

## Implementors

All extension plugins implement this protocol:

- `software-kb` — components, ADRs, backlog, standards
- `zettelkasten` — atomic notes, literature notes
- `social` — writeups, user profiles
- `encyclopedia` — articles, stubs
- `journalism-investigation` — sources, claims, entities, connections

## Consumers

- `PluginRegistry` — discovers and initialises plugins, calls `set_context`
- MCP server — aggregates `get_mcp_tools` across all loaded plugins
- `KBService` — invokes hooks and validators during write operations
- Schema layer — merges `get_entry_types` into the type registry
- QA service — runs `get_rubric_checkers` during quality analysis

## Related

- [[plugin-system]] — registry, loading, and context injection
- [[mcp-server]] — consumes plugin-contributed tools at each tier
