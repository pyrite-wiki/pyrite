---
id: launch-staging
title: "Launch Plan: Staging & Sequencing"
type: note
tags:
- launch
- marketing
- staging
links:
- launch-plan
- launch-messaging
- launch-channels
- roadmap
- per-kb-permissions
- personal-kb-repo-backing
- container-deployment
- demo-site-deployment
---

# Staging & Sequencing (four waves, ~2 pieces/week within each)

See [[launch-channels]] for detailed content specs per channel. See [[launch-messaging]] for messaging foundation.

## Wave 1: Platform Launch (0.16 release)

**Message:** "Pyrite turns your AI into a domain expert."
**Primary audience:** Agent builders (OpenClaw, Claude Code, Codex, CrewAI), Claude Desktop MCP users

### Pre-Launch (2-3 weeks before)

**Infrastructure (milestones 0.12–0.16 must complete in order):**
- [x] PyPI publish (#74) — `pip install pyrite` works [0.12 ✅]
- [ ] Web UI hardening — all UI bugs fixed, Playwright tests pass [0.13]
- [ ] GitHub OAuth (#110) — "Sign in with GitHub" [0.14]
- [ ] Per-KB permissions (#112) — ephemeral KB sandboxes, per-KB ACL [0.14]
- [ ] Rate limiting (#97) — required for public-facing endpoints [0.14]
- [ ] Container deployment (#114) — Dockerfile, `docker-compose.yml`, deploy buttons [0.15]
- [ ] Deploy demo site (#85) — depends on auth + containers [0.15]
- [ ] Plugin repo extraction (#107) — extensions installable from PyPI [0.16]
- [ ] Personal KB repo backing (#113) — connect GitHub repo, usage tiers [0.16]

**Content & community (launch-blocking):**
- [ ] Write wave 1 blog post with "Try it now" demo link above the fold, get 2-3 people to review
- [ ] Finalize tutorial, verify install flow on clean machine (both pip and Docker paths)
- [ ] Prepare demo KBs (timeline, Pyrite's own KB) loaded on demo site
- [ ] Set up Discord server (channels: #general, #getting-started, #extensions, #agent-builders, #feedback)
- [ ] Draft HN title + first comment, Reddit posts, email template
- [ ] Record launch-day videos: Claude Desktop demo (broadest audience), full self-configuration loop (BHAG in action)
- [ ] Prepare social media assets (GIFs from demo videos, knowledge graph screenshots)

**Content (week 1-2, not launch-blocking):**
- [ ] Update [[mcp-submission]] (`kb/designs/mcp-submission.md`), submit to directory
- [ ] Set up YouTube channel, upload launch-day videos
- [ ] Seed the extension registry KB and public KB directory
- [ ] Record Deploy Your Own video (Docker compose demo for r/selfhosted)
- [ ] Record Fork & Contribute video (fork → edit → PR workflow)

### Launch Day (Tuesday or Wednesday, ~10am ET)

- [ ] Publish wave 1 blog post
- [ ] Submit to HN ("Show HN")
- [ ] Send personal emails to Python developer network
- [ ] Tweet/post thread with GIFs

### Week 1 (~3 pieces, focus: core audience)

- [ ] Post to r/ClaudeAI (MCP integration, Claude Desktop demo)
- [ ] Post to r/Python (architecture deep-dive, dogfooding story)
- [ ] Publish Claude Desktop demo video

### Week 2 (~3 pieces, focus: deployment + broader reach)

- [ ] Post to r/selfhosted (Docker deployment, $6/month pitch, Deploy Your Own video)
- [ ] Post to r/programming (architecture deep-dive angle)
- [ ] Publish on dev.to (adapted blog post)

### Week 3-4 (~2 pieces/week, focus: secondary audiences)

- [ ] Post to r/LocalLLaMA (agent memory, local-first)
- [ ] Post to r/artificial (agent-first knowledge infrastructure, BHAG)
- [ ] Publish Fork & Contribute video
- [ ] LinkedIn post (professional framing, "we've been building...")

### Wave 1 Activation Metric

Someone connects Pyrite via MCP or CLI to their agent runtime and creates entries programmatically. Track: `pyrite create --format json` invocations, MCP `kb_create` tool calls, demo site signups, sandbox KB creations.

---

## Wave 2: Software Project Plugin (1-2 weeks after wave 1)

**Message:** "Your software project, managed by agents that actually understand it."
**Primary audience:** Dev teams using Claude Code/Codex, engineering leads frustrated with doc rot

### Content

- [ ] Wave 2 blog post: "We built a software project management tool on Pyrite in [X days] — here's what agents can do with your architecture docs, backlog, and ADRs"
- [ ] Demo video: agents navigating Pyrite's own project KB — claiming tasks, updating status, linking evidence
- [ ] Getting started guide specific to the software plugin

### Distribution (~2 pieces/week)

- [ ] HN follow-up post (new content, not a repost)
- [ ] r/programming, r/Python (dev tools angle, dogfooding story)
- [ ] r/ExperiencedDevs, r/softwarearchitecture (ADR management, doc rot)
- [ ] LinkedIn post ("we use Pyrite to build Pyrite")
- [ ] Email wave 2 to Python network ("here's what we built on it")

### Wave 2 Activation Metric

A dev team installs the software plugin and creates 20+ entries across multiple types (ADRs, components, backlog items) for their actual project.

---

## Wave 3: Investigative Journalism Plugin (1-2 weeks after wave 2)

**Message:** "Follow the money — investigative research powered by structured knowledge and AI."
**Primary audience:** Journalists, OSINT researchers, investigative teams, and the broader public that finds this kind of work fascinating

### Content

- [ ] Wave 3 blog post: "Building an investigative journalism tool with structured knowledge and AI workflows"
- [ ] Demo video: knowledge graph showing entity relationships, money flows, evidence chains
- [ ] Example KB: curated public investigation dataset demonstrating the plugin

### Distribution (~2 pieces/week)

- [ ] HN (this is the post that goes viral — "follow the money" is inherently dramatic)
- [ ] r/OSINT, journalism-focused communities
- [ ] Twitter/X journalism circles, GIJN network
- [ ] r/artificial, r/programming (different-domain proof)
- [ ] dev.to follow-up

### Wave 3 Activation Metric

Someone builds a KB for a real investigation or research project and shares it (publicly or with collaborators).

---

## Wave 4: PKM Capture Plugin (after waves 1-3, when capture pipeline is built)

**Message:** "Capture anything. Pyrite turns it into structured knowledge."
**Primary audience:** PKM power users (Obsidian, Anytype, Logseq), anyone who collects and organizes information

This wave is the widest aperture. By this point, three shipping plugins prove the platform works. The PKM crowd hears: "software teams, journalists, and AI agents all use this — and now it captures your web clippings, voice notes, and photos too."

### Prerequisites (must ship before wave 4 launch)

- [ ] PKM capture plugin built: ingest → classify → extract → create typed entry pipeline
- [ ] Capture endpoints: image (OCR/vision), URL (extract + summarize), voice (transcribe), text (classify)
- [ ] Mobile-friendly web UI (responsive, quick-capture interface)
- [ ] Claude app integration for capture (if feasible)
- [ ] BYOK AI in UI working for interactive classification and querying

### Content

- [ ] Wave 4 blog post: "Your AI organizes your knowledge for you — snap, clip, dictate, and Pyrite turns it into structured entries"
- [ ] Demo video: phone capture → auto-classification → knowledge graph shows connections → search finds it later
- [ ] Obsidian migration guide: point Pyrite at your existing vault, get instant search + graph + validation
- [ ] Getting started guide specific to PKM workflow (no CLI required)

### Distribution (~2 pieces/week)

- [ ] r/ObsidianMD, r/PKMS, r/Zettelkasten (this is their home turf)
- [ ] r/productivity, r/selfhosted
- [ ] HN post (new angle: "we built a PKM tool where AI does the filing")
- [ ] PKM YouTube/blog community outreach (Linking Your Thinking, etc.)
- [ ] Twitter/X PKM circles
- [ ] dev.to / Medium (adapted blog post)

### Wave 4 Activation Metric

Someone migrates an existing Obsidian/markdown vault into Pyrite and uses the capture pipeline to add 10+ entries via web/mobile in their first week.

---

## Ongoing (across all waves)

- [ ] Monitor MCP directory listing traffic
- [ ] Respond to GitHub issues and Discord from new users
- [ ] Iterate on tutorial based on friction points
- [ ] Collect testimonials / usage stories
- [ ] Record remaining demo videos as time permits (n8n, corporate KB, web UI tour, AI in UI, manual walkthrough)
- [ ] Publish new extensions and public KBs to the registry
- [ ] Post to secondary communities (r/n8n, r/ExperiencedDevs) as relevant content exists
