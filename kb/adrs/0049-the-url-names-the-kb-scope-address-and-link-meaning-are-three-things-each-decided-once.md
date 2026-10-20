---
id: adr-0049
title: "The URL names the KB: scope, an entry's address and a link's meaning are three things, each decided in one place"
type: adr
importance: 4
adr_number: 49
status: accepted
date: '2026-10-15'
tags: [architecture, web, links, wikilinks, kb-context]
links:
- target: adr-0031
  relation: related
  kb: pyrite
- target: adr-0037
  relation: related
  kb: pyrite
- target: adr-0038
  relation: related
  kb: pyrite
---

# ADR-0049: The URL names the KB: scope, an entry's address and a link's meaning are three things, each decided in one place

> **Proposed** (2026-10-15) for #10 and #936, with #912/#910. The maintainer
> accepts or rejects it.

## Context

The web answers "which KB" in three different senses, and today each page
answers each of them in its own way.

- **Scope:** which KBs a list, search, graph or tag page shows. 18 web files
  read it, in six regimes (dev 8e15faa8):
  - the store only: `DailyNote`, `Calendar`, `tags`, `collections`,
    `collections/new`, `entries/wanted`, `entries/new`;
  - the URL, then the store: `entries`, `orient`, `EntryList`;
  - a page-local value defaulting to all KBs: `search`, `graph`, `qa`;
  - a page-local value defaulting to the first KB: `merge-queue`, `changes`;
  - a local value seeded from the store: `entries/clip`;
  - the URL only: `collections/[id]`.

  The store (`web/src/lib/stores/kbs.svelte.ts:22-25`) defaults to a KB named
  `guide` and is not persisted. `KBSwitcher` only sets the store.
- **Address:** which entry a page shows. An id alone is ambiguous across KBs.
  #912 (in review) makes REST refuse an ambiguous id (409 `AMBIGUOUS`) and
  moves every entry link to `entryHref(id, kb)`, which writes
  `/entries/<id>?kb=<kb>`.
- **Link meaning:** which (kb, id) the text `[[x]]`, `[[kb:x]]` or `![[x]]`
  means. The indexer says a bare id means the source entry's KB, and stores a
  `kb:` prefix as written, without mapping a shortname
  (`pyrite/storage/index.py:466-490`). `WikilinkService.resolve_entry`
  maps shortnames, searches every readable KB when no KB is given, and picks
  `LIMIT 1` in its id, title and alias passes
  (`pyrite/services/wikilink_service.py:78-140`). Six web files parse
  wikilink text with their own regex (`wikilink-utils.ts`,
  `codemirror/wikilinks.ts`, `tiptap/markdown.ts`,
  `tiptap/wikilink-extension.ts`, `renderCitations.ts`, `EntryCard.svelte`). None of them knows the host entry's KB, and red
  links are computed from ids without KBs (`wikilink-utils.ts:162`). Two cold
  reads of #912 found this property failing on a new surface each round
  (#936).

## Decision

1. **Three things, never substituted for each other.** Scope is a filter the
   person chooses. An address is an entry's identity. A link's meaning comes
   from the entry the link is written in. The scope never decides an address
   or a link's meaning.

2. **Scope lives in the path, `/kb/<name>/...`, and only there.** One web
   module (`kbScope`) reads it from the URL and builds URLs with it. A route
   without the prefix means every readable KB on a page that can show
   several. On a page that needs one KB (new entry, clip, daily, new
   collection, changes, merge queue), no prefix shows a KB picker; it never
   silently defaults. The switcher navigates: it moves the current page under
   `/kb/<name>/`, or opens `/kb/<name>/entries` from a page that has no
   scope. The store keeps the KB list and no longer holds an active KB. The
   last KB chosen is kept in `localStorage` and is used only to redirect a
   bare `/`. A legacy `?kb=<name>` URL redirects to the path form.

3. **An address is (kb, id): `/kb/<kb>/entries/<id>`.** Every link to an
   entry is built by `entryHref`. A bare `/entries/<id>` with a unique id
   resolves and the page rewrites its URL to the path form (`replaceState`).
   An ambiguous id shows the chooser of #912.

4. **One rule maps wikilink text to (kb, id).** Its inputs are the text, the
   host KB and the caller's readable set.
   - A bare target means the host KB only. If the host KB does not hold it,
     it is missing: a red link, never another KB's entry.
   - A `prefix:target` means the KB with that name or shortname. A prefix
     that names no readable KB is unknown, as today.
   - A title or alias that matches more than one entry is ambiguous and
     answered as ambiguous, never picked.

   On the server the rule is one function, called by the indexer,
   `/entries/resolve` and `/entries/resolve-batch`. `resolve-batch` takes the
   host KB and returns, for each text, `(kb, id)`, `missing` or `ambiguous`.
   The web has one parser, which reads syntax only (prefix, target, heading,
   block, display). The meaning comes from the server, because shortnames
   are the operator's configuration. One table of wikilink texts and
   expected results drives a Python test and a vitest test.

5. **The host KB comes from the address** of the entry being shown or
   edited, which is decision 3. It is never taken from the scope.

## Questions for the maintainer

- **Q1. Query or path?** **Decided: the path, `/kb/<name>/`** (decisions 2
  and 3); the maintainer chose the form a new page cannot forget.
  Recommendation was the query string. #912 already
  moved every entry link to `?kb=`, and the path form (`/kb/<name>/...`)
  rewrites every route. Counter-case: a path makes the KB impossible for a new
  page to forget, which is how the six regimes arose. Decision 2's single
  `kbScope` module, together with a test that fails when a route reads `kb`
  any other way, covers the same risk.
- **Q2. A bare link that only another KB holds.** **Decided: a red link.** Recommendation: a red link
  (decision 4). This is what the indexer already records as a wanted page.
  Counter-case: KBs that were split apart lose links that work today. They
  can be fixed with an explicit prefix, which a qa finding can list.
- **Q3. Shortnames in the index.** **Decided: the full KB name.** Recommendation: the indexer stores the
  KB **name** the prefix resolves to, so backlinks and wanted pages agree with
  resolution. A change of shortname in the config needs a reindex, and
  `index health` reports link rows whose KB is not a KB name. Counter-case:
  store the prefix as written and map it at query time, which avoids staleness
  but puts the rule in every query.
- **Q4. #912's open round.** Recommendation: land #912 with one change: the
  web sends a prefixed target unsplit (`target=kbb:foo`), so the server maps
  the shortname. Everything else of #936 is phase 1 below.

## Alternatives considered

- **Each surface fixes its own split** (#912 rounds 1 and 2). Rejected:
  principle 8. Each round found another surface.
- **The web maps prefixes itself from `/api/kbs` shortnames.** Rejected. That
  puts a second copy of the rule in TypeScript.
- **The store stays the authority and the URL mirrors it.** Rejected. A
  reload, a bookmark or a shared link cannot carry the store.

## Consequences

- Phase 0 is #912 as in Q4. Phase 1 is the server rule and the shared table
  (#936). Phase 2 is the web parser and its six call sites. Phase 3 is
  scope (#10): `kbScope`, the switcher and the 18 readers, the routes moved
  under `/kb/<name>/`, `entryHref` switched to the path form, and a redirect
  from `?kb=` URLs (including links #912 shipped). Phase 3 shares no
  file with phases 1 and 2, so it can run beside them.
- #911 (extension id lookups) and #684/#781 (rename rewrites links by regex)
  should call the server rule once it exists.

## Decided

Accepted by the maintainer (Mark), 2026-10-15, through the desk.
