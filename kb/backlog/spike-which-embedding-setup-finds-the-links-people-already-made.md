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

## Findings, full corpus (2026-10-02; final for `all-MiniLM-L6-v2`, keyword and hybrid)

9,876 entries. Task A: article to the events it covers (92 queries). B: event to its article (218). C: note to the notes it links (500 sampled). Cells are recall@10 / MRR.

| Setup | A | B | C |
|---|---|---|---|
| Today's default: title + summary + 500 characters, one vector | 0.301 / 0.405 | 0.566 / 0.467 | 0.223 / 0.406 |
| Whole entry, one vector (the model stops at 256 tokens) | 0.330 / 0.431 | 0.622 / 0.505 | 0.260 / 0.456 |
| 200-word passages, mean-pooled to one vector | 0.367 / 0.441 | 0.755 / 0.585 | 0.345 / 0.493 |
| **200-word passages, best passage** | **0.460 / 0.524** | **0.819 / 0.716** | **0.437 / 0.595** |
| Keyword (BM25) over the full text | 0.441 / 0.580 | 0.788 / 0.688 | 0.483 / 0.649 |
| Hybrid: reciprocal-rank fusion of keyword and best passage | 0.456 / 0.519 | 0.829 / 0.716 | 0.491 / 0.642 |

1. **The same small model, given the whole entry in passages and scored by its best passage, beats today's default on every task.** MRR rises by +0.119, +0.249 and +0.190 (paired bootstrap 95% intervals all exclude zero); recall@10 roughly doubles on C.
2. **One vector over more text does not.** `all-MiniLM-L6-v2` reads 256 tokens, so a whole-entry vector is still a truncated one: +0.026, +0.038 and +0.050 MRR, within noise on A and B. Raising `_MODEL_MAX_BODY_CHARS` for this model changes little.
3. **Averaging the passages into one vector gives most of the gain away.** Best passage beats mean-pooling by +0.083, +0.131 and +0.102 MRR, all significant. The index has to hold several vectors per entry.
4. **Keyword search alone beats today's default** by +0.175 to +0.244 MRR, and beats the best small-model setup at the top of the ranking on C (+0.054, significant). Embeddings find more at depth: recall@50 is higher than keyword's by 0.101 on A and 0.036 on B.
5. **Hybrid ranks best overall.** It keeps keyword's top ranks on B and C and lifts recall@50 over keyword alone by +0.115, +0.021 and +0.051. On A its MRR is below keyword's (−0.061).
6. **Details that matter less.** 100-word passages: within noise of 200, for 1.8 times the vectors. 400-word passages: truncated by this model, worse on B. Title prepended: consistently a little better, free. Splitting on headings first: a little better (significant on C) for 18% more vectors.
7. **"Short" entries are not short for this model.** The events have a median body of about 2,000 characters, past its window, and passages beat one vector on them too. An entry fits in one vector only under about 190 words.

### Cost (4 vCPU, no GPU, OpenVINO backend)

| | Today | 200-word passages |
|---|---|---|
| Vectors stored | 9,876 | 73,638 (7.5 per entry; 14.6 per long note, 95th percentile 44) |
| Index size, float32 x 384 | 15 MB | 113 MB |
| Time to embed the corpus | 2.5 min | 27 min |

Cost follows words, not entries: about 22 seconds per 1,000 passages. The torch backend Pyrite ships measured 1.3 to 2 times slower on the same CPU, so expect 35 to 55 minutes for a KB this size. A KB of short entries pays almost nothing extra.

## Still to come

- Rows for `bge-small-en-v1.5`, `all-mpnet-base-v2` and `modernbert-embed-base` (running), which decide whether there is a long-form option worth its cost. Measured seconds per 1,000 passages: 64, 174 and about 430.
- Phase 2: the recommended setup inside Pyrite itself, on SQLite and on Postgres with pgvector: whether Pyrite's own search reproduces the measured result, and index build time, index size, query time and peak memory at this scale, which Pyrite has not been run at.

## Deliverables

- This entry, updated with the full results (no corpus content: shape, setups, metrics, timings).
- If the finding holds: a backlog item to change the default to whole-entry passage embedding, with the numbers, the storage cost (vectors per entry, index size) and a migration path for existing indexes.
- A proposal for a `pyrite qa` command that runs this test on any KB from its own links, with the leak-stripping rules, so a user can ask "is my search working?" and get a number. The same test is a regression guard for search changes.

## Limits of the evidence

One corpus, one domain (legal, procurement and local-government text), English, CPU only. Unlinked pairs are not known to be unrelated, so every setup is under-credited. Link ids that are also ordinary words could not be stripped (1.7% of held-out pairs), which favours keyword search slightly. Query time and index build inside Pyrite are not measured yet.
