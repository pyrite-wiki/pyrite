---
id: launch-messaging
title: "Launch Messaging Foundation"
type: note
tags:
- launch
- marketing
- messaging
links:
- launch-plan
- bhag-self-configuring-knowledge-infrastructure
- roadmap
lifecycle: archived
---

> **Historical**: March 2026 launch plan.

# Launch Messaging Foundation

Every content piece — blog post, video, Reddit post, email — draws from this section. The goal: anyone producing launch content can look here and know exactly what the argument is.

## The Core Insight

**Your AI is already smart. Pyrite makes it knowledgeable.**

Claude, Codex, OpenClaw — they can reason, write code, analyze documents. But they don't *know* anything about your specific domain. They lose context between sessions. They can't validate their own output against your schema. They start from scratch every time.

Pyrite fixes that. Add a knowledge base, and your AI becomes a domain expert — with structured memory, validated facts, and typed relationships. It doesn't just remember things; it knows them *reliably*.

## The Elevator Pitches

**The headline (works everywhere):** "Pyrite turns your AI into a domain expert."

**One sentence:** "Pyrite gives AI agents persistent, structured, validated knowledge — so Claude, Codex, or OpenClaw can become genuine domain experts instead of starting from scratch every session."

**The technical version:** "Pyrite is a knowledge compiler — define a schema, the schema generates infrastructure (validation, search, API surface), and the infrastructure constrains agents to produce quality output."

**For Claude/Cowork users:** "Add a Pyrite KB and give Claude superpowers. It searches structured knowledge instead of hallucinating."

**For agent builders:** "Persistent structured memory for your agent runtime. Schema validation on every write. Your agents don't lose context — they build on it."

## The Four-Wave Story

Each launch wave builds on the same core pitch but demonstrates it for a different audience, progressively widening the aperture from developers to everyone:

**Wave 1 — Platform Launch (0.16):** "Pyrite turns your AI into a domain expert." Here's the platform. Connect it to Claude Desktop, Claude Code, OpenClaw, Codex. Add a KB, get structured memory with validation and search. Instant domain expertise. *Audience: agent builders, MCP users, Python developers.*

**Wave 2 — Software Project Plugin:** "Here's what domain expertise looks like for software teams." Your agents understand your architecture, your backlog, your ADRs, your runbooks. They navigate typed, validated, interconnected project knowledge — not scattered markdown files. Workflows let agents claim tasks, report progress, and collaborate natively. *Audience: dev teams, engineering leads, Claude Code/Codex power users.*

**Wave 3 — Investigative Journalism Plugin:** "And here's a completely different domain." Follow the money. Entities, transactions, relationships, evidence chains, source validation. Same platform, same superpowers, entirely different world. Proof that Pyrite is genuinely general-purpose. *Audience: journalists, OSINT researchers, the HN/Reddit crowd that finds investigations fascinating.*

**Wave 4 — PKM Capture Plugin:** "Now it's for everyone." Snap a photo, paste a URL, record a voice note — Pyrite ingests it, classifies it, extracts structure, and creates typed entries automatically. Mobile access through Claude app and web UI. The knowledge you capture on the go becomes structured, searchable, connected knowledge by the time you sit down. *Audience: PKM power users (Obsidian, Anytype, Logseq), anyone who collects and organizes information.*

Wave 4 is when the aperture goes wide. By this point, three shipping plugins prove the platform works. The PKM crowd isn't hearing "here's a CLI tool, trust us." They're hearing "here's the tool that software teams, journalists, and AI agents all use for structured knowledge — and now it captures your web clippings, voice notes, and photos too."

**The BHAG (implicit in every wave):** "Today you build the KB and the agent becomes an expert. Tomorrow the agent builds the KB itself."

## The Pain Story (before/after)

**Before Pyrite:**

You point an AI agent at a new domain — legal research, threat intelligence, scientific literature. What happens? The agent stuffs findings into flat text files, or a vector database with no schema, or just loses context entirely when the session ends. The next agent that works in that domain starts from scratch. The agent after that, same thing. No validation. No structure. No memory. Every agent reinvents the wheel, and the wheels are bad.

Meanwhile, your team's Confluence hasn't been updated in six months. Nobody trusts the search. The new hire reads three contradictory runbooks before asking a human anyway. Your docs aren't wrong — they're just unvalidated, unsearchable, and disconnected.

**After Pyrite:**

Your agent creates a typed knowledge base: legal cases have jurisdiction, status, and citation fields. Statutes link to the cases that interpret them. Every entry is validated on write — missing fields, broken links, schema violations caught immediately. The next agent that works in this domain inherits structured, queryable, validated knowledge. It doesn't start from scratch. It starts from infrastructure.

