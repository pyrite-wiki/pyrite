---
id: domain-specific-kb
type: design_doc
title: "Market Positioning: Domain-Specific KB Creation"
status: active
author: markr
date: "2026-02-23"
tags: [positioning, platform, domains, plugins, vertical]
lifecycle: archived
---

# Market Positioning: Domain-Specific KB Creation

**Priority: 3 — Build partnerships**

## Market Overview

Every specialized field needs structured knowledge management, and every field builds bespoke tools or forces their data into generic systems that don't fit:

- **Legal teams** track cases, statutes, precedents, and relationships between them — in SharePoint folders
- **Medical researchers** manage patient cohorts, trial data, literature connections — in Excel spreadsheets
- **Policy analysts** track legislation, stakeholders, timelines, regulatory changes — in Google Docs
- **Patent analysts** map technology landscapes, prior art, inventor networks — in proprietary databases
- **Compliance teams** maintain regulatory requirements, audit findings, control mappings — in GRC tools that cost six figures

The common pattern: domain experts know what types of knowledge they need (cases, trials, regulations, patents) and how those types relate to each other — but they can't express this without hiring developers to build custom tools.

## Competitive Landscape

| Competitor | Approach | Why It Falls Short |
|-----------|----------|-------------------|
| **Notion databases** | User-defined properties, relations, rollups | No temporal queries; no full-text search across relations; flat (not graph); vendor lock-in |
| **Airtable** | Spreadsheet-database hybrid with linked records | Not a knowledge base; no markdown content; no version history; expensive at scale |
| **Obsidian + Dataview** | User-defined frontmatter + query plugin | Powerful but requires learning Dataview query syntax; single-user; no typed validation |
| **Semantic MediaWiki** | Wiki + structured semantic properties + SPARQL | Mature but aging; MediaWiki dependency; steep setup; limited visualization |
| **Custom Neo4j builds** | Graph database + domain ontology | Requires developer; no out-of-box UI; no markdown content model; no collaboration |
| **Vertical SaaS** (Relativity, Veeva, etc.) | Domain-specific platforms | Expensive ($50K-500K/yr); vendor lock-in; can't customize types; no AI agent access |

**Key gap:** No platform lets domain experts define their own typed knowledge structures without writing code, while still getting full-text search, relationship graphs, temporal queries, and AI agent integration.

## Pyrite Differentiation

**YAML-defined types without code** — A legal researcher defines their domain in `kb.yaml`:

```yaml
types:
  case:
    description: "Legal case or proceeding"
    fields:
      jurisdiction:
        type: select
        options: [federal, state, international]
      status:
        type: select
        options: [active, decided, appealed, settled]
      filing_date:
        type: date
      parties:
        type: list
        items:
          type: text
```

No Python. No developer. Auto-validation and UI generation from this schema.

**Plugin protocol for deeper customization** — When YAML isn't enough, a developer can write a plugin that adds custom entry types, relationship semantics, workflows (e.g., peer review state machine), and domain-specific MCP tools — all while maintaining the same markdown-in-git data model.

**Multi-KB for multi-domain** — A policy organization can maintain separate KBs for legislation, stakeholders, and news events — each with its own types — and query across all of them. The cross-KB link model is unique.

**Agent-powered domain workflows** — An AI agent can be given read access to a legal KB and write access to a research-notes KB, with domain-specific MCP tools: "Find all cases citing *Chevron* since 2024" or "Create a timeline of regulatory actions affecting Company X."

**Content negotiation for diverse consumers** — API responses in JSON, Markdown, CSV, or YAML via `Accept` header. Domain teams can export search results as CSV for spreadsheet workflows or get Markdown for documentation.

## What's Already Built

| Capability | Status |
|-----------|--------|
| YAML-defined types with 10 field types (text, number, date, select, multi-select, etc.) | Shipped |
| Field-level validation (range, format, required, options) | Shipped |
| Plugin protocol with 12 extension points | Shipped |
| 3 proof-of-concept extensions (zettelkasten, social, software-kb) | Shipped |
| Type metadata with AI instructions per type | Shipped |
| Templates system for entry scaffolding | Shipped |
| Multi-KB with cross-KB links | Shipped |
| Content negotiation (JSON, Markdown, CSV, YAML) | Shipped |

## Ideal Customer Profile

1. **Research organizations** (think tanks, policy institutes, NGOs) with structured knowledge needs
2. **Specialized consulting firms** (legal research, patent analysis, compliance) who build internal tools
3. **Academic research groups** managing multi-year longitudinal studies
4. **Government analysts** tracking regulatory landscapes, stakeholder networks, policy timelines

## Go-to-Market

**Immediate:**
- Publish 2-3 domain extension examples beyond the existing four (legal-kb, policy-kb, medical-research-kb)
- Write guide: "Build a custom knowledge base for your domain in 30 minutes"
- Identify 2-3 partner organizations willing to co-develop domain extensions

**Next quarter:**
- Extension marketplace / registry (even if just a GitHub topic tag initially)
- "Domain starter kits" — pre-built kb.yaml + sample entries for common verticals
- Community contribution workflow: fork an extension, customize, publish

**Later:**
- No-code extension builder UI (backlog item already exists)
- Hosted offering for non-technical domain teams
- Certification/partnership program for consulting firms building on Pyrite

## Feature Gaps

| Gap | Effort | Impact |
|-----|--------|--------|
| Domain starter kit templates (legal, policy, medical) | S each | High — proves platform versatility |
| Extension marketplace / registry | M | High — network effects |
| No-code extension builder UI | L | Transformative — opens non-developer market |
| Import/migration tools (CSV, JSON, existing databases) | M | High — adoption requires data migration |
| Role-based access per KB (not just per tier) | M | Medium — enterprise requirement |

## Risks

- **"Just use Notion"** — for simple domains, Notion databases are good enough and already adopted
- **Schema design is hard** — domain experts may need guidance; bad schemas create bad KBs
- **Chicken-and-egg** — platform value depends on extensions; extensions depend on platform adoption
- **Vertical SaaS incumbents** have deep domain features Pyrite can't match without significant plugin development
- **Maintenance burden** — each domain extension needs ongoing maintenance as the core evolves
- **Single-maintainer project** — platform credibility requires visible project health; mitigated by 583 tests, 11 ADRs, comprehensive plugin guide, and clear contribution path
