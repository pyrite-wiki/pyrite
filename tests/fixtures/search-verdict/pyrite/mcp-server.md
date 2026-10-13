---
id: mcp-server
title: MCP Server
type: note
tags:
- core
- mcp
- ai-agents
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

`kb_orient` is the first call of a session. With no `kb_name` it answers from `KBService.orient_overview` with the KBs the caller may read; its handler takes `readable_kbs`, which is also what lets a scoped caller past the dispatcher's fail-closed rule for a call that names no KB. With a `kb_name` it answers from `KBService.orient`; `detail="brief"` drops the write-side schema blocks (`ai_instructions`, `evaluation_rubric`, `guidelines`, `goals`, and the top-level `guidelines`) but keeps `relationship_types`, and blocks a plugin adds (`get_orient_supplement`) are returned whole.
