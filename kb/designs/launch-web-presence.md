---
id: launch-web-presence
title: "Launch Plan: Web Presence, Registry & Community"
type: note
tags:
- launch
- marketing
- infrastructure
- web
links:
- launch-plan
- pyrite-website
- demo-site-deployment
- container-deployment
- per-kb-permissions
- personal-kb-repo-backing
lifecycle: archived
---

> **Historical**: March 2026 launch plan.

# Web Presence, Extension Registry & Community

## Three-Layer Web Architecture

See [[pyrite-website]] (backlog item #111) and [[demo-site-deployment]] (backlog item #85) for implementation details.

### Layer 1 — Marketing Site (pyrite.dev)

Static site telling the Pyrite story. Separate repo (`pyrite-website`). Landing page, use cases, how-it-works, plugins page, blog for launch content. Hosted free on GitHub Pages / Netlify / Cloudflare Pages.

Key pages:
- Landing page: elevator pitch, key visuals, call-to-action
- How it works: three portals (CLI, MCP, Web UI)
- Use cases: software teams, investigators, PKM, community hubs, wiki/encyclopedia
- Plugins: links to awesome-plugins-page and extension registry
- Blog: hosts all launch content pieces (resolves the "where to host the blog post" open question)
- Links: demo site, GitHub, Discord, PyPI

### Layer 2 — Docs (pyrite.dev/docs)

Documentation rendered from the Pyrite KB itself — "dogfooding as documentation." Read-only access to the project KB (ADRs, components, standards), getting started tutorial, plugin writing tutorial, API reference. Could be a Pyrite read-only instance or static pages generated from KB markdown.

### Layer 3 — Demo Site (demo.pyrite.dev)

Live Pyrite instance running the full web UI on PostgresBackend:

```
Internet → [Reverse proxy (Caddy)] → [Uvicorn :8088]
                                          ↓
                              [PostgresBackend (knowledge index)]
                              [App state DB]
                              [Static SvelteKit frontend]
```

#### Curated KB Loading (Awesome-List Model)

The demo site loads KBs from curated git repos on the awesome-list — no user-generated content to moderate:

1. Author creates a KB locally, pushes to GitHub
2. Submits to the awesome-list (PR reviewed for quality)
3. Demo site pulls the repo, indexes it, makes it searchable

**Launch KBs:** Journalism KBs (CaptureCascade), Pyrite's own KB.

**Post-launch:** Community KBs accumulate as the awesome-list grows. Visitors see breadth without us building everything.

#### Access Model

See [[per-kb-permissions]] (#112) and [[personal-kb-repo-backing]] (#113) for full design.

| Role | Access | Auth |
|------|--------|------|
| Anonymous | Browse, search, explore graph on curated KBs | No |
| Registered (local) | All anonymous + create one ephemeral KB sandbox (private, 24h TTL) + BYOK AI features | Yes (local or GitHub OAuth) |
| Registered (GitHub OAuth) | All above + connect a public GitHub repo to make KB permanent | Yes (GitHub OAuth) |
| Admin | Manage curated KBs, user roles, KB permissions | Yes |

**User funnel:** Anonymous browsing → register → ephemeral sandbox → invest time building a KB → connect GitHub repo → permanent personal KB. Each step upgrades commitment.

Ephemeral KB policy is configurable per deployment (see [[per-kb-permissions]]): `ephemeral_min_tier`, `ephemeral_max_per_user`, `ephemeral_default_ttl`. Usage tiers (see [[personal-kb-repo-backing]]) let the operator set resource limits per tier (max KBs, max entries, storage, rate limits).

#### Cost Model

No AI inference server-side. All AI features are BYOK. Hosting costs: compute + Postgres only. ~$6/month on a basic VPS. For context: Notion Team costs $10/user/month — a 5-person team on Pyrite runs on a single $6 VPS with unlimited users.

### Content needed:
- [ ] Set up `pyrite-website` repo with static site generator
- [ ] Domain acquisition (pyrite.dev or alternative)
- [ ] Landing page design and content
- [ ] Docs section — decide: static generation vs Pyrite read-only instance
- [ ] Production Dockerfile + `docker-compose.yml` + `docker-compose.prod.yml` — see [[container-deployment]] (#114)
- [ ] Deploy-to buttons: `railway.json`, `render.yaml`, `fly.toml`
- [ ] Environment variable configuration for auth, AI keys, data dir (no config file editing)
- [ ] KB seeding pipeline (clone awesome-list repos, index, serve)
- [ ] CI pipeline to rebuild on site or KB updates

---

## Extension Registry & Public KB Directory

### Concept

A Pyrite KB whose entries are Pyrite extensions and public knowledge bases. This eats its own dog food: the registry is itself a knowledge base, searchable through the same tools it catalogs.

### Why This Matters

- **Discovery**: Users find extensions and KBs through the same search they use inside Pyrite
- **Demo value**: The registry itself demonstrates Pyrite's capabilities
- **Network effects**: Every new extension or public KB makes the ecosystem more valuable
- **Dogfooding**: Proves the system works for real catalog/directory use cases

### Extension Registry Schema

```yaml
# Entry type: extension
type: extension
fields:
  name: string (required)
  description: string (required)
  repo_url: string (required) # GitHub/GitLab repo URL
  author: string
  license: string
  pypi_package: string # if published to PyPI
  entry_types: list[string] # types this extension provides
  mcp_tools: list[string] # MCP tools this extension adds
  pyrite_version: string # minimum compatible version
  install_command: string # e.g. "pip install pyrite-legal"
  status: enum[experimental, stable, maintained, archived]
tags: # category tags
  - domain (legal, scientific, security, media, etc.)
  - capability (types, validators, workflows, tools)
```

### Public KB Directory Schema

```yaml
# Entry type: public_kb
type: public_kb
fields:
  name: string (required)
  description: string (required)
  repo_url: string (required)
  author: string
  license: string
  entry_count: integer
  kb_type: string # generic, software, research, encyclopedia, etc.
  extensions_used: list[string] # links to extension entries
  topics: list[string]
  last_updated: date
  status: enum[active, archived, snapshot]
```

### Seed Content

Extensions to list at launch (even if first-party):
- `pyrite-zettelkasten` — Zettelkasten extension (ships with Pyrite)
- `pyrite-social` — Social/engagement extension
- `pyrite-encyclopedia` — Encyclopedia workflows
- `pyrite-software-kb` — Software team KB (ADRs, components, backlog)

Public KBs to list:
- Pyrite's own `kb/` — the meta-KB
- 4800-event timeline
- Any community datasets built for demo (Awesome Python, RFCs, etc.)

### Implementation

This could be:
1. **A GitHub repo** with markdown entries following the schemas above — simplest, works immediately
2. **A section of the demo site** — browse extensions and KBs through the web UI
3. **Both** — the repo IS the KB, the demo site indexes it

The "awesome list" approach (option 1) is the fastest to launch. A simple README with a table, plus individual markdown entries for each extension/KB that Pyrite can index. Over time, this becomes a proper Pyrite KB that demonstrates the tool's own capabilities.

### Content needed:
- [ ] Create `pyrite-registry` repo (or directory within main repo)
- [ ] Define extension and public_kb entry type schemas
- [ ] Seed with first-party extensions and KBs
- [ ] Add to demo site
- [ ] README with contribution guidelines ("list your extension here")
- [ ] Consider: should this be a Pyrite extension itself? (meta!)

---

## Discord Community

### Channel Structure

| Channel | Purpose |
|---------|---------|
| #announcements | Release notes, blog posts, new videos |
| #general | Discussion |
| #getting-started | Install help, first-time questions |
| #extensions | Building and sharing extensions |
| #agent-builders | Agentic use cases, MCP integration, OpenClaw etc. |
| #pkm | Personal knowledge management, Obsidian migration |
| #showcase | Share your KBs, extensions, workflows |
| #feedback | Bug reports, feature requests (supplement to GitHub issues) |
| #dev | Contributing to Pyrite core |

### Setup timing

Set up 1-2 weeks before launch. Link in README, blog post, and all channel posts. Include invite link in `pip install` post-install message if possible.
