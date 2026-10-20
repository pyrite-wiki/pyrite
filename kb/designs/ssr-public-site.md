---
id: ssr-public-site
type: design_doc
title: "SSR Public Site: kb.pyrite.wiki via route groups"
tags: [web, seo, publishing, architecture]
date: "2026-03-24"
---

> The built-in public site (`/site`, ADR-0023) was removed by #953 (2026-10). Kept as history.

## Summary

Add a public, read-only, SEO-friendly view of Pyrite KBs alongside the existing app UI. Both run in the same SvelteKit app using route groups, served from the same backend.

## Architecture

```
One SvelteKit app, two route groups:

  /app/...   → Full Pyrite UI (auth, editing, settings)
  /site/...  → Public read-only view (SSR, SEO metadata, content-forward)

Same API, same DB, same data.
```

Caddy/nginx maps subdomains to paths:
- `demo.pyrite.wiki` → `/app`
- `kb.pyrite.wiki` → `/site`

Self-hosters without subdomains get both at `/app` and `/site`.

## Route groups

SvelteKit route groups split the layouts:

```
src/routes/
  (app)/           ← Current UI (moved here)
    +layout.svelte ← Sidebar, auth, full chrome
    entries/
    graph/
    search/
    settings/
    ...
  (site)/          ← Public view (new)
    +layout.svelte ← Minimal chrome, SEO head, no auth
    +page.svelte   ← Landing: KB index with descriptions
    [kb]/
      +page.svelte       ← KB overview (orient-style)
      entries/+page.svelte ← Entry list
      [id]/+page.svelte   ← Entry detail (the main SEO page)
      graph/+page.svelte  ← Graph view
      search/+page.svelte ← Search
    sitemap.xml/+server.ts ← Dynamic sitemap
```

## What the site view renders

**Reuses from app:** Entry content rendering, search, graph, collections, type badges, tag pills, backlinks panel, wikilink resolution.

**Omits:** Sidebar navigation, auth UI, edit/create/delete actions, settings, changes page, daily notes, QA, admin features.

**Adds:**
- "Edit on Pyrite" link on entry pages (configurable target URL)
- SEO `<head>`: title, meta description (from entry summary), Open Graph, Twitter cards
- JSON-LD structured data per entry type (Article, Person, Organization, etc.)
- `sitemap.xml` generated from entry index
- `robots.txt`
- Wider content area, minimal navigation (KB selector, search, graph link)

## SSR

Switch from `adapter-static` to `adapter-node`. The Python backend serves the API; the Node SSR server renders pages and proxies API calls.

Docker setup:
- Python backend on port 8088 (existing)
- Node SSR server on port 3000 (new)
- Caddy routes `/api` → Python, everything else → Node

Or: Node SSR server proxies `/api` requests to the Python backend internally.

## SEO specifics

**Per-entry page:**
- `<title>{entry.title} — {kb.name} | Pyrite</title>`
- `<meta name="description" content="{entry.summary or first 160 chars of body}">`
- `<meta property="og:title/description/type/url">`
- JSON-LD: map entry_type → schema.org type (note→Article, person→Person, organization→Organization, event→Event, source→ScholarlyArticle)
- Canonical URL: `https://kb.pyrite.wiki/{kb}/{entry-id}`

**KB index page:**
- `<title>{kb.name} Knowledge Base | Pyrite</title>`
- Description from kb.yaml or auto-generated
- Entry count, type breakdown

**Sitemap:**
- Dynamic `/sitemap.xml` endpoint
- One URL per entry across all public KBs
- `<lastmod>` from entry updated_at
- Priority: 0.8 for entries, 0.5 for KB indexes

**Cross-KB links:**
- `[[boyd:ooda-loop]]` → `<a href="/site/boyd/ooda-loop">OODA Loop</a>`
- Works because all KBs are on the same domain with path-based routing

## Implementation phases

**Phase 1: Route group split + adapter-node**
- Move current routes under `(app)/`
- Create `(site)/` with layout and entry detail page
- Switch to adapter-node
- Verify app still works at `/app`, site renders at `/site`

**Phase 2: SEO layer**
- Add `+page.server.ts` load functions for SSR data
- Meta tags, Open Graph, JSON-LD
- `sitemap.xml` endpoint
- `robots.txt`

**Phase 3: Content-forward design**
- Minimal navigation for site view
- "Edit on Pyrite" links
- Landing page with KB directory
- Wider content area, reading-optimized typography

**Phase 4: Deployment**
- Docker multi-service: Python API + Node SSR
- Caddy config for kb.pyrite.wiki → /site
- GitHub Action for automated rebuilds on KB content changes

## Configuration

```yaml
# config.yaml
settings:
  public_site:
    enabled: true
    base_url: https://kb.pyrite.wiki
    app_url: https://demo.pyrite.wiki  # for "Edit on Pyrite" links
    excluded_kbs: [pyrite, drafts, personal]
```
