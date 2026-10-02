---
id: spike-which-embedding-setup-finds-the-links-people-already-made
title: 'Spike: which embedding setup finds the links people already made'
type: backlog_item
tags:
- search
- embeddings
- quality
importance: 5
kind: spike
status: in_progress
priority: high
effort: M
rank: 0
---

Maintainer direction, 2026-10-02: measure, on a real KB, which embedding setup finds the links people already made; if a small model over the whole entry holds up, change the defaults.

## Question

Pyrite embeds each entry's title, summary and the first 500 characters of its body with `all-MiniLM-L6-v2` (`_MODEL_MAX_BODY_CHARS` in `pyrite/services/embedding_service.py`). On a KB of long-form notes that is a small fraction of the text. Does embedding the whole entry in passages, with the same small model, find more of the entries a person linked by hand? Does a larger model do better, and at what cost?

The backlog item `embedding-body-truncation` (done) made the limit visible and per-model. It did not embed the rest of the entry; its "consider chunking strategies" line was never taken up for lack of evidence. This spike is that evidence.

## Method

Link prediction against a KB's own links. Hide the links, then see whether search finds them again.

- Ground truth: links people made. Frontmatter fields that point at other entries, and wikilinks in bodies.
- **Close the leak first.** An entry's body contains its links, so the target's id is in the text being embedded. Stripping `[[id]]` and the link fields is not enough: in the test corpus, relative Markdown links, backticked file names and bare ids still carried the target's id in about 11% of pairs after those first rules. A further set of rules took link markup to zero; what remained (under 2% of a held-out sample) was ordinary prose and third-party URL slugs.
- Tasks: article to the events it covers; event to its article; note to the notes it links.
- Metrics: recall@5, @10, @50 and MRR.
- Setups: a keyword baseline (BM25); today's default reproduced; the same model over the whole entry in passages (about 200 words, 40 overlap, title prepended; scored by best passage, and by mean-pooled entry vectors); hybrid by reciprocal-rank fusion; then stronger models the same two ways.

Run as a cloud session on a 4-vCPU, 16 GB machine with no GPU.

## Corpus shape (no content)

Three KBs: about 3,600 long-form notes (median body about 9,000 characters, mean about 14,000), about 6,100 short events, 155 articles. About 73,000 passages in all. Ground truth: 348 event-to-article pairs and about 11,400 note-to-note pairs; 74% of wikilinks resolved to an entry.

On the notes, today's default covers about 3% of the body text; 2% of notes fit whole.

## Interim findings (2026-10-02, a 300-entry sample: indicative only)

| Task | Whole entry in passages, same small model (MRR) | Keyword (MRR) | Today's default (MRR) |
|---|---|---|---|
| Article to events | 0.66 | 0.57 | 0.52 |
| Event to article | 0.71 | 0.70 | 0.44 |

1. **Today's default came last on both tasks.**
2. **The same small model over the whole entry beat it clearly,** with no change of model.
3. **Keyword search is a hard baseline** on text full of names, case numbers and figures. Hybrid is the row to watch.

Cost on that machine, seconds per 1,000 passages: `all-MiniLM-L6-v2` 22; `bge-small-en-v1.5` 60; `all-mpnet-base-v2` about 220; `modernbert-embed-base` about 430. The small model covers the whole test corpus in roughly half an hour on four CPUs; the largest would take most of a working day. A default has to run on a contributor's laptop.

## Still to come

- The full-corpus scorecard, with the larger models on a stratified sample.
- Phase 2: the winning setup inside Pyrite itself, on SQLite and on Postgres with pgvector: whether Pyrite's own search reproduces the measured result, and index build time, index size, query time and peak memory at this scale, which Pyrite has not been run at.

## Deliverables

- This entry, updated with the full results (no corpus content: shape, setups, metrics, timings).
- If the finding holds: a backlog item to change the default to whole-entry passage embedding, with the numbers, the storage cost (vectors per entry, index size) and a migration path for existing indexes.
- A proposal for a `pyrite qa` command that runs this test on any KB from its own links, with the leak-stripping rules, so a user can ask "is my search working?" and get a number. The same test is a regression guard for search changes.

## Limits of the evidence

One corpus, one domain (legal, procurement and local-government text), English, CPU only. The interim numbers are from a small sample.
