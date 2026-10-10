---
id: spike-a-court-records-extension-case-references-docket-search-and-watch-through
title: 'Spike: a court-records extension (case references, docket search and watch through evidence-search and CourtListener/RECAP)'
type: backlog_item
tags:
- extension
- plugin
- journalism
- research
importance: 5
kind: spike
status: proposed
priority: medium
effort: M
rank: 0
---

Maintainer direction, 2026-10-10: a research KB that cites court records should be able to search and watch them from inside Pyrite. The idea is an extension that knows case references, uses evidence-search (and through it CourtListener and RECAP, run by Free Law Project) as the search backend, and exposes that search through the KB's own CLI, REST, MCP and UI. The journalism-investigation extension needs it, but so might legal offices, law students, and organizers who follow dockets. This spike decides whether it is its own extension and what its contract is.

## The question

Is "court records" a separate extension that journalism-investigation depends on, or a part of journalism-investigation? And what are its types, its protocol, and its boundary with evidence-search?

Starting recommendation, to be tested: its own extension. The users named above want case references and docket watches without the journalism types, and plugins are moving out of tree ([[spike-plugins-out-of-tree-import-inventory-and-the-public-plugin-contract]]), so a small one with a real outside audience is a good second pilot.

## What a day of docket work needed (the requirements, observed)

A research session on 2026-10-10 did all of these by hand, several of them more than once:

1. Resolve a reference ("Smith v. Agency, D.D.C. 1:26-cv-01234") to a docket, and to the identifiers the document store uses. The search result carried the docket id but not the storage id, so document URLs had to be guessed.
2. List a docket's entries since a date.
3. Fetch a filing or an attachment, keep it in the KB as a file, and record its hash.
4. Tell apart "not on the docket", "on the docket but only on PACER (price shown)", "blocked by a WAF or rate limit", and "found". evidence-search already returns typed outcomes (Hit, VerifiedAbsence, AccessBlocker, RateLimited, AwaitingHuman); the extension must keep them visible, never collapse them into an empty list.
5. Watch a docket: "re-check on a date, or when a named kind of filing appears". Today that is a hand-set retest date on a task.
6. Cite with a locator (ECF number, page, paragraph), and say whether the text was read from a text layer, from OCR, or from the page image.
7. Find the exhibits attached to a filing.
8. Record a paid step (a PACER purchase) as something a person does, with the price.

## Constraints from the design ([[design]])

- The files are the KB: a fetched filing is a file in the KB with its hash in an entry's frontmatter; removing Pyrite loses nothing. No sidecar store of documents.
- Derive, do not write: "last filing", "next deadline", "days since checked" come from the index, shown as derived.
- Types give structure, protocols give behaviour ([[adr-0045]]): candidate types `court_case`, `docket_entry`, `filing`; a candidate protocol `watchable` (a retest date or trigger, a last-checked stamp, an outcome). Check whether `verifiable` ([[adr-0047]]) already covers the citation-and-read-kind fields before adding any.
- Pyrite runs no crawler of its own in this contract: evidence-search is the backend and is called, not vendored. Say what happens when it is not installed.
- Rate limits and terms of the upstream service are the operator's; credentials live in the operator's config, never in an entry.

## Deliverables

- A one-page recommendation: separate extension or not, with the argument against.
- The type and protocol sketch, with one worked example entry for a case, an entry for a filing, and a watch.
- The command surface: what `search`, `pull`, `watch` and `cite` would be on the CLI, and how the same operations appear over REST and MCP ([[adr-0046]], proposed, would apply: a partial result is not exit 0).
- The boundary with evidence-search, written as the calls the extension makes and the typed outcomes it must preserve.
- What Free Law Project's API offers that changes the design (alerts or webhooks in place of polling, document fetch, bulk data), read from their documentation, with links.
- Either backlog items with acceptance criteria for a first slice, or "not worth it" with the reason.

Out of scope for the spike: writing the extension, and anything about a relationship with Free Law Project beyond reading their public documentation.
