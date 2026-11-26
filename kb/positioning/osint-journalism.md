---
id: osint-journalism
type: design_doc
title: "Market Positioning: OSINT & Investigative Journalism"
status: active
author: markr
date: "2026-02-23"
tags: [positioning, journalism, osint, investigation, research]
lifecycle: archived
---

# Market Positioning: OSINT & Investigative Journalism

**Priority: 4 — Proven by use**

## Market Overview

Investigative journalists and OSINT researchers build complex webs of knowledge: people connected to organizations, events unfolding over decades, documents corroborating or contradicting claims, financial flows between entities, and timelines where sequence matters as much as content.

The tooling is fragmented. A typical investigation uses 5-8 tools: Aleph for document search, Maltego for link analysis, a spreadsheet for timelines, Google Docs for drafts, Signal for source communication, Hunchly for evidence capture, and a personal wiki for notes. Nothing connects them.

The market is also under pressure: OCCRP's Aleph is going paid (Aleph Pro, October 2025), Google Pinpoint commoditizes document search, and newsrooms face budget constraints that rule out enterprise tools.

## Competitive Landscape

| Competitor | Approach | Why It Falls Short |
|-----------|----------|-------------------|
| **Aleph (OCCRP)** | 400M+ document/entity search across 200 datasets | Going paid (Aleph Pro); focused on financial/corporate data; not a personal KB; no temporal reasoning |
| **OpenAleph** | Open-source Aleph fork, community-governed | Uncertain development resources; inherits Aleph's limitations; no personal KB layer |
| **Datashare + Neo4j (ICIJ)** | Document analysis + knowledge graph plugin | Closest competitor; requires technical skill; Neo4j setup is heavy; collaboration is ICIJ-centric |
| **Google Pinpoint** | Free AI-powered document search for journalists | Not a knowledge base; no relationships; no timeline; Google dependency; documents in, answers out |
| **Maltego** | OSINT link analysis with "transforms" | Expensive ($999/yr+); visualization-focused; no temporal dimension; no markdown notes; no version control |
| **Hunchly** | Forensic web capture with tamper-proof hashing | Capture only — no analysis, no relationships, no KB; now Maltego-owned |
| **DocumentCloud** | Document management, OCR, annotation | Document archive, not knowledge management; no entity relationships; no temporal queries |
| **i2 Analyst's Notebook** | Intelligence link analysis (IBM) | Enterprise pricing ($8K+/yr); government-focused; aging; no AI integration; no open data model |
| **Obsidian** | Personal markdown vault with graph view | Popular with researchers but no typed entries, no temporal queries, no collaboration, no AI agent access |

**Key gap:** No open-source tool combines document-backed research, entity/relationship management, temporal querying, and AI agent integration in a single platform accessible to individual journalists.

## Pyrite Differentiation

**The CascadeSeries proves it works** — Pyrite was built for and battle-tested on a real investigative project: 4,240+ timeline events (1619-2026), 323 knowledge base articles, 74 published articles, and two book manuscripts. This isn't a prototype — it's production infrastructure for investigative journalism.

**Temporal knowledge graph, productized** — No competitor handles "what did we know about X as of date Y?" or "show me how the relationship between A and B evolved from 2015-2025." Timeline events with importance ratings, date-range filtering, participant tracking, and causal links are core to the data model.

**Source provenance as first-class data** — Every entry tracks sources with confidence scores (confirmed/likely/possible/disputed), verification dates, and archived URLs. This matters for journalism where credibility depends on traceability.

**AI-assisted research workflows** — Through MCP, an AI agent can search the KB, pull timeline events, identify gaps, suggest connections, and draft research notes — all with permissioned access. The read tier means a public-facing chatbot can answer questions about published research without risking the underlying KB.

**Git-native for source protection** — Knowledge lives in local git repos. No cloud dependency. No third-party access to unpublished research. Sources stay protected. Collaboration happens through git, which journalists already use for data projects.

**Content negotiation for publishing workflows** — Export search results as CSV for data journalism, timeline events as Markdown for draft articles, or structured YAML for data processing pipelines. API and CLI both support format selection.

## What's Already Built

| Capability | Status |
|-----------|--------|
| Timeline events with date, importance, participants, status | Shipped |
| Person/organization entries with relationships | Shipped |
| Source provenance with confidence scores | Shipped |
| Full-text + semantic + hybrid search | Shipped |
| Three-tier MCP server | Shipped |
| MCP prompts (research_topic, find_connections, daily_briefing) | Shipped |
| Type metadata with AI instructions for all core types | Shipped |
| Content negotiation (JSON, Markdown, CSV, YAML) | Shipped |
| Slash commands in editor (callouts, tables, wikilinks, etc.) | Shipped |
| Wikilink autocomplete + backlinks panel | Shipped |
| Daily notes with calendar | Shipped |
| Battle-tested on CascadeSeries (4,240+ events, 323 articles) | Shipped |

## Ideal Customer Profile

1. **Independent investigative journalists** and small investigative outlets (ProPublica, The Intercept, OCCRP members)
2. **OSINT researchers** tracking networks of entities across time
3. **Citizen journalists** and activist researchers documenting systematic patterns
4. **Academic researchers** in political science, history, criminology working with large event datasets
5. **Legal investigators** building evidence timelines for litigation

## Go-to-Market

**Immediate:**
- Publish CascadeSeries as a case study: "How Pyrite powers a 4,000-event investigative knowledge base"
- Write for GIJN (Global Investigative Journalism Network): "Open-source tools for temporal investigation"
- Target the NICAR (Investigative Reporters & Editors) community — data journalists who already use code

**Next quarter:**
- "Investigation starter kit" — pre-built kb.yaml with types for people, organizations, events, documents, financial flows
- Import tools for common formats: Aleph entity exports, Maltego graphs, CSV timelines
- Partnership with one newsroom or investigative nonprofit for co-development

**Later:**
- Integration with DocumentCloud (import annotated documents as Pyrite entries)
- Integration with Hunchly/Maltego (import captured evidence)
- "Pyrite for newsrooms" guide: multi-user setup, shared KB, editorial workflow

## Feature Gaps

| Gap | Effort | Impact |
|-----|--------|--------|
| Investigation starter kit (kb.yaml + templates) | S | High — immediate value for new users |
| Aleph/Maltego/CSV import tools | M | High — migration path from existing workflows |
| Financial flow tracking (follow-the-money queries) | M | High — core investigative use case |
| Evidence attachment / document linking | M | Medium — connect documents to KB entries |
| Collaborative investigation workspace (shared KB with roles) | L | High — needed for newsroom adoption |

## Risks

- **Journalists are not developers** — CLI-first approach limits adoption; web UI needs to be solid before newsroom pitch
- **Aleph Pro may succeed** — if OCCRP delivers a good paid product, it absorbs the institutional market
- **Google Pinpoint is free** — commoditizes the document search layer; Pyrite must differentiate on structure and relationships
- **Security concerns** — journalists working with sensitive sources need threat modeling; any vulnerability is catastrophic
- **Small market** — investigative journalism is a small, underfunded market; can't sustain a product alone (hence horizontal positioning)
- **Single-maintainer project** — NGOs and newsrooms evaluate project health before depending on tools; mitigated by open-source model, comprehensive test suite, and the CascadeSeries itself as proof of sustained development
