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

- CLI: `pyrite mcp --tier <tier>` or `pyrite-admin mcp --tier <tier>`; both default to `write` (ADR-0006) and reject an unknown tier with `INVALID_TIER`. `pyrite-admin mcp` defaulted to `admin` until #582.
- Entry point: `main()` in `mcp_server.py` (`python -m pyrite.server.mcp_server`, no installed script) parses `--tier`, default `write` since #582; so does the separately published `pyrite-mcp serve` (`pyrite-mcp/`). The `PyriteMCPServer(tier="read")` constructor default is library API, and every caller passes a tier.
- The server creates its own `PyriteDB` and `KBService` instances
- `close()` must be called to release the DB connection

## Consumers

`pyrite mcp-setup` (`pyrite/cli/mcp_setup_command.py`, #582) points the `pyrite` entry at this install where each client reads it:

- Claude Code, user scope: `~/.claude.json` (or `$CLAUDE_CONFIG_DIR/.claude.json`). Claude Code rewrites that file continually, so Pyrite reads it to decide and changes it only through `claude mcp add -s user` / `claude mcp remove -s user`. There is no update subcommand: a change is `remove` then `add`.
- Claude Code, project scope (`--project`): `./.mcp.json`.
- Claude Desktop: `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS), `%APPDATA%\Claude\claude_desktop_config.json` (Windows). There is no official Linux build; `$XDG_CONFIG_HOME/Claude/` (default `~/.config/Claude/`) is the path unofficial builds use.
- Any MCP-compatible client over stdio: `--config <file>`, or the snippet the command prints when it finds no client.

**The rule** (maintainer, 2026-10-02; see [[pyrite-is-a-guest-in-state-it-does-not-own]]): do what was asked, lose nothing, report it. What to do is decided from the file as it is. Nothing records what Pyrite wrote, and nothing is inferred from what an entry looks like.

| The client has | `mcp-setup` does |
|---|---|
| no `pyrite` entry | writes the default one: this install's `pyrite` by absolute path (the interpreter's scripts directory, never `PATH`), `mcp --tier <tier>`, default `write` |
| an entry equal in what was asked (the command path; the tier when `--tier` is given) | nothing: no write, no `claude` call |
| an entry that differs in what was asked | changes that field; the report holds old and new |
| an entry whose arguments, run with this install's path, fail (`<cmd> <args> --help` exits non-zero) | stops (`ENTRY_WOULD_NOT_START`); nothing written. An existing `-t X` (`pyrite-admin mcp` reads it, `pyrite mcp` does not) is rewritten to `--tier X` when the entry is changed anyway |
| an entry carrying env, extra args, a tier nobody asked to change, unknown keys | keeps them |
| an entry whose args do not begin with `mcp`, or that is not an object with a string `command` and string `args` | stops (`ENTRY_NOT_MCP`, `ENTRY_MALFORMED`) and names `--force` |
| other servers and keys, `pyrite-read`/`pyrite-write`/`pyrite-admin` | never touched; the trio is reported |

`--force` discards the entry and writes the default one; the report names what was discarded. Env values are never printed, only keys.

Env: a new Desktop or `--config` entry pins `PYRITE_CONFIG_DIR`/`PYRITE_DATA_DIR` when the shell sets them explicitly, because a GUI-launched server gets no shell environment and an undefined cwd and would load `~/.pyrite`. A repo-local `.pyrite/` is never pinned (an explicit config dir is trusted). Env is never added to an entry that exists: the report names the mismatch and the line to add. Claude Code entries carry no env; Claude Code inherits the shell.

Files Pyrite writes (`.mcp.json`, Desktop, `--config`): parsed strictly (invalid JSON, comments, NaN, a duplicate key anywhere, a non-object `mcpServers` are `CONFIG_INVALID`), changed in the one entry, and written back in the file's own indent unit, line ending, BOM, surrounding whitespace and ASCII-only-ness. Before the replace the output is parsed again with numbers as `Decimal` and compared with the original outside the entry, in order; a difference is `CONFIG_NOT_PRESERVED` and nothing is written. The file is re-read right before the replace (`CONFIG_CHANGED` if it moved). The write is `pyrite/utils/atomic_write.py`; where that helper would write in place (a hard-linked file, a directory it cannot write, another user's file) or the file is read-only, the run stops with `CONFIG_NOT_WRITABLE`. A symlink is written through. A file a client wrote comes back byte for byte outside the entry. A hand-formatted file keeps its values, but one-line arrays open out, mixed indentation becomes uniform, and escapes and numbers may be respelled (`\/`, `\uXXXX`, `1.10`).

Claude Code user scope: `claude` is never run when `~/.claude.json` exists and is empty or unparseable (`CLIENT_CONFIG_UNSAFE`; the client would replace the file). An entry with env or unknown keys whose path must change stops before any call (`CLIENT_ENTRY_NEEDS_HAND_EDIT`) with the commands to run by hand, because `add` cannot write those back. The entry is re-read before the first call. A call is judged by its exit code and, when that is not 0 (a failure, a timeout, a `claude` that cannot be run), by reading the file back, never by message text. Once `remove` has been attempted, one function (`settle()` in `_setup_claude_code`) is the only way out that is not success, whatever failed: `remove` itself, `add`, its output (decoded as UTF-8 with errors replaced, so undecodable bytes are text), the read-back, the restore or its read-back. It reads the file back and ends in one of three states: the file holds the new entry (success, with a note); the old entry is in place, as it was or restored with `claude mcp add`; or the client is `failed` and the report's `removed` and `by_hand` hold what the entry was and the command that puts it back. When the file cannot be read back, `claude` is not run again, because it replaces a file it cannot read. Not covered: `KeyboardInterrupt` (Ctrl-C) or a signal between `remove` and `add`. `tests/test_mcp_setup_reads_back.py::test_a_failure_at_any_step_after_remove_restores_or_reports_what_was_removed` injects a failure at each step in turn.

Each client is all or nothing on its own, and any exception is that client's report line (`INTERNAL_ERROR`), so one client stopping never hides what happened to another. Output is one JSON document by default (`--format rich` or `PYRITE_FORMAT=rich` for text; no `-f`): `new_entry` (what a new entry runs, its tier and tool count) and per client `status` (`created`, `unchanged`, `changed`, `stopped`, `failed`), the `tier` and `tools` its entry serves (a kept entry may serve another tier than `--tier`; `tier_named` is false when the entry names none and the server's default, `write`, applies), `changes`, `kept`, `discarded`, `stale`, `notes` and, when stopped, `error_code`, `error`, `suggestion`. Exit 1 unless every client ended up pointing at this install; 2 for an unknown `--client` or options that contradict. Tests: `tests/test_mcp_setup_reads_back.py` (hand-written fixtures, byte comparisons, a stub `claude` that can fail, hang and vanish, and one test against the real `claude` in a temp `HOME`).

## Related

- [[rest-api]] — HTTP equivalent of MCP tools
- [[kb-service]] — business logic layer used by all tool handlers
- [[storage-layer]] — database access
- [[schema-validation]] — `kb_schema` tool uses `KBSchema.to_agent_schema()`
- [ADR-0006: MCP Three-Tier Tool Model](../adrs/0006-mcp-three-tier-tool-model.md) — defines the read/write/admin tier architecture
