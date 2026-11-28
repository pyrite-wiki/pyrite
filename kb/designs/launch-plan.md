---
id: launch-plan
title: "Launch Plan: Content Matrix & User Acquisition Strategy"
type: note
tags:
- launch
- marketing
- strategy
links:
- roadmap
- bhag-self-configuring-knowledge-infrastructure
- launch-messaging
- launch-user-types
- launch-channels
- launch-staging
- launch-web-presence
lifecycle: archived
---

> **Historical**: March 2026 launch plan.

# Launch Plan: Content Matrix & User Acquisition Strategy

Target: 0.16 launch release. Milestones 0.13-0.16 build toward launch day: UI hardening (0.13), auth & rate limiting (0.14), deployment & demo (0.15), ecosystem & onboarding (0.16). See [[roadmap]] for milestone details.

This document is the overview. Detailed content lives in linked sub-documents:

| Section | Document | What It Covers |
|---------|----------|----------------|
| Messaging | [[launch-messaging]] | Core insight, elevator pitches, pain story, objection responses, BHAG pitch, per-wave messaging, three portals narrative |
| User types | [[launch-user-types]] | Five audience profiles with hooks, demo priorities, and objections |
| Channels | [[launch-channels]] | HN, Reddit, blog post structure, video demos, tutorial, email, dev.to, MCP directory, social media |
| Staging | [[launch-staging]] | Four-wave rollout with checklists, pre-launch infrastructure, weekly distribution plan |
| Web presence | [[launch-web-presence]] | Three-layer web architecture (pyrite.dev / docs / demo site), extension registry, Discord community |

---

## Demo Datasets & KBs

Pre-built KBs for impressive demos:

| Dataset | Source | Entry Count | Best For |
|---------|--------|-------------|----------|
| **4800-event timeline** | Already built | ~4800 | Claude Desktop demo, search impressiveness, PKM intro |
| **Pyrite's own KB** | This repo's `kb/` | ~200 | "We use it to build it", Claude Code demo |
| **Awesome Python** | awesome-python GitHub | ~1600 | Python dev outreach, search demo ("find me async HTTP libs") |
| **CVE/vulnerability data** | NIST NVD JSON feeds | 1000+ (curated subset) | Security angle, corporate KB demo |
| **RFC summaries** | IETF | ~500 (curated) | Knowledge graph demo (RFCs reference each other heavily) |
| **Python top packages** | PyPI JSON API | ~500 | Python dev outreach, dependency graph visualization |

**Content needed:**
- [ ] Import scripts for each dataset (in `examples/` directory)
- [ ] Curated subsets that demo well (not raw dumps)
- [ ] Each dataset has a matching `--template` or schema definition
- [ ] Verify knowledge graph looks good with each dataset

---

## Content × Channel × Audience Matrix

Which content serves which channel for which audience:

| Content Piece | HN | Reddit | Blog | Email | MCP Dir | Social |
|--------------|-----|--------|------|-------|---------|--------|
| Blog post | ✓ (link) | ✓ (link) | ✓ (primary) | ✓ (link) | | ✓ (link) |
| PKM intro video | | r/ObsidianMD | embedded | | | ✓ |
| Corporate KB intro video | | r/programming | embedded | | | ✓ (LinkedIn) |
| Agentic teams intro video | ✓ (in comment) | r/LocalLLaMA, r/ClaudeAI | embedded | | | ✓ |
| Claude Desktop demo | ✓ (in comment) | r/ClaudeAI | embedded | | ✓ | ✓ |
| Claude Code demo | | r/programming, r/Python | embedded | ✓ | | |
| Deploy your own demo | ✓ (in comment) | r/selfhosted | embedded | | | ✓ |
| Fork & contribute demo | ✓ (in comment) | r/programming | embedded | | | ✓ |
| n8n demo | | r/n8n | linked | | | |
| Web UI tour | | all | embedded | | | ✓ |
| AI in UI demo | | r/artificial | embedded | | | ✓ |
| Manual UI walkthrough | | | linked | | | |
| Full self-config loop | ✓ (in comment) | r/LocalLLaMA | embedded | ✓ | | ✓ |
| Getting Started tutorial | | all (linked) | linked | ✓ | ✓ | |
| Architecture deep-dive | ✓ (in comment) | r/programming, r/Python | section | | | |
| Import scripts / example KBs | | r/Python | linked | ✓ | | |

---

## Success Metrics

| Metric | Target (Week 1) | Target (Month 1) |
|--------|-----------------|-------------------|
| GitHub stars | 100 | 500 |
| PyPI installs | 200 | 1000 |
| HN points | 50+ | — |
| MCP directory installs | 50 | 200 |
| Blog post views | 2000 | 5000 |
| Active users (any CLI/MCP activity) | 20 | 100 |
| GitHub issues from external users | 10 | 30 |
| Extensions built by external users | 1 | 5 |
| Demo site unique visitors | 500 | 2000 |
| Demo site GitHub OAuth signups | 20 | 100 |
| Ephemeral sandboxes created | 10 | 50 |
| KB forks (curated KB repos) | 5 | 20 |
| Docker pulls / deploy-button deploys | 10 | 50 |

---

## Open Questions

- ~~Where to host the blog post?~~ **Resolved:** Blog section on pyrite.dev (the marketing site)
- YouTube vs embedded video hosting?
- Pricing/licensing messaging — is MIT sufficient or does it need explicit "free forever" language?
- Should we target Product Hunt as well? (probably week 3-4, not launch day)
- Is there a launch week "challenge" angle? ("Build a KB for your domain in 10 minutes")
- ~~Should the extension registry be its own repo or a directory in the main repo?~~ **Resolved:** Awesome-list approach first (curated markdown), evolves into full registry (#84) in 0.13
- ~~Demo site domain name?~~ **Resolved:** demo.pyrite.dev for the live demo, pyrite.dev for the marketing site
- ~~Demo site content moderation?~~ **Resolved:** No user-generated content. Demo loads curated awesome-list KBs only. Users publish their own KBs via git repos.
- ~~Demo site AI costs?~~ **Resolved:** All AI features are BYOK. No server-side inference. Hosting is compute + Postgres only.