Your team's docs live in git. A QA agent catches the stale runbook, the broken link, the missing field. Search actually works — full-text, semantic, and structured queries across everything. The knowledge graph shows how your ADRs connect to your components connect to your backlog. And when someone asks Claude a question about your architecture, it searches the actual KB instead of hallucinating.

## The "Why Now" Argument

The agent explosion is already here. OpenClaw hit 180K GitHub stars in weeks. Tens of thousands of autonomous agents are deployed and that number is growing exponentially. Gartner predicts 40% of enterprise apps will embed agents by end of 2026. Claude Code, Codex, CrewAI, LangGraph, AutoGen — every team is deploying autonomous agents.

But these agents have no persistent memory infrastructure. Vector databases store embeddings without structure. RAG pipelines retrieve without validating. Chat histories lose context between sessions. The tooling assumes humans will always be in the loop to organize knowledge. That assumption is already broken.

Every one of those agents needs what Pyrite provides. The market exists *right now*.

## The "Why Us" Argument

This isn't a prototype or a weekend project. Pyrite has 1780+ tests. Five shipped extensions proving the plugin protocol works. A 4800-entry timeline KB proving it works at scale. 13 architecture decision records documenting every major choice. We use Pyrite to build Pyrite — the project's own knowledge base runs on the tool. The plugin protocol has 15 extension points. The MCP server has three permission tiers. The CLI outputs structured JSON on every command. This is infrastructure you can build on.

## Three Portals, One Knowledge Base

Pyrite isn't a CLI tool, or a web app, or an MCP server. It's all three — three equal interfaces to the same structured knowledge, optimized for different moments:

**CLI** — the agent-native path. OpenClaw, Claude Code, Codex, shell scripts, cron jobs. This is where agents do the heavy lifting: bulk creation, extension management, headless provisioning, JSON pipelines. Every command outputs structured JSON. Agents live here.

**MCP** — the conversational path. Claude Desktop, Cowork, any MCP-compatible client. Humans (and agents) talk to the knowledge base in natural language. "What were the key architecture decisions this quarter?" "Find all entities connected to this organization." Three permission tiers control who can do what. Knowledge becomes accessible without learning query syntax.

**Web UI** — the visual path. Knowledge graph exploration, rich entry editor, QA dashboard, BYOK AI integration. This is where you *see* the knowledge — watch the graph grow as agents populate a KB, explore relationships visually, interact with AI workflows from a browser. Also the demo layer: the public demo site lets people experience Pyrite before installing anything.

These aren't competing interfaces — they're complementary. An agent builds the KB through CLI at 3am. You review what it produced in the web UI over coffee. You ask follow-up questions through Claude Desktop. All hitting the same typed, validated, versioned knowledge.

**Every content piece should reinforce this:** your knowledge base is accessible from everywhere your AI already lives. Claude Desktop users don't need to open a browser. Claude Code users don't need to leave the terminal. And when you want the visual overview, the web UI is right there.

## The BHAG Pitch (closer than you think)

Today, a human defines the schema and agents fill it in. Pyrite is building toward agents defining the schema too:

| Approach | Who Defines Schema | Who Populates | Who Validates |
|----------|-------------------|---------------|---------------|
| Obsidian / Notion | Human | Human | Human |
| Vector databases | Nobody (unstructured) | Agent | Nobody |
| RAG pipelines | Human (retrieval config) | Agent | Human (spot-checks) |
| **Pyrite today** | **Human** | **Human + Agent** | **Schema + QA Agent** |
| **Pyrite BHAG** | **Agent** | **Agent** | **Schema + QA Agent** |

An autonomous agent encounters a domain. It scaffolds a Pyrite extension — typed entries, validators, MCP tools. It tests what it built using TDD. It installs the extension, provisions a KB, and starts working. Every future agent that works in that domain gets structured, validated, queryable knowledge instead of flat files and lost context.

**The schema is the program. Pyrite is the runtime.**

This isn't aspirational — it's almost working today. Here's what already ships:

- `pyrite extension init legal --types case,statute,ruling` — agent scaffolds a full extension in one command
- Extension builder skill files guide the agent through implementation, following the same TDD protocol used to build Pyrite itself
- `pyrite extension install extensions/legal --verify` — installs and optionally runs tests
- `pyrite init --template <domain>` — provisions a KB with zero interactive prompts
- `--format json` on every CLI command — agents consume output programmatically
- QA validation on every write — agents validate their own output against the schema

