---
id: mcp-tool-kb-list-entries-for-lightweight-kb-index-browsing
title: 'New command: list-entries for lightweight KB index browsing (CLI + MCP + REST)'
type: backlog_item
tags:
- mcp
- agents
- dx
- feature
kind: feature
status: done
priority: high
effort: m
milestone: "0.13"
---

## Problem

There's no way for an agent to get a "table of contents" view of a KB. Current options are: search (requires knowing what to look for), browse tags (gives counts but not entry titles), or get individual entries by ID (requires knowing the ID). The `pyrite://kbs/{name}/entries` MCP resource exists but resources aren't well-supported across all MCP clients.

This affects all agent surfaces equally — Claude Desktop, Claude Code, Codex, Gemini CLI, and CLI-based agents all lack this orientation capability.

## Proposal

Add a `list-entries` command across all three interfaces:

**CLI:**
```bash
pyrite list-entries -k mydb --type backlog_item --tag agents --sort modified --limit 20
pyrite list-entries -k mydb --format json
```

**MCP tool (`kb_list_entries`):**
```
kb_list_entries(
  kb_name: str,
  type: Optional[str],
  tag: Optional[str],
  sort: "modified" | "created" | "title" = "modified",
  limit: int = 20,
  offset: int = 0
) -> [{id, title, type, date_modified, tags}]
```

**REST API:** `GET /api/kbs/{name}/entries?type=...&tag=...&sort=...&limit=...`

Returns lightweight entry metadata — enough to scan and decide what to read in full.

## Impact

Fills a gap between search (targeted) and full reads (expensive). Enables the common agent workflow: orient → scan → select → deep read.
