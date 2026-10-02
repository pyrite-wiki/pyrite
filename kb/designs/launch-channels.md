---
id: launch-channels
title: "Launch Plan: Channels & Content Types"
type: note
tags:
- launch
- marketing
- content
links:
- launch-plan
- launch-messaging
- launch-user-types
- launch-staging
---

# Channels & Content Types

Detailed content specs for each distribution channel. See [[launch-messaging]] for the shared messaging foundation and [[launch-user-types]] for audience profiles.

## A. Hacker News

**Audience mix:** Agentic teams (40%), Python devs (30%), PKM power users (20%), curious (10%)

**Content needed:**
- [ ] HN submission title (< 80 chars, technical, intriguing)
- [ ] Blog post to link (the primary content piece)
- [ ] First comment / "Show HN" description (technical depth, architecture highlights)

**Messaging angle:** Lead with the technical insight and contrarian bet. "Knowledge-as-Code for a world of agent swarms. Git, not CRDTs. Schema, not free text. Designed for AI agents as primary consumers."

**Timing:** Tuesday or Wednesday, ~10am ET. Avoid major tech news days.

**Draft titles (pick/refine one):**
- "Show HN: Pyrite – Knowledge-as-Code platform where AI agents are first-class citizens"
- "Show HN: Pyrite – A knowledge base system designed for agent swarms, built on git and markdown"
- "Show HN: Pyrite – Structured knowledge infrastructure for humans and AI agents"

---

## B. Reddit

**Subreddits and angles:**

| Subreddit | Angle | Content |
|-----------|-------|---------|
| r/programming | Architecture deep-dive | Blog post + architecture diagrams |
| r/Python | "Show r/Python" — clean codebase, plugin protocol | Blog post + code walkthrough |
| r/LocalLLaMA | MCP integration, local-first, agent memory | Claude Desktop demo video |
| r/ClaudeAI | MCP tools, Claude Desktop integration | Claude Desktop demo + tutorial |
| r/ObsidianMD | Obsidian vault upgrade path, knowledge graph | PKM demo video + migration guide |
| r/selfhosted | `docker compose up` → $6/month, unlimited users, you own your data | Deploy video + Docker guide |
| r/artificial | Agent-first knowledge infrastructure | BHAG blog post |
| r/n8n | Workflow integration | n8n demo video + workflow template |

**Content needed (per subreddit):**
- [ ] Custom post title and intro paragraph for each subreddit
- [ ] Subreddit-specific demo video or link to relevant section of blog

---

## C. Blog Post (Central Content Piece)

**Audience:** All user types; this is what HN, Reddit, and email link to.

**Structure:**

1. **The thesis** (2 paragraphs) — Knowledge management is shifting from human to agent consumers. What does knowledge infrastructure look like in a world of agent swarms?
2. **The problem** (2 paragraphs) — Agents today lose context between sessions. Corporate KBs rot in Confluence. PKM tools don't validate or connect. No tool is designed for programmatic knowledge production.
3. **Introducing Pyrite** (2 paragraphs + Knowledge-as-Code table) — Knowledge-as-Code: markdown files with YAML frontmatter in git. Schema validation. Full-text + semantic search. Three-tier MCP server. Plugin system with 15 extension points.
4. **Try it now** (1 paragraph + prominent link) — demo.pyrite.dev lets you browse curated KBs, explore the knowledge graph, and search — no install needed. Sign in with GitHub to create your own sandbox KB. This section must be above the fold for HN/Reddit readers who skim.
5. **Demo: The self-configuration loop** (with CLI output / GIFs) — Agent scaffolds extension → tests it → installs → provisions KB → populates → QA validates
6. **Architecture highlights** (diagram + 3-4 paragraphs) — Dual storage, plugin protocol, MCP tiers
7. **Web UI showcase** (screenshots) — Knowledge graph, entry editor, search, QA dashboard
8. **Deploy your own** (2 paths) — `pip install pyrite` for developers, `docker compose up` for teams. Deploy buttons for Railway/Render/Fly. The pitch: "Notion Team costs $10/user/month. Pyrite on a $6 VPS: unlimited users, you own your data."
9. **Contribute to KBs like open source** (1 paragraph + GIF) — Fork a curated KB, edit it, submit a PR. Knowledge bases get the same contribution model as code. KBs accumulate GitHub forks as social proof.
10. **Getting started** (5 commands) — pip install → init → create → search → connect MCP
11. **What's next** (2 paragraphs) — Roadmap, plugin waves, BHAG vision
12. **Links** — GitHub, demo site, docs, MCP directory listing, demo videos, Discord

**Content needed:**
- [ ] Full blog post draft
- [ ] Architecture diagram (can be mermaid or SVG)
- [ ] Knowledge-as-Code comparison table (already exists in VISION.md)
- [ ] CLI output screenshots/GIFs for self-configuration loop
- [ ] Web UI screenshots (knowledge graph, editor, search)
- [ ] "Getting started" code block verified against actual install flow (both pip and Docker paths)
- [ ] Demo site link + screenshot of the sandbox experience
- [ ] GIF of fork → edit → PR workflow

---

## D. Video Demos