The gap between "human defines schema" and "agent defines schema" is one skill file and a few CLI commands. The pieces are shipping now. The BHAG isn't a 2-year plan — it's a closing sprint.

## Objection Responses (sharp versions)

**"Can't I just use a vector database?"**
A vector database gives you similarity search over embeddings. Pyrite gives you typed entries with validated fields, relationship graphs, full-text and semantic search, git-native versioning, and a plugin system. A vector database is a feature. Pyrite is infrastructure.

**"How is this different from Obsidian?"**
Obsidian is a fantastic tool for humans writing notes. Pyrite is for agents producing structured knowledge at scale. Obsidian has no schema validation, no programmatic API (MCP/REST), no QA automation, and no plugin protocol for domain extensions. If your use case is "I want to write notes," use Obsidian. If your use case is "I want agents to build and query knowledge bases," that's Pyrite.

**"Why not Notion/Confluence?"**
Notion and Confluence are SaaS tools that own your data. Your docs live in their database, searched by their algorithm, organized by their UI. Pyrite is Knowledge-as-Code: your docs are markdown files in git. You own them. You version them. You review them with PRs. You validate them with CI. And AI agents can read and write them through a real API, not a screen-scraping hack.

**"Is this production-ready?"**
It's an alpha — honest about that. But it's a *tested* alpha: 1780+ tests, six shipped extensions, a 4800-entry KB in daily use. The architecture is solid. The rough edges are in packaging and docs, not in the core.

**"Seems over-engineered for notes"**
It's not for notes. It's for knowledge infrastructure. The difference: notes are for one person to read later. Knowledge infrastructure is for agents to query, validate, extend, and build on. The "over-engineering" is exactly the point — schema validation, plugin protocol, three-tier permissions, QA automation. That's what makes it infrastructure instead of another note-taking app.

## How Launch Features Connect to the BHAG

Every launch feature is a step on the path to the BHAG. This connection should be explicit in every content piece:

| Launch Feature | Immediate Value | BHAG Step | Portal |
|---------------|-----------------|-----------|--------|
| QA validation | Catches stale docs, broken links | Agents validate their own output without human review | All three |
| `--format json` on every command | Clean scripting and automation | Agents consume CLI output programmatically | CLI |
| `pyrite init --template` | Quick setup for new KBs | Agents provision KBs non-interactively | CLI |
| `pyrite extension init` | Fast extension scaffolding | Agents build domain-specific extensions | CLI |
| Three-tier MCP | Secure agent access to Claude Desktop/Cowork | Trust tiers let you deploy untrusted agents safely | MCP |
| Web UI + knowledge graph | Visual exploration, see the KB grow | Operators monitor agent-built KBs | Web |
| BYOK AI in UI | Ask questions, trigger workflows from browser | Human-AI collaboration on structured knowledge | Web |
| Extension registry | Discover and install extensions | Agents discover and install domain infrastructure | All three |
| Software plugin (wave 2) | Agents collaborate on your project natively | Domain-specific infrastructure, built on the platform | All three |
| Journalism plugin (wave 3) | Follow-the-money investigation tool | Proves the platform is genuinely general-purpose | All three |
| PKM capture plugin (wave 4) | Ingest anything → structured entries automatically | AI classifies and organizes knowledge without human filing | Web + Mobile |

The blog post, every demo video, and every Reddit post should make at least one of these connections. Don't just show the feature — show where it's going.

## Per-Wave Messaging

**Wave 1 content should say:**
- "Pyrite turns your AI into a domain expert" (headline)
- Here's how it works (platform demo)
- Here's why it matters now (agent explosion, no memory infrastructure)
- Here's where it's going (BHAG — agents building their own knowledge infrastructure)

**Wave 2 content should say:**
- "Your software project, managed by agents that actually understand it" (headline)
- Here's what we built on Pyrite in [X days] (proof the platform works)
- Workflows: agents claim tasks, update status, link evidence
- We use it to build Pyrite itself (dogfooding credibility)
- You could build something like this for your domain too

**Wave 3 content should say:**
- "Follow the money — investigative journalism powered by structured knowledge and AI" (headline)
- Completely different domain, same platform (general-purpose proof)
- The knowledge graph is the investigation (visual payoff)
- Source chains and evidence validation (trust and rigor)
- Imagine what agents could build for *your* domain

**Wave 4 content should say:**
- "Capture anything. Pyrite turns it into structured knowledge." (headline)
- Snap, paste, clip, dictate — AI classifies and files it for you
- Mobile-first capture through Claude app and web UI
- Three plugins already proving the platform works — now it's for everyone
- Your AI doesn't just answer questions about your knowledge — it helps you build it
