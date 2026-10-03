---
id: mcp-server
type: component
title: "MCP Server"
kind: service
path: "pyrite/server/mcp_server.py"
owner: "markr"
dependencies: ["pyrite.plugins", "pyrite.storage", "pyrite.config"]
tags: [core, mcp, ai-agents]
---

The MCP (Model Context Protocol) server exposes pyrite tools to AI agents like Claude Code over stdio. Implemented as `PyriteMCPServer`, it uses the official `mcp` SDK to serve tools, prompts, and resources.

## Three-Tier Tool Model

Each server instance runs at a single tier, which determines which tools are available:

| Tier | Access Level | Tools |
|------|-------------|-------|
| **read** | Safe for any agent | kb_list, kb_search, kb_get, kb_timeline, kb_backlinks, kb_tags, kb_stats, kb_schema, kb_orient, kb_batch_read, kb_list_entries, kb_recent, kb_qa_validate, kb_qa_status |
| **write** | Trusted agents | read + kb_create, kb_bulk_create, kb_update, kb_delete, kb_link, kb_qa_assess |
| **admin** | Full control | write + kb_index_sync, kb_manage, kb_commit, kb_push |

Tier is set at construction time via the `tier` parameter and validated against `VALID_TIERS`. Invalid tiers raise `ConfigError`.

## The first call and the KB refusal

`kb_orient` is the first call of a session. With no `kb_name` it answers from `KBService.orient_overview` with the KBs the caller may read in pages of 50 by default (maximum 100); use `offset` when `has_more` is true. The handler applies `readable_kbs` before paging, which also lets a scoped caller past the dispatcher's fail-closed rule for a call that names no KB. With a `kb_name` it answers from `KBService.orient`; `detail="brief"` drops the write-side schema blocks (`ai_instructions`, `evaluation_rubric`, `guidelines`, `goals`, and the top-level `guidelines`) but keeps `relationship_types`, and blocks a plugin adds (`get_orient_supplement`) are returned whole.

A caller with a readable set is refused by `_kb_not_found` for any KB name outside that set, absent or private alike, before a handler runs, so one caller cannot tell the two apart. The code is `KB_NOT_FOUND` (with `legacy_error_code: NOT_FOUND` for 0.25.7 only). Handlers' own not-found answers are only ever reached for a name the caller may read, so they need not match it; several still say `NOT_FOUND` and move with the error-code sweep.

The types orient and `kb_schema` list come from `KBSchema.declared_types()`, the rule the write path's `UNDECLARED_TYPE` refusal uses.

## Plugin Tool Merging

After core tools are registered, `_register_plugin_tools()` calls `registry.get_all_mcp_tools(self.tier)` to collect plugin-provided tools. Plugin tools are merged into the same `self.tools` dict, making them indistinguishable from core tools to MCP clients. Plugins register tools via the `get_mcp_tools(tier)` protocol method. Plugin loading failures are silently caught to avoid breaking the server.

Plugins receive a `PluginContext` with shared `config` and `db` references so they don't need to bootstrap their own connections.

## Prompts

Four built-in prompts available at all tiers:

- `research_topic` — search across all KBs, summarize findings, identify gaps
- `summarize_entry` — fetch entry and generate concise summary
- `find_connections` — analyze relationships between two entries
- `daily_briefing` — summarize recent timeline events (configurable lookback days)

Each prompt returns MCP `PromptMessage` objects with pre-built user messages.

## Resources

Static resources and URI templates for browsing:

- `pyrite://kbs` — list all knowledge bases
- `pyrite://kbs/{name}/entries` — list entries in a KB (limit 200)
- `pyrite://entries/{id}` — get a specific entry

Resources are read via `_read_resource()` which dispatches based on URI prefix matching.

## SDK Integration

`build_sdk_server()` creates an `mcp.server.Server` instance and registers async handlers for all MCP protocol methods (list_tools, call_tool, list_prompts, get_prompt, list_resources, list_resource_templates, read_resource). The server runs over stdio via `anyio` in `run_stdio()`.

## Post-Save QA Validation

Write tools (`kb_create`, `kb_update`) support opt-in QA validation via two mechanisms:

1. **Per-request**: Pass `validate: true` to run `QAService.validate_entry()` after save
2. **KB-level**: Set `validation.qa_on_write: true` in `kb.yaml` — all writes auto-validate

When triggered, the `_maybe_validate()` helper runs structural QA and appends `qa_issues` to the response if any issues are found. Clean entries return no `qa_issues` key.

## Configuration and Startup

- CLI: `pyrite mcp --tier write` or `pyrite-admin mcp`
- Entry point: `main()` in `mcp_server.py` parses `--tier` flag
- Default tier: `read`
- The server creates its own `PyriteDB` and `KBService` instances
- `close()` must be called to release the DB connection

## Consumers

- Claude Code via `.claude-plugin/plugin.json` MCP server config
- Claude Desktop / Cline via manual MCP server setup
- Any MCP-compatible client over stdio

## Related

- [[rest-api]] — HTTP equivalent of MCP tools
- [[kb-service]] — business logic layer used by all tool handlers
- [[storage-layer]] — database access
- [[schema-validation]] — `kb_schema` tool uses `KBSchema.to_agent_schema()`
- [ADR-0006: MCP Three-Tier Tool Model](../adrs/0006-mcp-three-tier-tool-model.md) — defines the read/write/admin tier architecture