### Audience-Specific Intros (~90 seconds each)

| Video | Target | Hook | Key Scenes |
|-------|--------|------|------------|
| **PKM Intro** | Obsidian/Anytype users | "Your markdown vault, supercharged" | Existing markdown folder → `pyrite init` → instant search → knowledge graph → MCP chat with your KB |
| **Corporate KB Intro** | Confluence/Notion teams | "Version-controlled, validated knowledge" | Typed entries (ADRs, runbooks) → QA validation catching stale docs → web UI browse → git diff of knowledge changes |
| **Agentic Teams Intro** | AI engineers | "Agents that build their own knowledge infrastructure" | Empty directory → agent scaffolds extension → tests pass → KB populated → structured queries work |

### Integration Demos (~2-3 minutes each)

| Video | Target | What It Shows |
|-------|--------|---------------|
| **Claude Desktop** | Broadest audience | Connect MCP → natural language queries against timeline KB → create entries through conversation → knowledge graph updates live |
| **Claude Code** | Developers | Navigate Pyrite's own KB to understand the codebase → search ADRs → find backlog items → "we use it to build it" |
| **Deploy Your Own** | Self-hosters, teams | `docker compose up` → working instance in 90 seconds → register → create KB → "$6/month, unlimited users" |
| **Fork & Contribute** | Developers, KB maintainers | Browse curated KB → Fork & Edit → make changes → Submit PR → upstream maintainer reviews with `pyrite ci` |
| **n8n Workflow** | Automation builders | RSS feed → n8n → Pyrite API → create typed entries → scheduled QA validation → alert on issues |
| **Web UI Tour** | All (especially non-CLI) | Create entry → rich editor → link entries → knowledge graph exploration → search → collections → QA dashboard |
| **AI in UI** | All | AI-assisted search → suggested connections → content generation within editor |
| **Manual UI Walkthrough** | Skeptics, evaluators | Pure feature tour without AI: create, edit, search, filter, graph, collections. "This is a real product." |

### Extended Demo (~5 minutes)

| Video | Target | What It Shows |
|-------|--------|---------------|
| **Full Self-Configuration Loop** | HN, agent builders | Start from nothing → agent discovers domain → builds extension with TDD → installs → provisions KB → populates 100 entries → QA validates → web UI shows the result. The BHAG in action. |

**Content needed:**
- [ ] Script/storyboard for each video
- [ ] Screen recording setup (clean terminal, clean browser, consistent fonts)
- [ ] Demo KBs prepared for each video (timeline, software-team, legal example, etc.)
- [ ] Voiceover or text overlay plan
- [ ] Hosting: YouTube channel? Embedded in blog post?

---

## E. Tutorial / Getting Started Guide

**Audience:** Anyone who clicks through from a channel and wants to try it.

**Structure:**

1. **Install** (30 seconds) — `pip install pyrite` or `docker compose up`
2. **Initialize a KB** (1 minute) — `pyrite init --template software`
3. **Create entries** (2 minutes) — CLI and/or web UI
4. **Search** (1 minute) — FTS and semantic
5. **Connect MCP** (2 minutes) — Claude Desktop configuration + first query
6. **Explore the web UI** (2 minutes) — Knowledge graph, editor, search
7. **Next steps** — Links to extension building, API docs, example KBs

**Content needed:**
- [ ] Tutorial text (in docs/ or standalone page)
- [ ] Verified install-to-working flow on clean machine (both pip and Docker paths)
- [ ] MCP configuration snippet for Claude Desktop
- [ ] Example queries that produce impressive results with the template KB

---

## F. Email (Direct Outreach)

**Audience:** Python developers in your network.

**Content needed:**
- [ ] Personal email template (short, not marketing-speak)
- [ ] Subject line that gets opened
- [ ] 3-sentence pitch + link to blog post
- [ ] Ask: "try it and tell me what breaks" (beta-tester framing, not product launch)

**Messaging angle:** "I've been building this for a while, would love your eyes on it. It's a knowledge base platform designed for AI agents — think Obsidian meets MCP. Here's the blog post, here's the repo."

---

## G. Dev.to / Hashnode / Medium

**Audience:** Developers discovering through search (long-tail SEO).

**Content needed:**
- [ ] Adapted version of the blog post (platform-native formatting)
- [ ] Tags: python, ai, knowledge-management, mcp, claude, developer-tools
- [ ] Published 1-2 days after HN launch (catches the search traffic)

---

## H. MCP Directory

**Audience:** Claude Desktop users browsing for MCP integrations.

**Content needed:**
- [ ] Updated [[mcp-submission]] (`kb/designs/mcp-submission.md`) (accurate tool count, test count, config examples)
- [ ] Submission to Anthropic's MCP directory (timing: before launch)
- [ ] Clear install instructions in the listing

---

## I. Social Media (Twitter/X, Bluesky, LinkedIn)

**Content needed:**
- [ ] Thread format: 5-7 tweets telling the story (thesis → problem → solution → demo GIF → link)
- [ ] GIFs extracted from demo videos (knowledge graph, CLI in action, search results)
- [ ] LinkedIn post (more professional framing, "we've been building...")
