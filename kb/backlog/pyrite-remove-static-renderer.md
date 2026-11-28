---
id: pyrite-remove-static-renderer
type: backlog_item
title: "Remove the static site renderer after sitemap + SEO meta ship"
kind: chore
status: done
priority: low
effort: M
tags: [cleanup, publication, deprecation]
links:
- target: epic-pyrite-publication-strategy
  relation: subtask_of
  kb: pyrite
---

## Problem

`pyrite/services/site_cache.py` (~800 lines plus Jinja templates plus
CLI + tests) pre-renders entries to static HTML files at `/site/*`.
Its original jobs were:

1. SEO-indexable content for crawlers
2. Fast first paint for readers
3. Public unauthenticated read access
4. Static deploy target (CDN-cacheable HTML files)

With the parent publication-strategy epic, these concerns redistribute:

- **Public publication sites** (`capturecascade.org`,
  `detention-pipeline.transparencycascade.org`) move to their own Hugo
  repos that consume Pyrite JSON exports. They handle #2, #3, #4 in
  their own pipelines.
- **Hosted investigation instance**
  (`investigate.transparencycascade.org`) doesn't need #2–#4; it's a
  live editing app. Its one remaining concern is #1 (SEO), which
  [[pyrite-dynamic-sitemap]] + [[pyrite-entry-page-seo-meta]] solve
  natively in the live app.

Once those two tickets ship and at least one production-ish deploy
confirms crawlers index live-app pages successfully, the static
renderer's code can go.

## Scope

**Deferred — do not remove until:**

1. [[pyrite-dynamic-sitemap]] shipped, sitemap valid, robots.txt
   serving
2. [[pyrite-entry-page-seo-meta]] shipped, entry pages emit full
   meta + JSON-LD
3. `capturecascade.org` either:
   - has moved to its own Hugo repo (per the publication-strategy
     epic sub-epic C), OR
   - has an operator-side plan documented for replacing the current
     site-cache pipeline with a JSON-export → Hugo adapter
4. At least 2 weeks of Google Search Console data (or equivalent)
   showing the live app indexes correctly — catch any SEO regression
   before deleting the old path

When ready:

- Delete `pyrite/services/site_cache.py`
- Delete `pyrite/server/templates/base.html`, `search.html`, and any
  other Jinja templates the renderer used (check for other callers
  first — `search.html` is imported by search endpoint)
- Delete `site_cache.*` tests
- Remove the `/site` and `/site/*` routes from the server
- Remove the `site_cache_service` dependency from the API factory
- Remove the "Read" button from `web/src/routes/+page.svelte`
  (currently it links to `/site/{kb.name}`)
- Remove the `render_all` / `render_entry_by_id` / invalidation
  plumbing wired from `kb_service.py` save paths
- Remove the `pyrite site-cache` CLI commands
- Update `docs/deployment/white-labeling.md` — the site-cache
  branded-title discussion becomes historical
- Deprecation note in the release where this lands

## TDD

Not applicable — this is deletion. Verification:

- `rg site_cache` returns no hits except historical entries
- `rg '/site/'` returns no hits except historical entries
- Full backend test suite passes
- Frontend build still succeeds, no dead links

## Risks

- **SEO regression.** Mitigation: the two prerequisite tickets plus
  waiting on 2 weeks of indexing data.
- **capturecascade.org breakage.** Mitigation: don't delete until that
  deploy has moved off site-cache.
- **Unknown callers.** `search.html` imports `base.html`; check
  what else does. Either migrate callers to a simpler template or
  keep the template alive.

## Changes (enumerated for planning)

- `pyrite/services/site_cache.py` — delete
- `pyrite/server/templates/*.html` — delete or audit
- `tests/test_site_cache.py` — delete
- `pyrite/services/kb_service.py` — remove site-cache save hooks
- `pyrite/server/api.py` — remove `/site`, `/site/*` routes
- `pyrite/cli/__init__.py` — remove `site-cache` commands
- `web/src/routes/+page.svelte` — remove "Read" link
- `docs/deployment/white-labeling.md` — remove site-cache branding
  discussion

## Done when

- Code deletions land on `dev`, CI green
- At least one production deploy confirms the live app indexes
  correctly via the dynamic sitemap
- Release notes document the removal and the sitemap+meta
  replacement path
