---
id: mcp-search-add-fields-parameter-for-token-efficient-results
title: 'Search: add fields parameter for token-efficient results (CLI + MCP + REST)'
type: backlog_item
tags:
- mcp
- search
- agents
- dx
kind: enhancement
status: done
priority: high
effort: m
milestone: "0.13"
---

## Problem

When an agent searches a KB, `kb_search` returns full entry bodies. A single search returning 10 results can consume significant context window tokens when entries are 2,000+ words each. Agents often only need a paragraph from each result to decide what to read in full.

This affects all agent surfaces: Claude Desktop, Claude Code, Codex, Gemini CLI, and any other MCP client, as well as agents using the CLI with `--format json`.

## Proposal

Add an optional `fields` parameter to search across all three interfaces:

**CLI:**
```bash
pyrite search "topic" -k mydb --fields title,snippet,metadata
pyrite search "topic" -k mydb --body-limit 200
```

**MCP tool (`kb_search`):**
```
fields: ["title", "snippet", "metadata"]  — lightweight index scan
fields: ["title", "body"]  — full content (current default)
body_limit: 200  — return first N chars of body
```

**REST API (`/api/kbs/{name}/search`):**
Same parameters as query params or JSON body fields.

All three surfaces should behave identically — same parameter names, same default behavior (full body for backward compatibility), same truncation logic.

## Impact

Directly reduces token cost and context window pressure for agent research workflows. Especially important for agents operating under cost constraints or with smaller context windows.
