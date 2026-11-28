---
id: agent-shared-memory
type: design_doc
title: "Market Positioning: Agent Shared Memory"
status: active
author: markr
date: "2026-02-23"
tags: [positioning, agents, mcp, shared-memory]
lifecycle: archived
---

# Market Positioning: Agent Shared Memory

**Priority: 1 — Ship now**

## Market Overview

AI agents (Claude, GPT, custom LLM workflows) need persistent, structured memory that survives across sessions. Today's solutions are primitive:

- **Vector stores** (Pinecone, Chroma, Weaviate) — lose structure; retrieval is approximate; no typed relationships
- **Plain text files** (CLAUDE.md, .cursorrules) — no querying, no relationships, no versioning, no access control
- **JSON blobs** (LangGraph state) — no typing, no search, no temporal reasoning
- **Nothing** (CrewAI, AutoGen) — agents start fresh every session

The problem is acute: every team building agent workflows eventually hits "how does my agent remember what it learned last week?" No good answer exists.

## Competitive Landscape

| Competitor | Approach | Why It Falls Short |
|-----------|----------|-------------------|
| **LangGraph** | JSON state persistence | No typing, no relationships, no full-text search, no temporal queries |
| **Mem0** | "Memory layer for AI agents" | Vector-only retrieval; no structured types; no relationship graph; cloud-dependent |
| **Zep** | Long-term memory for assistants | Session-focused; entity extraction is basic; no user-defined types; SaaS |
| **LangChain Memory** | Conversation buffer / summary | Conversation-scoped, not knowledge-scoped; no structure beyond key-value |
| **Obsidian + MCP** | Community MCP servers for Obsidian vaults | Read-only or basic CRUD; no permission tiers; no typed schemas; no temporal queries |
| **Custom Neo4j** | Build your own graph + MCP | Requires developer; no out-of-box agent integration; no permission model |

**Key gap:** No product offers typed, queryable, permissioned structured memory that agents can read and write through a standard protocol.

## Pyrite Differentiation

**Three-tier MCP access control** — The only knowledge system that gates agent access by permission level:
- **Read tier**: Untrusted agents can search and retrieve but not modify (safe for public-facing assistants)
- **Write tier**: Trusted agents create entries, add relationships, update knowledge (research workflows)
- **Admin tier**: Full KB management (reserved for human-supervised operations)

This solves the "I want my agent to use the KB but I'm scared it'll corrupt it" problem that blocks adoption.

**Typed knowledge, not vector soup** — Agents write structured entries (person, event, document, custom types) with typed fields that validate on save. When an agent retrieves knowledge, it gets structured data it can reason over — not a similarity-ranked list of text chunks.

**Multi-KB for multi-agent** — Different agents can own different knowledge bases. A research agent writes to `research-kb`, a coding agent writes to `software-kb`, a summarization agent reads from both. Cross-KB links connect them.

**Git-native audit trail** — Every agent write is tracked in git. You can diff what an agent added, revert bad entries, review changes before merging. No other agent memory system offers this.

**Temporal reasoning** — Agents can query "what did we know about X as of date Y?" — critical for research workflows where knowledge evolves.

**MCP prompts and resources** — Pre-built prompt templates (`research_topic`, `summarize_entry`, `find_connections`, `daily_briefing`) that agents can invoke without custom prompting. Browsable resources via `pyrite://` URIs let MCP clients discover and navigate KB content. No competitor offers this level of MCP integration.

**Self-documenting types for agents** — Every type (core and custom) carries AI instructions, field descriptions, and display hints through a 4-layer metadata resolution system. When an agent calls `kb_schema`, it gets not just field names but guidance on how to use each type — "Events require a date. Set importance 1-10 based on significance." Agents write better entries with less prompt engineering.

**Content negotiation** — API endpoints support `Accept` header negotiation (JSON, Markdown, CSV, YAML). Agents can request the format that best fits their workflow. CLI supports `--format` flag for the same flexibility.

## Ideal Customer Profile

1. **AI engineering teams** building multi-agent systems who need shared persistent context
2. **Claude Code / Cursor power users** who want their coding assistant to remember project architecture decisions across sessions
3. **Research automation teams** building RAG pipelines who've outgrown vector-only retrieval
4. **Enterprise AI teams** who need auditability on what agents know and who changed it

## Go-to-Market

**Immediate (this quarter):**
- Publish Pyrite MCP server to the MCP community registry
- Write tutorial: "Give Claude Code a persistent, structured memory"
- Write tutorial: "Shared memory for multi-agent workflows with Pyrite"
- Target Claude Code plugin ecosystem (CLAUDE.md already exists; natural upgrade path)

**Next quarter:**
- LangChain/LangGraph integration (Pyrite as a Memory backend)
- CrewAI integration (Pyrite as shared agent knowledge)
- Benchmarks: structured retrieval accuracy vs. vector-only retrieval

**Later:**
- Hosted offering for teams who don't want to self-host
- Dashboard for monitoring agent knowledge changes

## What's Already Built

| Capability | Status | Key Files |
|-----------|--------|-----------|
| Three-tier MCP server (read/write/admin) | Shipped | `pyrite/server/mcp_server.py` |
| 8 core typed entries with field validation | Shipped | `pyrite/schema.py`, `pyrite/models/core_types.py` |
| Multi-KB with cross-KB links | Shipped | `pyrite/config.py`, `pyrite/services/kb_service.py` |
| Full-text + semantic + hybrid search | Shipped | `pyrite/services/search_service.py` |
| MCP prompts (research, summarize, connections, briefing) | Shipped | `pyrite/server/mcp_server.py` |
| MCP resources (`pyrite://` URIs) | Shipped | `pyrite/server/mcp_server.py` |
| Type metadata with AI instructions | Shipped | `pyrite/schema.py` (CORE_TYPE_METADATA, resolve_type_metadata) |
| Content negotiation (JSON, Markdown, CSV, YAML) | Shipped | `pyrite/formats/` |
| Plugin protocol (12 extension points) | Shipped | `pyrite/plugins/protocol.py` |
| Git-native storage (markdown + YAML frontmatter) | Shipped | `pyrite/storage/repository.py` |
| Service layer with lifecycle hooks | Shipped | `pyrite/services/kb_service.py` |
| Templates system | Shipped | `pyrite/services/template_service.py` |
| 583 tests passing | Shipped | `tests/` |

## Feature Gaps

| Gap | Effort | Impact |
|-----|--------|--------|
| Standalone MCP packaging (`pip install pyrite-mcp`) | M | **Critical** — #1 go-to-market blocker |
| Webhook/event notifications on KB changes | M | Medium — enables reactive agent workflows |
| Conflict resolution for concurrent agent writes | M | High at scale — needed for multi-agent |
| Hosted/cloud deployment option | L | Opens non-technical teams |

## Risks

- **Mem0 and Zep are raising VC** and iterating fast on "memory for agents" — they could add structure
- **MCP ecosystem is young** — if MCP doesn't become the standard protocol, the distribution channel narrows
- **Developers may prefer building custom** — the "just use SQLite" instinct is strong
- **Vector search is "good enough"** for many use cases — structured memory is a harder sell until agents get more sophisticated
- **Single-maintainer project** — prospects evaluating open-source tools care about sustainability; mitigated by clear architecture docs (11 ADRs), comprehensive plugin guide, 583 tests, and extensible plugin protocol
