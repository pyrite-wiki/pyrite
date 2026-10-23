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

## Core calls a plugin command makes

- `pyrite.cli.context.require_kb(config, name, output_format=...)` -- resolve a `-k` (an unknown name or a shortname is `KB_NOT_FOUND`).
- `pyrite.cli.context.require_entry_kb(entry_id, kb_name, entry_type=..., output_format=...)` (#911) -- the KB an entry id names when `-k` is optional: found, `AMBIGUOUS` with `candidate_kbs`, or `NOT_FOUND`, through `KBService.locate` (the same rule as `pyrite get`). Call it before reading or writing and work in the KB it returns, never in `kb_name or ""`. Over MCP, a tool that takes an entry id declares `"locates_entry": {"id_args": [...], "entry_type": ...}` beside its `handler` (`entry_type` omitted for a tool that takes any type); `PyriteMCPServer._dispatch_tool` then resolves the id through `KBService.locate(..., readable_kbs=...)` and passes the handler the KB as `kb_name` -- a blank `kb_name` is no KB named. With `kb_name` not named it does so before its read, write and landing checks, so the handler's KB is always a checked KB (the full contract: the `get_mcp_tools` docstring in `pyrite/plugins/protocol.py`). A handler never resolves for itself, and `tests/test_plugin_mcp_tools_locate_ids.py` fails for a registered tool with an id parameter that does not declare it (#1310). One type rule on both: a command or tool that acts on one entry type passes it; one that takes any passes none (`KBService.locate`). Both belong in the plugin API symbol count that #1042 ratchets (ADR-0040).

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
