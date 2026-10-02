---
id: mcp-submission
title: "MCP Directory Submission Draft"
type: note
tags:
- launch
- mcp
---

> The text for submitting Pyrite to MCP server directories. Moved here from the
> repository root (#585); the launch checklists in [[launch-channels]] and
> [[launch-staging]] say to update it before submitting.

# MCP Server Submission: Pyrite Knowledge Infrastructure

## Repository Information

- **Repository**: https://github.com/pyrite-wiki/pyrite
- **License**: MIT
- **Language**: Python 3.11+
- **MCP Transport**: STDIO
- **Dependencies**: FastAPI, SQLAlchemy, Pydantic (AI providers optional)

## Project Summary

**Pyrite** is a knowledge infrastructure platform that gives AI agents structured, permissioned access to typed knowledge bases through MCP. It's the first MCP server with tiered access control — agents get read, write, or admin tools depending on trust level.

Knowledge lives as markdown files in git repositories with YAML frontmatter. Pyrite indexes them into SQLite FTS5 for fast search, validates entries against typed schemas, and exposes everything through MCP tools, a REST API, and CLI.

## What Makes This Different

**Three-tier permission model with rate limiting** — Most MCP knowledge servers expose flat read/write access. Pyrite separates tools into read (safe for untrusted agents), write (for trusted workflows), and admin (human-supervised). Per-client, per-tier rate limiting prevents runaway agents from hammering the server. This solves the "I want my agent to use the KB but not corrupt it" problem.

**Typed entries, not text chunks** — Agents write structured entries (person, organization, event, document, or custom types defined in YAML) with field-level validation. Retrieval returns structured data, not similarity-ranked text fragments.

**Multi-KB with cross-references** — Mount multiple knowledge bases with different schemas. A research agent writes to one KB while a coding agent writes to another. Cross-KB links connect them. Search spans all mounted KBs.

**Self-documenting types for agents** — Every type carries AI instructions, field descriptions, and display hints through a 4-layer metadata resolution system. When an agent calls `kb_schema`, it gets not just field names but guidance on how to use each type. Agents write better entries with less prompt engineering.

**MCP prompts and resources** — Pre-built prompt templates (`research_topic`, `summarize_entry`, `find_connections`, `daily_briefing`) that agents can invoke. Browsable `pyrite://` URI resources for KB metadata discovery.

**Content negotiation** — Responses in JSON, Markdown, CSV, or YAML via format parameter. Agents request the format that fits their workflow.

## MCP Server Details

### Installation

```bash
pip install pyrite
```

Or from source:

```bash
git clone https://github.com/pyrite-wiki/pyrite
cd pyrite
pip install -e ".[all]"
```

### Configuration

```json
{
  "mcpServers": {
    "pyrite": {
      "command": "pyrite",
      "args": ["mcp", "--tier", "write"]
    }
  }
}
```

Set `--tier read` for untrusted agents, `--tier write` for trusted research workflows, `--tier admin` for human-supervised KB management. Use `pyrite-admin mcp` for admin-tier access.

### MCP Tools by Tier

#### Read Tier (14 tools — safe for any agent)

| Tool | Description |
|------|-------------|
| `kb_list` | List mounted knowledge bases with stats |
| `kb_search` | Full-text, semantic, or hybrid search with filters |
| `kb_get` | Retrieve entry by ID with metadata, links, sources |
| `kb_batch_read` | Fetch multiple entries in one call (up to 50) |
| `kb_list_entries` | Browse entries with type/tag/date filters for orientation |
| `kb_recent` | Get recently changed entries across KBs |
| `kb_orient` | One-shot KB summary: description, types, stats, recent entries |
| `kb_timeline` | Query events by date range and importance threshold |
| `kb_tags` | List all tags with frequency counts and hierarchy |
| `kb_backlinks` | Find all entries linking to a given entry |
| `kb_stats` | Index statistics (entry counts, type distribution) |
| `kb_schema` | Get type definitions with AI instructions and field descriptions |
| `kb_qa_validate` | Run structural QA validation on entries |
| `kb_qa_status` | Get QA status summary for a KB |

#### Write Tier (6 tools — trusted agents)

All read tools, plus:

| Tool | Description |
|------|-------------|
| `kb_create` | Create typed entry with auto-validation and indexing |
| `kb_bulk_create` | Batch create up to 50 entries with best-effort semantics |
| `kb_update` | Update entry fields, body, or metadata |
| `kb_delete` | Delete entry with cascade link cleanup |
| `kb_link` | Create typed relationship between entries |
| `kb_qa_assess` | Create QA assessment entry for an entry |

#### Admin Tier (4 tools — human-supervised)

All write tools, plus:

| Tool | Description |
|------|-------------|
| `kb_index_sync` | Rebuild search index from markdown files |
| `kb_manage` | KB lifecycle management (init, remove, schema operations) |
| `kb_commit` | Commit KB changes to git |
| `kb_push` | Push KB git repository to remote |

#### Plugin Tools

Extensions register additional MCP tools per tier. Up to 32 plugin tools at admin tier across 7 shipped extensions:

- **Software-KB**: `sw_adrs`, `sw_backlog`, `sw_component`, `sw_standards`, `sw_create_adr`, `sw_create_backlog_item`
- **Task Coordination**: `task_list`, `task_status`, `task_create`, `task_update`, `task_claim`, `task_checkpoint`, `task_decompose`
- **Zettelkasten**: `zettel_inbox`, `zettel_graph`
- **Encyclopedia**: `wiki_stubs`, `wiki_review_queue`, `wiki_quality_stats`, `wiki_assess_quality`, `wiki_submit_review`, `wiki_protect`
- **Social**: `social_top`, `social_newest`, `social_reputation`, `social_post`, `social_vote`
- **Cascade Series**: `cascade_timeline`, `cascade_actors`, `cascade_capture_lanes`, `cascade_network`
- **Solidarity**: `solidarity_timeline`, `solidarity_infrastructure_types`

### MCP Prompts

| Prompt | Purpose |
|--------|---------|
| `research_topic` | Guide agent through researching a topic across the KB |
| `summarize_entry` | Produce a structured summary of an entry and its connections |
| `find_connections` | Discover relationships between two entries |
| `daily_briefing` | Generate a briefing of recent KB activity |

### MCP Resources

Browsable `pyrite://` URIs expose KB metadata, entry lists, and type schemas for MCP clients that support resource discovery.

### Example Agent Interactions

```
"Search my research KB for everything about authentication architecture"
→ kb_search(query="authentication architecture", kb="software-kb", mode="hybrid")

"Create a new ADR about our decision to use Kafka"
→ kb_create(kb="software-kb", type="adr", title="Use Kafka for event streaming", ...)

"What events happened between 2020 and 2022 involving Company X?"
→ kb_timeline(date_from="2020-01-01", date_to="2022-12-31", query="Company X")

"Find connections between the auth-service component and the billing incident"
→ kb_backlinks(id="auth-service") + kb_get(id="billing-incident-2025")
```

## Core Entry Types

| Type | Key Fields | Use Case |
|------|-----------|----------|
| `note` | tags, links, sources | General knowledge |
| `person` | role, affiliations, importance | People tracking |
| `organization` | org_type, jurisdiction, founding_date | Institutional analysis |
| `event` | date, importance (1-10), participants, status | Timeline events |
| `document` | author, document_type, url, date | Reference materials |
| `topic` | — | Hub entries for organizing related knowledge |
| `relationship` | source, target, relation_type, properties | Reified connections |
| `timeline` | events (ordered) | Event sequences |

Custom types defined in `kb.yaml` without code — 10 field types with validation.

## Technical Details

- **Storage**: Markdown files in git (source of truth) + SQLite FTS5 index (queryable)
- **Search**: Full-text (BM25), semantic (sentence-transformers + sqlite-vec), hybrid
- **Validation**: Pydantic models + YAML-defined field schemas with 4-layer resolution
- **Extensions**: Plugin protocol with 15 extension points (entry types, MCP tools, hooks, collection types, DB tables, relationship types, validators, KB presets, field schemas, type metadata, CLI commands, search)
- **APIs**: REST (FastAPI with OpenAPI), MCP (STDIO), CLI (Typer)
- **Frontend**: SvelteKit 2 + Svelte 5 + Tailwind (entries, search, backlinks, daily notes, graph, templates, slash commands, collections, AI chat)
- **Rate Limiting**: In-memory token bucket rate limiting on the MCP server, per-client per-tier, with configurable exemptions for local stdio connections
- **Tests**: 1550+ passing, covering CRUD, FTS5, REST API, MCP protocol, plugins, migrations, schema validation, collections, QA, task coordination, rate limiting

## Submission Checklist

- [x] MCP server with STDIO transport and three permission tiers
- [x] 14 read tools, 6 write tools, 4 admin tools + 32 plugin tools across 7 extensions
- [x] Per-client per-tier rate limiting on the MCP server
- [x] 4 built-in MCP prompts for common research workflows
- [x] MCP resources via `pyrite://` URIs
- [x] 1550+ tests including MCP protocol tests
- [x] MIT license
- [x] Comprehensive documentation (README, 16 ADRs, plugin developer guide)
- [x] Active development with detailed changelog

## Value Proposition

Pyrite is the first MCP server designed as **structured shared memory for AI agents**. Unlike vector stores that lose structure or flat files that can't be queried, Pyrite gives agents typed, validated, relationship-rich knowledge with permission-controlled access — all backed by git for auditability.

It's battle-tested on a real investigative journalism project (4,240+ timeline events, 323 knowledge base articles) and designed to generalize across domains: software architecture, legal research, policy analysis, or any field where knowledge has structure.
