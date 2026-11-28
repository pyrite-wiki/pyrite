---
id: support-docs-platform
type: design_doc
title: "Market Positioning: Support-to-Docs Publishing Platform"
status: active
author: markr
date: "2026-07-03"
tags: [positioning, support, documentation, publishing, vertical, agents]
lifecycle: archived
---

# Market Positioning: Support-to-Docs Publishing Platform

**Priority: 4 — Post-extraction, post-pilot; keep warm until a
design partner appears**

## The motion (not just a market)

Internal company support/engineering KBs as the backend; the public
software docs site as the published frontend; an agent-assisted
synthesis loop in between that promotes case patterns into canonical
documentation — with provenance. Unlike the sibling positioning
notes (software-teams, enterprise-kb), this is a *product motion*,
not a KB category: pyrite's two-surface architecture ("consumption
is static, investigation is live" — epic-pyrite-publication-strategy)
applied to the support→docs pipeline.

## Why pyrite is structurally suited — the operation IS the prototype

The journalism deployment maps 1:1; every capability is already
built or on the 0.25/0.26 path for the operator's own use:

| Journalism operation (reference deployment) | Support platform |
|---|---|
| Internal research KBs (cascade-research) | Internal support/engineering KBs |
| Research tasks-as-record, claimed by an agent fleet | Cases/tickets claimed by support agents (human + AI) |
| Ephemeral job-memory KBs per investigation | Per-ticket debugging scratch (ADR-0029 ephemerals) |
| Conductor synthesis: "3+ completions reveal a pattern → promote a theme" | "3+ cases reveal a pattern → promote a docs page" |
| Editorial pipeline: brief → draft → publish-audit | Curation: case finding → doc draft → review |
| capturecascade.org static published tier | Public docs site (static, fast, SEO) |
| Read-only pilot peers; grants | Public read tier / staff write tier |
| Git audit trail on the corpus | Compliance: provable history of what the docs said, when |

The synthesis loop is the differentiator no incumbent runs: support
tools capture cases; none operate a conductor that promotes case
patterns into published docs with the evidence trail attached. The
investigation-conductor's promotion criteria ("three or more worker
completions reveal a pattern none articulated individually; the
structural claim is defensible from committed artifacts") are
already a support-knowledge-management policy, field-tested.

## Competitive landscape

| Competitor | Approach | Why it falls short |
|-----------|----------|-------------------|
| **Zendesk Guide / Intercom** | Ticketing with bolted-on KB | Cases and docs are separate silos; no promotion loop; no git; heavy lock-in |
| **Notion / Confluence support spaces** | Wiki as internal KB | Knowledge graveyard dynamics; no typed structure; no agent-native surface |
| **Docs-as-code (GitBook, Docusaurus, ReadTheDocs)** | Published frontend only | No internal-investigation tier; no case substrate; docs rot because nothing feeds them |
| **AI support copilots (Fin, Forethought, etc.)** | LLM over the ticket pile | Answers from unstructured history; no curation loop; no provenance; docs never improve |

## Differentiators

1. **Knowledge-as-markdown-in-THEIR-git** — the anti-lock-in pitch
   writes itself; the derived-disposable DB (ADR-0029) means leaving
   pyrite costs nothing, which is exactly why buyers can adopt it.
2. **Agent-native from the ground up** — MCP read/write/admin tiers,
   atomic task claims with leases, ephemeral job memory,
   conductor-orchestrated fleets. Competitors are adding copilots to
   ticket piles; pyrite is the substrate agent fleets already work.
3. **Self-hostable with a real threat model** — the journalist
   hosting-security work (REQ-1..8) doubles as enterprise
   data-residency posture.
4. **The promotion loop with provenance** — every public docs page
   traces to the cases and evidence that produced it; compliance and
   trust story in regulated verticals.
5. **Versioned docs history** — git answers "what did our docs say
   when the customer filed this" natively.

## Sequencing gates (in order; none on the 0.25 path)

1. Social-plugin extraction (proves extension packaging; the
   engagement layer for published docs sites — see ADR-0029 §5).
2. Shared-instance pilot complete (multi-user tiers proven on the
   journalism deployment).
3. Library/ephemeral machinery shipped (0.26 — ADR-0029).
4. **A design partner asks for it.** Do not build ahead of one; the
   reference deployment keeps the story credible at zero marginal
   cost until then.

## Risks

- Solo founder with a mission-bearing primary operation; this is a
  second business. Mitigation: the gate structure above — the
  vertical is configuration over the same machinery, so "keeping it
  warm" costs nothing.
- Crowded market with strong incumbents. Mitigation: enter through
  the motion (support→docs promotion loop) where incumbents are
  structurally absent, not through general KM (see enterprise-kb's
  Priority-5 caution).

## Related

[[epic-pyrite-publication-strategy]] (static/live split),
[[adr-0029]] (libraries, ephemerals, runtime state — the machinery),
[[software-teams]] and [[enterprise-kb]] (adjacent positioning; this
note is the productized motion between them), engagement-federation
(Future roadmap item — the docs-site feedback layer).
