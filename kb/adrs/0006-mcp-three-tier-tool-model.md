---
id: adr-0006
type: adr
title: "MCP Three-Tier Tool Model"
adr_number: 6
status: accepted
deciders: ["markr"]
date: "2025-08-01"
tags: [architecture, mcp, ai-agents]
---

> **Amended by [[adr-0043]] (2026-10-03).** The mechanism stands. The tier
> contents in Consequences are stale: the server builds 72, 103 and 112 tools
> (read, write, admin) per the audit, not "4 software-kb read tools and 2 write
> tools". Tiers predate users and grants: over HTTP the tool list follows what a
> principal may do on any KB (ADR-0043), and each call is decided per KB
> ([[adr-0037]]). Evidence: `kb/designs/adr-audit-2026-10.md`.

## Context

AI agents connecting via MCP need different permission levels. A research agent should search but not delete. A drafting agent should create but not manage KBs.

## Decision

Three tiers of MCP tools:
- **Read**: list, search, get, timeline, backlinks, tags, stats, schema
- **Write**: create, update, delete (plus all read tools)
- **Admin**: index sync, KB manage (plus all write tools)

Plugins register tools at specific tiers. The server is started at a chosen tier.

## Consequences

- Safe to give read-tier access to any agent
- Plugin MCP tools merge into the same tier system
- Currently 4 software-kb read tools + 2 write tools registered via plugin
- Server defaults to write tier for Claude Code integration
