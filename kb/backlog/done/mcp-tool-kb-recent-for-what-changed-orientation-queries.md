---
id: mcp-tool-kb-recent-for-what-changed-orientation-queries
title: "New command: recent for 'what changed?' orientation queries (CLI + MCP + REST)"
type: backlog_item
tags:
- mcp
- agents
- dx
- feature
kind: feature
status: done
priority: medium
effort: m
milestone: "0.13"
---

## Problem

There's no way for an agent to ask "what changed recently?" across all entry types. The timeline tool is scoped to timeline_event entries specifically. An agent returning to a KB after time away (or connecting for the first time) has no re-orientation query to determine whether its cached understanding is current.

This affects all agent surfaces: Claude Desktop, Claude Code, Codex, Gemini CLI, and CLI-based agents all need this re-orientation capability.

## Proposal

Add a `recent` command across all three interfaces:

**CLI:**
```bash
pyrite recent -k mydb --since 2026-03-01
pyrite recent -k mydb --hours 48 --type backlog_item
pyrite recent -k mydb --hours 24 --format json
```

**MCP tool (`kb_recent`):**
```
kb_recent(
  kb_name: str,
  since: Optional[str],  # ISO date
  hours: Optional[int],  # alternative: last N hours
  type: Optional[str],
  limit: int = 20
) -> [{id, title, type, action: "created"|"modified", timestamp}]
```

**REST API:** `GET /api/kbs/{name}/recent?since=...&hours=...&type=...`

Returns entries created or modified within the specified window, sorted by recency. Lightweight metadata only.

## Implementation Note

This likely reads git log or file modification times rather than maintaining a separate change log. Git-native approach preferred — consistent with founding principles.

## Impact

Enables the re-orientation workflow: agent connects, checks what's changed, updates its understanding, then proceeds. Critical for agents that interact with a KB periodically rather than continuously.
