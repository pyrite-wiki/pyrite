---
id: embed-the-whole-entry-in-passages-by-default
title: Embed the whole entry in passages by default
type: backlog_item
tags:
- search
- embeddings
- index
importance: 5
kind: improvement
status: proposed
priority: high
effort: L
rank: 0
---

Proposed, and conditional: do this only if the full results of the spike `spike-which-embedding-setup-finds-the-links-people-already-made` confirm its interim finding.

## Problem

Pyrite embeds an entry's title, summary and the first 500 characters of its body (`_MODEL_MAX_BODY_CHARS`, `pyrite/services/embedding_service.py`). For long-form entries that is a few percent of the text, so semantic search and link discovery see only the opening. On the spike's sample, the same small model over the whole entry, in passages, ranked hand-made links well above today's default, and today's default ranked below plain keyword search.

## Proposal

Embed the whole entry in passages with the current default model, and rank an entry by its best passage.

## What has to be decided (needs a groom, and probably an ADR: it changes the index)

- Where passage vectors live. A `block` table already exists; nothing embeds it.
- Passage size, overlap and what is prepended (title, headings).
- How an entry's score is formed from its passages, and how that combines with keyword rank.
- Storage and time: vectors per entry, index size, embed time on a laptop, and the embed queue's budget.
- Migration: existing indexes re-embed in the background; search must stay correct while they do.
- Both backends: SQLite with sqlite-vec and Postgres with pgvector. The spike's second phase measures them at about 400,000 vectors.
- Whether short entries (events, tasks) keep one vector.

## Acceptance (to be sharpened by the groom)

- On the spike's test, Pyrite's own search matches the measured result for the chosen setup within noise, on both backends.
- A fresh `pyrite init` KB and an existing KB both end up with whole-entry embeddings without the user doing anything.
- `index embed --stats` reports passages embedded, not only entries.

## Out of scope

Changing the default model. The spike's point is that the small model, given the whole entry, already does well; a model change is a separate decision with its own cost.
