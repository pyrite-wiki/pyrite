---
id: epic-capturecascade-org-and-pyrite-web-ux-overhaul
title: 'Epic: CaptureCascade.org and Pyrite Web UX Overhaul'
type: backlog_item
tags:
- epic
- web
- ux
- cascade
importance: 5
kind: epic
status: done
priority: critical
effort: XL
rank: 0
---

## Overview

Comprehensive UX overhaul driven by site review of capturecascade.org and Pyrite web frontend code audit. Covers bugs, viewer UX, navigation/filtering, mobile responsiveness, SEO, and Substack integration.

Two reviews performed (2026-04-01): code audit of web/ SvelteKit app, and live site review of capturecascade.org via browser extension.

## Scope

22+ items organized into waves:

### Wave 1: Critical Bugs (blocks credibility)
- Frontmatter type field leaking into rendered body text
- About & Methodology page not rendered
- Edit on Pyrite links broken/exposed on public site
- Event count mismatch (4,776 vs 4,529)
- Source citations claimed but not surfaced

### Wave 2: Core UX (highest user impact)
- Tags not clickable in viewer or timeline
- Search/viewer state not in URL (not shareable)
- Mobile viewer layout broken
- Entry detail metadata hidden on mobile
- No actor/participant linking

### Wave 3: Filtering & Navigation
- Tag filter/facet sidebar in viewer and web app
- Actor search/browse
- Timeline viewer improvements (jump-to-date, dense period handling)
- Sidebar search input, persistent filters

### Wave 4: Content Connections
- Substack article links on event pages
- Related events navigation
- Event detail slide-out panel in viewer

### Wave 5: SEO & Polish
- og:image generation
- twitter:card meta tags
- JSON-LD author/publisher
- Favicon
- Loading skeletons

## Decision Required

Some items are Pyrite-general (tags, search URL, mobile, filtering). Others are Cascade-specific (Substack links, event count, About page). Cascade-specific items should be tracked separately or flagged as cascade-only.
