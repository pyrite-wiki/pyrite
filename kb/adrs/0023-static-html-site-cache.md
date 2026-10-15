---
id: adr-0023
type: adr
title: "Static HTML Site Cache for SEO-Friendly KB Pages"
adr_number: 23
status: accepted
deciders: ["markr"]
date: "2026-03-25"
tags: [architecture, site-cache, seo, deployment]
---

> Removed by #953 (2026-10): `/site`, `/viewer`, the sitemap routes and `SiteCacheService` are gone, and an instance has no crawlable surface; a KB is made crawlable by a Hugo or Astro site built from its files (the maintainer's decision on #953). Kept as history.

## Context

Pyrite KBs with 5,000+ entries need to be crawlable by search engines. The SvelteKit SPA renders client-side only — invisible to crawlers. We need server-rendered HTML at `/site/` URLs with proper SEO metadata (sitemap, JSON-LD, canonical URLs, Open Graph).

## Options Considered

1. **Quartz static site export** — Export KB to Quartz and deploy separately. Rejected: no cross-KB wikilink resolution, separate deployment pipeline, stale data until re-exported.

2. **SvelteKit SSR with adapter-node** — Run SvelteKit in SSR mode to server-render `/site/` routes. Tried and abandoned: required Node.js in the Docker container alongside Python, complex route-group configuration, `ssr=false` in root layout blocked child route SSR.

3. **Python-served static HTML cache** — Pre-render all entries to HTML files using Python templates, serve via FastAPI. Cache invalidated on index sync. Progressive JS for search, TOC, heading anchors.

## Decision

Option 3: Python-served static HTML cache via `SiteCacheService`.

## Implementation

- `pyrite/services/site_cache.py` — `SiteCacheService` renders entries to `data/site-cache/` as HTML files
- `pyrite/server/static.py` — `mount_site_routes()` serves cached files at `/site/`, plus sitemap.xml, robots.txt, and a dedicated search page
- Cache rebuilt on `index sync` when entries change (runs in background thread via `asyncio.to_thread`)
- Also available via `POST /api/site/render` admin endpoint and UI button in settings

## Consequences

### Positive
- No Node.js dependency — pure Python rendering
- SEO-complete: sitemap, JSON-LD, canonical URLs, OG tags, reading time
- Fast serving: static files with `Cache-Control: public, max-age=3600`
- Progressive enhancement: pages work without JS, search/TOC added via JS
- Cross-KB wikilinks resolve correctly (unlike Quartz export)
- Custom homepage support via `_homepage` entries

### Negative
- HTML/CSS/JS templates are inline Python strings (807 lines) — no syntax highlighting or hot-reload. Should be extracted to Jinja2 templates (tracked: `extract-site-cache-templates`).
- Stale for up to 1 hour between renders (acceptable for KB content)
- N+1 query pattern for backlinks/outlinks per entry (partially mitigated, tracked for batch DB method)

### Neutral
- Pages are not dynamic — no per-user personalization on `/site/`
- Search on `/site/search` still hits the live API (not static)
