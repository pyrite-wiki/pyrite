---
id: mcp-tool-kb-batch-read-for-multi-entry-retrieval-in-one-call
title: 'New command: batch-read for multi-entry retrieval in one call (CLI + MCP + REST)'
type: backlog_item
tags:
- mcp
- agents
- dx
- feature
kind: feature
status: done
priority: high
effort: s
milestone: "0.13"
---

## Problem

When an agent identifies multiple entry IDs to read (from search results, backlinks, or link traversal), it must make sequential `kb_get` calls — one per entry. For a typical research workflow where an agent wants to synthesize 5-8 related entries, this means 5-8 round-trips.

This affects all agent surfaces: Claude Desktop, Claude Code, Codex, Gemini CLI, and CLI-based agents all suffer the same sequential round-trip penalty.

## Proposal

Add a `batch-read` command across all three interfaces:

**CLI:**
```bash
pyrite batch-read -k mydb entry-1 entry-2 entry-3
pyrite batch-read -k mydb entry-1 entry-2 --fields title,body --body-limit 200 --format json
```

**MCP tool (`kb_batch_read`):**
```
kb_batch_read(
  entry_ids: list[str],
  kb_name: Optional[str],
  fields: Optional[list[str]]
) -> [Entry]
```

**REST API:** `POST /api/kbs/{name}/entries/batch` with `{"ids": [...], "fields": [...]}`

Pairs well with the `fields` parameter from the search enhancement — an agent can batch-read 10 entries but only request titles and first 200 chars of body.

## Impact

Reduces round-trips for research workflows from O(n) to O(1). Significant latency improvement for agents doing synthesis across multiple entries.
