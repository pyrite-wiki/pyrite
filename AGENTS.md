# AGENTS.md

Entry point for AI agents working in this repository — not just
Claude Code, any agent framework that reads `AGENTS.md`.

## Start here

The [documentation index](docs/README.md) groups guides and contracts by reader and task.

1. Read `CLAUDE.md` — the full development guide (git workflow,
   testing, KB usage, pre-commit hooks). It applies regardless of
   which agent is reading it.
2. Run `pyrite orient -k <kb-name>` before working in any knowledge
   base — it returns entry types, tags, recent changes, schema, and
   the operational contracts (indexing, error shape, search quoting,
   task-claim semantics) in one call. Don't relearn these from trial
   and error. Caveat: for a plugin-declared type (e.g. this repo's own
   `backlog_item`, `adr`, `component`, `standard`), the schema block is
   rubric prose with no field list — `pyrite kb schema show <kb>` and
   `pyrite schema diff` don't fill the gap either (both verified to
   return the same prose, no fields); today the required fields exist
   only in that type's MCP create-tool schema (e.g. `sw_create_backlog_item`
   requires `title`, `kind`, `kb_name`) — see #232.
3. If integrating over MCP: connect with `pyrite mcp --tier <tier>`
   and call the `kb_orient` tool first for the same reason.

## JSON contracts

See `docs/json-contracts.md` for the canonical error shape, success
envelopes, and body-truncation fields returned by the CLI (`--format
json`), MCP tools, and REST API.

## Everything else

`CLAUDE.md` is the source of truth for architecture, testing
commands, git workflow, and KB conventions. This file exists only so
agents that specifically look for `AGENTS.md` find their way there.
