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

Proposed. The condition is met: the full-corpus results of the spike `spike-which-embedding-setup-finds-the-links-people-already-made` confirm its interim finding (2026-10-02). Not yet in a milestone; that is the maintainer's call.

## Problem

Pyrite embeds an entry's title, summary and the first 500 characters of its body (`_MODEL_MAX_BODY_CHARS`, `pyrite/services/embedding_service.py`). For long-form entries that is a few percent of the text, so semantic search and link discovery see only the opening. On the spike's corpus of 9,876 entries, the same small model over the whole entry in 200-word passages, scored by best passage, raised MRR on hand-made links by +0.12, +0.25 and +0.19 over today's default across three tasks, and recall@10 from 0.30 to 0.46, 0.57 to 0.82 and 0.22 to 0.44. Today's default ranked below plain keyword search on all three.

## Proposal

Embed the whole entry in passages with the current default model, and rank an entry by its best passage: split on headings, then about 200 words with 40 overlap, title prepended. Fuse that rank with keyword rank.

The spike rules out two cheaper designs. One vector over more text gains almost nothing, because the model reads 256 tokens. Averaging passages into one vector per entry loses +0.08 to +0.13 MRR against best passage. So the index must hold several vectors per entry: 7.5 on average on the spike's corpus, 113 MB against 15 MB, 27 minutes to embed against 2.5 on four CPUs (35 to 55 with Pyrite's torch backend).

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
