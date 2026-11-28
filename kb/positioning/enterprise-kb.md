---
id: enterprise-kb
type: design_doc
title: "Market Positioning: Enterprise Knowledge Management"
status: active
author: markr
date: "2026-02-23"
tags: [positioning, enterprise, knowledge-management, collaboration]
lifecycle: archived
---

# Market Positioning: Enterprise Knowledge Management

**Priority: 5 — Largest TAM, hardest entry**

## Market Overview

Enterprise knowledge management is a $1.1 trillion problem. Organizations lose an estimated 31.5 hours per employee per month searching for information. Knowledge silos cost Fortune 500 companies $31.5 billion annually in failed knowledge sharing.

The tools are either too simple (wikis that become graveyards) or too complex (enterprise platforms that require consultants to configure). The rise of AI assistants has made the problem more visible — companies want to point AI at their knowledge base, but their knowledge base is a mess of unstructured Confluence pages, Google Docs, and Slack threads with no typed structure, no relationships, and no temporal dimension.

## Competitive Landscape

| Competitor | Approach | Why It Falls Short |
|-----------|----------|-------------------|
| **Confluence** | Wiki with team spaces, page trees, macros | Knowledge graveyard; no typed entries; terrible search; no relationships; Atlassian lock-in |
| **Notion** | Flexible workspace with databases + AI | No git integration; vendor lock-in; databases are flat; AI is surface-level (Q&A, not structured reasoning) |
| **SharePoint** | Microsoft's enterprise content management | Complex; requires admin expertise; poor UX; no knowledge graph; search depends on Microsoft Graph |
| **Guru** | AI-powered knowledge cards with verification workflows | Good for support/sales knowledge; not designed for deep technical or research knowledge; expensive |
| **Tettra** | Wiki for internal knowledge with Slack integration | Simple and clean but shallow; no typed entries; no relationships; limited to SMBs |
| **Slite** | Team wiki with AI Q&A over docs | Clean UX but no structured data; no relationships; limited to document-style knowledge |
| **Palantir AIP** | Enterprise AI platform with ontology | Full-featured but $millions/year; requires dedicated implementation team; government-centric culture |

**Key gap:** No enterprise tool combines typed knowledge structures, relationship graphs, temporal queries, git-native versioning, and AI agent integration at a price point accessible to mid-market companies.

## Pyrite Differentiation

**Knowledge-as-code** — Knowledge entries are markdown files in git repositories with YAML frontmatter. This means:
- Version history on every entry (who changed what, when, and why)
- Pull request workflows for knowledge review
- Branch-based knowledge drafting
- CI/CD integration (validate KB entries on merge)
- No vendor lock-in — data is always portable

**Typed knowledge, not page soup** — Instead of wiki pages with arbitrary content, entries have types (process, decision, person, project, system) with typed fields that validate. Search returns structured results, not a ranked list of pages that might contain the answer.

**AI-agent-ready from day one** — The three-tier MCP server means companies can deploy AI assistants that query internal knowledge with appropriate permissions. A support bot gets read access; an internal research agent gets write access; infrastructure automation gets admin access. No other enterprise KMS has this architecture.

**Multi-KB for organizational structure** — Different teams own different knowledge bases with different types and policies, but search spans all of them. Engineering has typed component entries; HR has policy entries; Sales has account entries. Cross-KB links connect a customer account to the engineering components they use.

**Plugin protocol for customization without consultants** — Domain-specific types defined in YAML. Workflows defined in plugins. No six-month implementation project. No professional services dependency.

**Content negotiation for diverse consumers** — Same API serves JSON to web frontends, Markdown to documentation generators, CSV to dashboards, and YAML to configuration management. No separate export pipeline needed.

## What's Already Built

| Capability | Status |
|-----------|--------|
| Typed entries with 10 field types and validation | Shipped |
| Multi-KB with cross-KB links | Shipped |
| Three-tier MCP server for AI agents | Shipped |
| Plugin protocol (12 extension points) | Shipped |
| Service layer with lifecycle hooks | Shipped |
| Content negotiation (JSON, Markdown, CSV, YAML) | Shipped |
| Web UI (entries, search, backlinks, daily notes, templates) | Shipped |
| Slash commands in editor | Shipped |
| Type metadata with AI instructions | Shipped |

## Ideal Customer Profile

1. **Engineering-led companies** (50-500 employees) where developers influence tool choices
2. **Companies already using git for documentation** (docs-as-code culture)
3. **Organizations deploying internal AI assistants** who need structured knowledge backends
4. **Remote-first companies** where knowledge management is existential (no hallway conversations)

## Go-to-Market

**Immediate:**
- Do not target enterprise directly yet — the web UI needs significant polish
- Build credibility through developer adoption (markets 1-3 feed into this)
- Publish "knowledge-as-code" thought leadership: why git-native knowledge management matters

**Next quarter:**
- Company KB starter kit: org types (team, project, process, decision, person) with relationship templates
- Single sign-on (SSO) support — table stakes for enterprise
- Role-based access control beyond the three MCP tiers

**Later:**
- Slack/Teams integration (capture and file knowledge from chat)
- Admin dashboard: KB health metrics, stale entry detection, coverage gaps
- Compliance features: audit logging, retention policies, access reports
- Import from Confluence/Notion/SharePoint (migration tooling)
- Managed hosting option

## Feature Gaps

| Gap | Effort | Impact |
|-----|--------|--------|
| Web UI polish (match Notion quality) | XL | Critical — enterprise buyers evaluate on UI |
| SSO / SAML authentication | M | Critical — enterprise gatekeeper requirement |
| Role-based access control (per-KB, per-team) | M | Critical — enterprise security requirement |
| Admin dashboard and analytics | L | High — managers need visibility |
| Confluence/Notion import tools | M | High — migration path |
| Slack/Teams integration | M | Medium — captures knowledge from where it lives |
| SOC 2 / compliance documentation | L | Critical for enterprise sales |

## Risks

- **Notion and Confluence have massive moats** — brand recognition, existing data, team habits, IT procurement relationships
- **Enterprise sales cycles are long** — 6-18 months; requires dedicated sales team; cash-intensive
- **"Good enough" is the enemy** — Confluence is bad but familiar; switching costs are real
- **UI gap is significant** — enterprise buyers compare to Notion's polish; CLI-first won't fly
- **Support expectations** — enterprise customers expect SLAs, phone support, dedicated CSMs
- **This market funds competitors** — Notion ($10B valuation), Atlassian ($50B market cap); hard to compete on resources
- **Risk of spreading too thin** — pursuing enterprise before nailing developer/agent markets could dilute focus
- **Single-maintainer project** — enterprise procurement requires confidence in project longevity; must build community and contributor base through markets 1-3 before enterprise is viable

## Strategic Note

Enterprise KB is the largest addressable market but should be approached last. The path is:

1. Win developers through agent shared memory and software-KB (markets 1-2)
2. Win domain researchers through plugin protocol (market 3)
3. Prove at scale through journalism/OSINT (market 4)
4. Enter enterprise with proven technology, reference customers, and a polished web UI

Trying to compete with Notion on day one is a losing strategy. Building from the bottom up — developers first, then their organizations — is how Slack, GitHub, and Figma won enterprise.
