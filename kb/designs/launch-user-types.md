---
id: launch-user-types
title: "Launch Plan: User Types & What They Care About"
type: note
tags:
- launch
- marketing
- user-research
links:
- launch-plan
- launch-messaging
- launch-channels
lifecycle: archived
---

> **Historical**: March 2026 launch plan.

# User Types & What They Care About

Five user types, each with different pain points and hooks. See [[launch-messaging]] for the shared messaging foundation.

## 1. PKM Power Users (Obsidian, Anytype, Logseq, Notion personal)

**Target wave: Wave 4 (PKM Capture Plugin)**. Do not actively target this audience until the capture plugin exists. The PKM crowd will be disappointed by a CLI-first platform without frictionless mobile capture. Waves 1-3 may attract some PKM-adjacent early adopters organically, but don't market to them directly until wave 4.

**Profile:** Individual knowledge workers who maintain personal knowledge bases. Already sold on structured notes and markdown. Pain points: search sucks at scale, no validation, tools are siloed, *capturing new knowledge is high-friction*.

**Hook (wave 4):** "Capture anything — photos, web clippings, voice notes, pasted text — and Pyrite turns it into structured, searchable, connected knowledge automatically."

**Key capabilities to demo (wave 4):**
- Frictionless capture: image → OCR/vision → typed entry; URL → extract → classify → entry; voice → transcribe → entry
- Mobile access through Claude app and web UI
- AI auto-classification and tagging (the killer feature for PKM)
- FTS5 + semantic search across everything you've captured
- Knowledge graph showing connections across your captured knowledge
- Schema validation and QA — your knowledge base stays clean automatically
- Git-native versioning (they already use git for Obsidian vaults)
- Markdown source of truth — no lock-in, portable

**Objections to address:**
- "Why not just use Obsidian with plugins?" — Obsidian doesn't auto-classify, validate, or give your AI agent access to your knowledge
- "I don't want to learn a CLI" — Wave 4 is web/mobile first. The CLI exists for power users and agents, not required for PKM
- "Will this lock me in?" — Markdown files in git. You own everything. Take it anywhere.

---

## 2. Corporate KB Teams (Confluence, Notion team, SharePoint)

**Profile:** Teams maintaining shared knowledge: engineering docs, runbooks, architectural decision records, onboarding guides. Pain points: docs rot, search is keyword-only, no validation, no CI/CD for knowledge.

**Hook:** "Your team's knowledge base with version control, schema validation, and AI search — like upgrading from Google Docs to GitHub for your documentation."

**Key capabilities to demo:**
- Typed entries (ADRs, components, runbooks, standards)
- QA validation — catch stale docs, broken links, missing fields automatically
- Web UI for non-CLI users
- Knowledge graph showing how everything connects
- "We use Pyrite to build Pyrite" — the dogfooding story
- Git workflow for knowledge review (PRs for doc changes)

**Objections to address:**
- "Our team won't use a CLI"
- "How does this integrate with our existing tools?"
- "We already have Confluence/Notion"

---

## 3. Agentic Teams (AI engineers, agent runtime builders)

**Profile:** Developers building autonomous agent systems (OpenClaw, custom Claude Code setups, Codex-based pipelines). Pain points: agents lose context between sessions, no structured memory, no way for agents to validate their own work.

**Hook:** "Persistent, structured, validated memory for your AI agents — and they can build their own extensions for any domain."

**Key capabilities to demo:**
- Self-configuration loop (agent builds extension → tests → installs → populates KB)
- Three-tier MCP permission model (read/write/admin)
- CLI with `--format json` for agent consumption
- QA validation on every write
- Extension scaffolding (`pyrite extension init`)
- Task coordination plugin (when shipped)

**Objections to address:**
- "Can't I just use a vector database?"
- "How does this compare to LangChain memory / MemGPT?"
- "Is this production-ready?"

---

## 4. Python Developers (your network)

**Profile:** Experienced Python devs who appreciate good architecture. May not have an immediate KB need but will recognize quality tooling and see applications.

**Hook:** "A beautifully engineered knowledge platform — 15-method plugin protocol, SQLite FTS5, git-native storage, three-tier MCP. Worth studying even if you never use it."

**Key capabilities to demo:**
- Plugin protocol architecture (runtime checkable, 15 extension points)
- Dual storage model (markdown source of truth, SQLite derived index)
- Test suite (1780+ tests, TDD culture)
- Extension system (pip-installable domain plugins)
- Clean CLI with Click

**Objections to address:**
- "What would I use this for?"
- "Seems over-engineered for notes"

---

## 5. Automation Builders (n8n, Make, Zapier users)

**Profile:** People who build automated workflows connecting services. Pain points: no good "knowledge" node in their pipelines, can't easily store and query structured data from workflows.

**Hook:** "A knowledge base your automations can read and write — structured data in, smart search out."

**Key capabilities to demo:**
- REST API for workflow integration
- MCP tools for AI nodes
- CLI for shell-based automations
- Typed entries mean consistent data structure
- n8n workflow: RSS → Pyrite → search → alert

**Objections to address:**
- "Can't I just use Airtable/Notion API?"
- "Is there an official n8n node?"
