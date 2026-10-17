---
id: knowledgeclaw-pyrite-powered-agent-for-openclaw-ecosystem
title: 'KnowledgeClaw: Pyrite-Powered Agent for OpenClaw Ecosystem'
type: backlog_item
tags:
- post-launch
- agent
- openclaw
- mcp
- knowledge-infrastructure
kind: feature
status: retired
priority: medium
effort: xl
milestone: post-launch
---

## Summary

An autonomous AI agent that brings structured knowledge infrastructure to the OpenClaw ecosystem. It builds and maintains a public ontology of the ecosystem (skills, platforms, security advisories, agent patterns), publishes it via MCP, and engages on Moltbook with KB-backed substance.

**Core thesis:** The OpenClaw ecosystem has agents that can act but cannot remember. KnowledgeClaw gives them memory — structured, validated, versioned, and shareable.

## Key Decisions

- **Runtime:** Anthropic Agent SDK (Python), NOT a NanoClaw fork. Avoids polyglot container complexity and fork maintenance. NanoClaw compatibility via MCP.
- **Container:** Apple Containers (not Docker).
- **Scope (narrowed for v1):** One KB (the ontology), one surface (GitHub), one integration (MCP read-only). Moltbook presence, community contributions, templates, and self-KB are expansion after the flywheel turns.
- **North star:** An agent you've never interacted with connects to the MCP server, queries the ontology, gets a structured answer, and uses it to make a better decision.

## Gated On

- Pyrite 0.16 — requires stable PyPI package, container deployment story, MCP rate limiting, and post-launch ecosystem maturity.

## Open Design Questions

- Should the agent be visibly Pyrite-affiliated or operate independently? (Recommendation: lean into independence — the "how does this work so well?" discovery moment is more valuable than branding.)
- MCP authentication model: API keys per agent, or OAuth-style tokens?
- Ontology governance: when does it need a formal RFC process for schema changes?
- Data ingestion pipeline: what sources does the agent monitor, how often, what schema mappings?
- Federation: multiple instances for regions/languages, syncing via git?

## Ontology Schema (Draft)

Entry types: `skill`, `agent_pattern`, `security_advisory`, `platform`, `integration`, `community_resource`, `configuration`, `event`

Key relationships: `depends_on`, `affects`, `uses`, `supports`, `documents`

## Reference

Full spec (v0.1, March 2026) available as uploaded document. Includes detailed technical architecture, community engagement strategy, trust model, phased rollout plan, and success metrics.

Spec evaluation notes:
- Sequencing: must ship after Pyrite launch, not before
- Narrow v1 scope: one KB, one surface, one integration
- Drop vanity metrics (followers, stars); focus on flywheel metrics (MCP queries, KB forks, contributors)
- Need explicit data ingestion pipeline design before "autonomous" is meaningful
- Templates: ship one excellent one (research-kb) first, expand based on demand
