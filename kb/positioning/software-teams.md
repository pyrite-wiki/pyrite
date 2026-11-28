---
id: software-teams
type: design_doc
title: "Market Positioning: Software Development Teams"
status: active
author: markr
date: "2026-02-23"
tags: [positioning, software, developer-tools, devex]
lifecycle: archived
---

# Market Positioning: Software Development Teams

**Priority: 2 — Natural early adopters**

## Market Overview

Software teams maintain architectural knowledge across dozens of tools and formats: ADRs in markdown, runbooks in Confluence, component docs in Notion, incident timelines in Jira, system diagrams in Miro, tribal knowledge in Slack threads. None of these are connected. When a new engineer asks "why did we choose Kafka over RabbitMQ?" the answer lives in a 2-year-old Slack thread nobody can find.

The problem compounds with AI assistants: Claude Code and Cursor read CLAUDE.md flat files for project context, but these files can't express relationships ("this service depends on that service"), temporal evolution ("we migrated from Postgres to CockroachDB in Q3 2025"), or typed structure ("this is a component with these dependencies").

## Competitive Landscape

| Competitor | Approach | Why It Falls Short |
|-----------|----------|-------------------|
| **Confluence** | Wiki pages with tree navigation | No typed entries; no relationships; search is notoriously bad; pages become graveyards |
| **Notion** | Flexible databases + docs | No git integration; no CLI; vendor lock-in; databases are flat (no relationship graph) |
| **Backstage (Spotify)** | Service catalog with plugins | Catalog only — describes services, not decisions or history; complex to self-host; YAML soup |
| **ADR tools** (adr-tools, log4brains) | Flat markdown files in repo | No search; no relationships; no temporal queries; just numbered files |
| **Obsidian** | Personal vault with plugins | No team collaboration; no typed schemas; requires each dev to maintain their own vault |
| **GitBook / ReadTheDocs** | Published documentation sites | Documentation, not knowledge management; no structured data; no agent integration |

**Key gap:** No tool lets a software team maintain typed, relationship-rich, temporally queryable knowledge in git — searchable by both humans and AI agents.

## Pyrite Differentiation

**Software-KB extension exists as a proof-of-concept** — The `extensions/software-kb/` plugin defines `ComponentEntry` for documenting services, libraries, and modules with typed fields (path, owner, dependencies, API surface). It's functional but early — a starting point for teams to customize, not a production-ready solution.

**Git-native fits developer workflows** — Knowledge lives in the same repo as code. PRs include KB updates. CI can validate KB entries. No context-switching to a separate wiki tool.

**ADRs as first-class entries** — Architecture Decision Records with `adr_number`, `status` (proposed/accepted/deprecated/superseded), date, and typed links to components they affect. `pyrite sw adrs` lists them; `pyrite search "caching" -k project` finds relevant decisions.

**Agent-queryable architecture** — A coding agent can ask the MCP server "what components depend on the auth service?" or "what ADRs were made about database choices?" and get structured answers — not keyword-matched wiki pages.

**Temporal queries for system evolution** — "What was our infrastructure in Q1 2025?" is answerable because events and decisions are timestamped entries with relationships.

**Slash commands in the editor** — Type `/` to insert headings, code blocks, tables, callouts, wikilinks, dates, and task lists. Reduces markdown friction for less technical team members.

**Content negotiation for CI pipelines** — API endpoints support `Accept: text/csv` for exporting component lists to dashboards, `text/markdown` for generating docs, and `text/yaml` for configuration management tools. CLI `--format` flag for scripting.

## What's Already Built

| Capability | Status |
|-----------|--------|
| Software-KB extension with ComponentEntry | Shipped (PoC) |
| ADRs as first-class typed entries | Shipped |
| Backlog items as typed entries | Shipped |
| MCP server with 3-tier access | Shipped |
| MCP prompts (research, summarize, connections, briefing) | Shipped |
| Slash commands in editor (14 commands) | Shipped |
| Content negotiation (JSON, Markdown, CSV, YAML) | Shipped |
| Type metadata with AI instructions | Shipped |
| Templates system | Shipped |
| Web UI (entries, search, backlinks, daily notes, starred) | Shipped |

## Ideal Customer Profile

1. **Platform engineering teams** (10-50 engineers) maintaining microservices architectures
2. **Startups using Claude Code / Cursor** who want better AI context than CLAUDE.md
3. **Open-source maintainers** who need contributor-accessible architecture docs
4. **DevRel teams** documenting public APIs and SDKs with structured relationships

## Go-to-Market

**Immediate:**
- Position as "CLAUDE.md, but structured" — upgrade path for Claude Code users
- Publish software-kb extension tutorial: "Document your architecture so AI understands it"
- Target developer communities: Hacker News, Dev.to, relevant Discord/Slack communities

**Next quarter:**
- GitHub Action: validate KB entries on PR, auto-index on merge
- VS Code / Cursor extension: browse KB entries in sidebar
- Template repositories: "Start a new project with Pyrite KB"

**Later:**
- Backstage plugin: sync Pyrite component entries to Backstage catalog
- Incident timeline integration: post-mortem events linked to affected components
- Team onboarding automation: "Here's everything you need to know about service X"

## Feature Gaps

| Gap | Effort | Impact |
|-----|--------|--------|
| `pyrite init --template software` scaffolding | S | High — reduces time-to-value for new teams |
| GitHub Action for CI integration | S | High — fits existing workflows |
| Dependency graph visualization (component → component) | M | High — visual "what depends on what" |
| Import from Confluence / Notion (migration path) | M | Medium — removes switching cost |
| VS Code extension for KB browsing | L | Medium — nice-to-have, CLI is primary |

## Risks

- **Backstage has momentum** in the service catalog space — teams may see Pyrite as redundant if they already use Backstage
- **Confluence/Notion inertia** — switching costs are high; teams won't migrate unless pain is acute
- **"Just use markdown files"** — many teams feel their existing ADR/docs setup is good enough
- **AI context window growth** — if context windows get large enough, flat files might suffice for agent context (but structured querying still wins for large codebases)
- **Single-maintainer project** — developer teams assess open-source sustainability; mitigated by 583 tests, 11 ADRs, plugin protocol, and dog-fooding (Pyrite's own backlog and architecture are managed in Pyrite)
