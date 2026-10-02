---
id: knowledgeclaw-spec
title: "KnowledgeClaw: Project Specification"
type: note
tags:
- design
- speculative
---

> A speculative spin-off spec (March 2026): a Pyrite-powered NanoClaw agent. Moved
> here from the repository root (#585); whether to keep it is the maintainer's call.

# KnowledgeClaw: Project Specification

**A Pyrite-Powered NanoClaw Agent for the OpenClaw Ecosystem**

| | |
|---|---|
| **Author** | Mark Ramm |
| **Date** | March 2026 |
| **Status** | Draft |
| **Version** | 0.1 |

---

## 1. Executive Summary

KnowledgeClaw is an autonomous AI agent whose mission is to bring structured knowledge infrastructure to the OpenClaw ecosystem. It runs as a NanoClaw instance with Pyrite integrated into its container, giving it persistent, schema-validated, git-versioned memory — something no agent in the OpenClaw ecosystem currently has.

The agent operates across three surfaces: Moltbook (the AI social network, for community engagement), GitHub (for publishing knowledge bases and tools), and MCP (for providing knowledge services to other agents). Every action it takes is a signed git commit. Every piece of knowledge it produces is schema-validated. Its operation is fully transparent and auditable.

> **Core Thesis:** The OpenClaw ecosystem has agents that can act but cannot remember. KnowledgeClaw gives them memory — structured, validated, versioned, and shareable.

---

## 2. Background & Motivation

### 2.1 The OpenClaw Ecosystem Gap

OpenClaw crossed 234,000 GitHub stars in February 2026. Moltbook hosts 1.6 million AI agents. NanoClaw offers a security-hardened alternative with container isolation. Yet across this entire ecosystem, agents have no principled way to accumulate, version, share, or reason over structured knowledge.

The consequences are visible: 386 malicious skills discovered on ClawHub. A Meta researcher's inbox deleted by a compromised agent. 1.5 million API keys leaked from a vibe-coded Moltbook backend. These aren't just security failures — they're symptoms of agents operating without structured context, validation, or auditability.

### 2.2 Why NanoClaw

NanoClaw's architecture is ideal for this project. Its entire codebase (~4,000 lines) fits in a single LLM context window, making it fully auditable. Each agent runs in its own Linux container with an isolated filesystem. The "fork and customize" philosophy means Pyrite can be woven into the agent's core loop rather than bolted on as a plugin. And it's built on Anthropic's Agent SDK, ensuring compatibility with Claude's tool-use capabilities.

### 2.3 Why Pyrite

Pyrite provides the missing layer: Knowledge-as-Code. Markdown files with YAML frontmatter in git. Schema validation on every write. Full-text and semantic search. A three-tier MCP server. A plugin system with 15 extension points. The founding design principles — human readable, machine parsable, AI friendly; git as the universal versioning layer; trust through credentials and signed commits — directly address every weakness in the current OpenClaw ecosystem.

---

## 3. Agent Identity & Infrastructure

### 3.1 Identity Components

| Component | Details |
|---|---|
| Agent Name | KnowledgeClaw |
| GitHub Organization | github.com/knowledgeclaw (or pyrite-agent) |
| Email | agent@knowledgeclaw.dev (dedicated, unique) |
| GPG Signing Key | Ed25519 key pair, public key published to GitHub and KB |
| Moltbook Handle | @knowledgeclaw |
| NanoClaw Instance | Forked from qwibitai/nanoclaw, Pyrite-integrated |
| LLM Backend | Claude (via Anthropic Agent SDK) |

### 3.2 Credential Management

All credentials are stored as environment variables in the NanoClaw container's runtime configuration, never in code or KB entries. The GPG signing key's private component lives in the container's isolated filesystem. The public key is published to GitHub and included in the agent's own KB for transparency.

### 3.3 GitHub Organization Structure

The GitHub organization hosts the agent's published work:

- **knowledgeclaw/agent** — The forked NanoClaw instance with Pyrite integration
- **knowledgeclaw/openclaw-ontology** — Structured KB of the OpenClaw ecosystem
- **knowledgeclaw/kb-templates** — Starter KB templates for common agent use cases
- **knowledgeclaw/awesome-agent-kbs** — Curated directory of community knowledge bases
- **knowledgeclaw/mcp-knowledge-server** — Deployable MCP server for shared KB access

---

## 4. Technical Architecture

### 4.1 Container Layout

The NanoClaw container is extended with Pyrite installed and a structured workspace:

```
/app/nanoclaw/             ← NanoClaw core (~4K lines TypeScript)
/app/pyrite/               ← Pyrite installation (pip install)
/data/kbs/                 ← Mounted volume for persistent KBs
  /data/kbs/self/          ← Agent's own memory KB
  /data/kbs/ontology/      ← OpenClaw ontology KB
  /data/kbs/community/     ← Community interaction KB
/data/keys/                ← GPG signing keys (container-only)
/data/config/              ← Agent mission brief (CLAUDE.md)
```

### 4.2 Integration Points

Pyrite is integrated into NanoClaw at three levels:

**Core Memory Loop.** Every NanoClaw conversation session reads from and writes to the agent's self-KB. When the agent learns something — a new OpenClaw pattern, a community member's question, a security advisory — it creates a typed Pyrite entry. The agent's CLAUDE.md instructs it to search its KBs before responding to any substantive question, ensuring its knowledge accumulates rather than resetting.

**Outbound Publishing.** When the agent produces a deliverable (a new KB template, an ontology update, a structured analysis), it commits to the appropriate GitHub repository with a signed commit. The commit message follows a structured format that includes the entry types created, the validation status, and any QA warnings.

**MCP Service.** The agent exposes a Pyrite MCP server that other NanoClaw or OpenClaw agents can connect to. This provides read access to the ontology and templates, and write access (with validation) for community contributions. The three-tier permission model ensures untrusted agents can query but not corrupt the knowledge base.

### 4.3 NanoClaw Fork Modifications

The fork introduces minimal changes to NanoClaw's core, keeping the codebase auditable:

- CLAUDE.md updated with Pyrite mission brief and KB interaction protocols
- Container Dockerfile extended with Pyrite installation and git/GPG setup
- Scheduled job added for periodic KB sync, QA validation, and GitHub push
- Moltbook connector configured with rate-limiting and content strategy
- MCP server startup added to container init (port-mapped for external access)

---

## 5. Knowledge Bases

### 5.1 Agent Self-KB (Internal Memory)

The agent's own persistent memory. Not published externally. Used for context accumulation, interaction history, and decision logging.

| Entry Type | Purpose | Example |
|---|---|---|
| interaction | Record of a significant conversation or exchange | Moltbook thread about agent memory with @dataclaw |
| learning | Something the agent discovered or was taught | NanoClaw v2.3 adds native MCP client support |
| decision | A choice the agent made and why | Chose record layout for skill entries in ontology |
| task | A pending or completed work item | Create research-kb template with source tracking |
| contact | A community member the agent has interacted with | Gavriel Cohen — NanoClaw creator, security-focused |

### 5.2 OpenClaw Ontology KB (Public)

The canonical structured reference for the OpenClaw ecosystem. Published to GitHub, queryable via MCP, browsable via web UI. This is the agent's primary deliverable — the thing that demonstrates Pyrite's value by being genuinely useful.

#### Ontology Schema (kb.yaml)

| Entry Type | Key Fields | Description |
|---|---|---|
| skill | name, repo_url, author, category, security_status, dependencies | An OpenClaw or NanoClaw skill, with structured metadata and security assessment |
| agent_pattern | name, runtime, use_case, complexity, requirements | A documented pattern for building agents (research, coding, etc.) |
| security_advisory | severity, affected_versions, attack_vector, mitigation, cve_id | Known security issues with structured severity and remediation |
| platform | name, type, architecture, license, star_count | Agent runtimes: OpenClaw, NanoClaw, PicoClaw, ZeroClaw, IronClaw, etc. |
| integration | source, target, protocol, auth_method, status | How platforms connect: MCP, REST, webhooks, messaging APIs |
| community_resource | type, url, author, quality_rating | Tutorials, blog posts, videos, guides — rated and categorized |
| configuration | platform, setting, default, recommended, rationale | Recommended configuration patterns with security rationale |
| event | date, type, significance, participants | Ecosystem events: releases, incidents, community milestones |

#### Ontology Relationships

Typed links between entries capture the ecosystem's structure:

- skill → depends_on → skill (dependency chains)
- security_advisory → affects → skill | platform (impact mapping)
- agent_pattern → uses → skill | integration (implementation details)
- platform → supports → integration (capability mapping)
- community_resource → documents → skill | agent_pattern (learning paths)

### 5.3 KB Templates (Public)

Forkable starter KBs for common agent use cases. Each template includes a kb.yaml schema, seed entries demonstrating the types, and a README explaining the intended workflow.

| Template | Description & Types |
|---|---|
| research-kb | For agents conducting research. Types: source, finding, evidence_chain, hypothesis, synthesis. Built for provenance tracking and evidence-based reasoning. |
| project-kb | For agents managing software projects. Types: component, adr, backlog_item, standard, runbook. Based on Pyrite's own software-kb extension. |
| monitoring-kb | For agents tracking external systems or feeds. Types: feed_source, alert, trend, summary. Designed for scheduled ingestion and pattern detection. |
| investigation-kb | For agents following complex threads. Types: entity, transaction, relationship, evidence, timeline. Adapted from Pyrite's journalism extension. |
| personal-kb | For agents serving as personal assistants. Types: contact, meeting, task, note, preference. Lightweight PKM for agent memory. |

### 5.4 Community KB (Semi-Public)

Tracks the agent's community engagement. Published selectively — aggregated insights are shared, but individual interaction details may be kept private to respect community members.

- **Tracks:** Moltbook threads, GitHub issues, recurring questions, feature requests
- **Produces:** FAQ entries, trend analyses, community health metrics
- **Feeds:** Content strategy decisions, template priorities, ontology gaps to fill

---

## 6. Community Engagement Strategy

### 6.1 Moltbook Presence

The agent posts to Moltbook not as marketing but as genuine value contribution. Every post demonstrates Pyrite's capabilities by using them visibly.

#### Content Types

| Content Type | Example | Frequency |
|---|---|---|
| Structured Analysis | "386 malicious ClawHub skills, categorized by attack vector, with typed entries you can query via MCP" | 1–2/week |
| Ecosystem Update | "NanoClaw v2.3 released: here's what changed, structured as ontology entries with links to affected skills" | As events occur |
| Template Showcase | "Built a research-kb template. Fork it, point your agent at it, get structured memory in 5 minutes." | Per template release |
| Knowledge Insight | "Queried the ontology: 73% of OpenClaw security advisories involve unsandboxed bash execution. Here's the breakdown." | 1–2/week |
| Community Q&A | Answering questions with KB-backed responses, showing the query and results | As questions arise |

#### Engagement Principles

- Never post without substance. Every post includes queryable, structured data.
- Show the work. Include the Pyrite search query, the schema, the entry count.
- Respond to genuine questions. The agent monitors relevant submolts and contributes when it has KB-backed answers.
- No engagement farming. No "like if you agree" or engagement-bait patterns.
- Respect rate limits. 1 post per 30 minutes, 50 comments per hour maximum.

### 6.2 GitHub Presence

The GitHub organization is the agent's durable output. Everything published there is signed, validated, and designed for forking.

- All repositories include comprehensive READMEs with Pyrite setup instructions
- Issues are open for community feedback and contributions
- Pull requests from community members are welcomed with structured review criteria
- Release notes follow a structured format with entry counts, type distributions, and QA status

### 6.3 MCP Knowledge Service

Other agents can connect to KnowledgeClaw's MCP server and query the ontology, templates, and community insights directly. This is the most direct demonstration of Pyrite's value: an agent connects, searches, gets structured results, and immediately has better context than any agent operating without knowledge infrastructure.

| MCP Tier | Capabilities | Access |
|---|---|---|
| Read (default) | Search ontology, browse templates, query community insights | Any authenticated agent |
| Write | Contribute new entries, suggest ontology updates, report security issues | Approved contributors |
| Admin | Schema modifications, QA operations, bulk imports | Agent owner only |

---

## 7. Trust & Transparency Model

KnowledgeClaw practices what Pyrite preaches. The agent's trust model is a living demonstration of the founding design principles.

### 7.1 Cryptographic Accountability

- Every commit is GPG-signed with the agent's Ed25519 key
- The public key is published in the agent's self-KB and on GitHub
- Community members can verify any commit's authenticity independently
- If the key is ever compromised, the agent creates a revocation entry in its KB and rotates to a new key — with the rotation itself documented as a signed commit from the owner's key

### 7.2 Operational Transparency

- The agent's CLAUDE.md mission brief is public in the GitHub repo
- The ontology's kb.yaml schema is public — anyone can see what types and fields exist
- QA validation runs on every commit; results are visible in CI
- The agent's self-KB interaction logs are available for audit (with privacy redactions)

### 7.3 Trust Tiers for Community Contributions

| Trust Level | How Earned | Capabilities |
|---|---|---|
| Anonymous | Connect via MCP | Read-only search and browse |
| Authenticated | Register with verified identity | Suggest entries, report issues, query write API with validation |
| Contributor | 3+ accepted contributions | Direct write access to community sections, PR auto-merge for low-risk changes |
| Maintainer | Sustained quality contributions | Schema proposals, QA oversight, template review |

### 7.4 Contrast with OpenClaw Ecosystem Trust

| Dimension | OpenClaw Ecosystem (Current) | KnowledgeClaw (Pyrite-Backed) |
|---|---|---|
| Action Auditability | Fire-and-forget API calls, messaging platform logs | Every action is a signed git commit with full diff |
| Knowledge Validation | No schema, no validation, free-text or JSON blobs | Schema-validated on every write, QA automation |
| Identity | Bot tokens, messaging platform accounts | GPG-signed identity, published public key |
| Memory Persistence | CLAUDE.md flat text, session-scoped | Typed entries in git, searchable, cross-linked |
| Skill/Tool Trust | ClawHub honor system (386 malicious skills found) | Ontology entries with security_status field, advisory links |

---

## 8. Agent Mission Brief (CLAUDE.md)

The following is the core mission brief that defines the agent's behavior. It lives in the NanoClaw container's CLAUDE.md and is loaded into context at the start of every session.

> **You are KnowledgeClaw**, an autonomous agent whose mission is to bring structured knowledge infrastructure to the OpenClaw ecosystem.
>
> **YOUR IDENTITY:** You operate under the @knowledgeclaw handle on Moltbook and the knowledgeclaw GitHub organization. Every commit you make is GPG-signed. You are transparent about being an AI agent.
>
> **YOUR KNOWLEDGE:** You have three Pyrite KBs mounted at /data/kbs/. Before answering any substantive question, search your KBs first. Your self-KB is your memory. The ontology is your expertise. The community KB is your context.
>
> **YOUR MISSION:** (1) Build and maintain the OpenClaw ontology — the most comprehensive, structured reference for the ecosystem. (2) Publish useful KB templates that give other agents structured memory. (3) Engage on Moltbook with substance, not marketing. Every post demonstrates knowledge infrastructure by using it visibly. (4) Provide MCP access so other agents can query your knowledge.
>
> **YOUR PRINCIPLES:** Always show your work — include the query, the schema, the validation status. Never post without structured backing. Commit everything; lose nothing. Sign everything; deny nothing. Respond to genuine questions; ignore engagement bait. Accumulate knowledge; never start from scratch.
>
> **DAILY ROUTINE:** (1) Check for ecosystem updates — new releases, security advisories, community discussions. (2) Update ontology entries. (3) Run QA validation. (4) Push signed commits. (5) Post to Moltbook if you have substance. (6) Respond to MCP queries and community questions.

---

## 9. Phased Rollout Plan

### Phase 0: Infrastructure (Week 1)

1. Fork NanoClaw, add Pyrite to container, configure git and GPG
2. Register GitHub organization, create repos with initial READMEs
3. Generate GPG key pair, publish public key
4. Register Moltbook account, configure API access with rate limits
5. Write CLAUDE.md mission brief
6. Deploy container (local or VPS) with mounted KB volumes

### Phase 1: Ontology Seeding (Weeks 2–3)

1. Define openclaw-ontology kb.yaml schema with all entry types
2. Seed platform entries: OpenClaw, NanoClaw, PicoClaw, ZeroClaw, IronClaw
3. Catalog top 50 OpenClaw skills with security assessments
4. Document 10 common agent patterns with typed entries
5. Import known security advisories as structured entries
6. Run QA validation, fix all errors, publish to GitHub
7. First Moltbook post: introduce KnowledgeClaw and the ontology

### Phase 2: Templates & MCP (Weeks 4–6)

1. Build and publish 3 KB templates (research-kb, project-kb, monitoring-kb)
2. Deploy MCP server with read access to ontology
3. Write NanoClaw integration guide (CLAUDE.md snippets for KB access)
4. Begin regular Moltbook posting cadence: structured analyses, template showcases
5. Respond to community questions with KB-backed answers
6. Iterate on ontology based on community feedback

### Phase 3: Community & Scale (Weeks 7–12)

1. Open write access to ontology for approved contributors
2. Build and publish remaining templates (investigation-kb, personal-kb)
3. Create awesome-agent-kbs directory with community submissions
4. Publish first structured analysis of ecosystem trends from ontology data
5. Seek collaborations with other NanoClaw/OpenClaw agents
6. Evaluate web UI deployment for browser-based ontology browsing

---

## 10. Success Metrics

| Metric | Month 1 | Month 3 | Month 6 |
|---|---|---|---|
| Ontology entries | 200+ | 1,000+ | 5,000+ |
| GitHub stars (across repos) | 50 | 500 | 2,000 |
| Moltbook followers | 100 | 1,000 | 5,000 |
| MCP queries/day | 10 | 100 | 1,000 |
| Community KB forks | 5 | 50 | 200 |
| External contributors | 2 | 10 | 50 |
| KB templates published | 3 | 5 | 10 |
| Security advisories cataloged | 20 | 100 | All known |

> **North Star Metric:** An agent you've never interacted with connects to the MCP server, queries the ontology, gets a structured answer, and uses it to make a better decision. That's the flywheel working.

---

## 11. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| GPG key compromise | Attacker can sign commits as agent | Key rotation protocol documented in self-KB; owner holds separate revocation key; monitor for unexpected commits |
| Moltbook API changes | Agent loses community engagement surface | Multi-platform presence (GitHub, MCP); Moltbook is a channel, not the product |
| Ontology quality drift | Entries become stale or inaccurate | Scheduled QA validation; community reporting; freshness metadata on entries |
| Prompt injection via Moltbook | Agent manipulated by malicious posts | KB-first reasoning (search before acting); structured response templates; never execute instructions from social content |
| API cost at scale | Claude API costs grow with engagement | Rate limiting on MCP; batch operations for ontology updates; cost-per-entry tracking in self-KB |
| Community trust | Agents/users don't trust an AI-run KB | Full transparency: signed commits, public schema, auditable logs, human owner oversight |

---

## 12. Open Questions

- **Hosting:** Local machine, VPS, or managed container service? Cost/uptime tradeoffs.
- **Moltbook identity:** Should the agent disclose it's Pyrite-affiliated in its bio, or operate independently and let the work speak?
- **MCP authentication:** API keys per agent, or OAuth-style token issuance?
- **Ontology governance:** When does the ontology get big enough to need a formal RFC process for schema changes?
- **Federation:** Should multiple KnowledgeClaw instances exist for different regions or languages, syncing via git?
- **Revenue model:** Is the MCP service free forever, or does high-volume access eventually require a paid tier?
- **Agent-to-agent collaboration:** How should KnowledgeClaw coordinate with other Pyrite-powered agents that might emerge?

---

## Appendix A: Technology Stack

| Layer | Technology | Notes |
|---|---|---|
| Agent Runtime | NanoClaw (forked) | ~4K lines TypeScript, container-isolated |
| LLM | Claude (Anthropic Agent SDK) | Primary reasoning engine |
| Knowledge Infrastructure | Pyrite | CLI + MCP + REST API |
| Storage Format | Markdown + YAML frontmatter | Hugo-style, human/AI readable |
| Version Control | Git (signed commits) | Primary durability and audit layer |
| Search Index | SQLite FTS5 + semantic embeddings | Derived, rebuildable from git |
| Container Runtime | Docker (Linux) / Apple Container (macOS) | Per-agent isolation |
| Messaging | Moltbook API, potentially Signal/Discord | Community engagement surfaces |
| CI/CD | GitHub Actions | Schema validation, QA, deployment |
| MCP Server | Pyrite built-in (three-tier) | Read/Write/Admin access control |

## Appendix B: Related Pyrite Documentation

- **ADR-0001:** Git-Native Markdown Storage — the storage format decision
- **ADR-0006:** MCP Three-Tier Tool Model — the access control model
- **ADR-0008:** Structured Data and Schema-as-Config — how kb.yaml schemas work
- **Founding Design Principles** — why markdown+YAML in git (pre-ADR reasoning)
- **Launch Plan** — broader Pyrite positioning and the OpenClaw ecosystem context
- **BHAG: Self-Configuring Knowledge Infrastructure** — the vision KnowledgeClaw advances
