---
id: load-the-embedding-model-inside-the-cache-lock-207
title: Load the embedding model inside the cache lock (#207)
type: backlog_item
tags:
- concurrency
importance: 5
kind: bug
status: done
priority: high
assignee: agent:pyrite-worker-207
effort: M
rank: 0
---

# #207 — Concurrent first writes on a cold process load N embedding models at once — segfault/hang (model-cache lock is taken after the load)

## Problem

A cold server whose first traffic is several concurrent writes crashes or hangs: with an empty model cache in the process, 8 concurrent `POST /api/entries` at once produce `Segmentation fault: 11` or a hang past 60 s. `faulthandler` stacks show 8 threads each independently loading a sentence-transformers model: `kb_service.py:148 _auto_embed` → `embedding_service.py:220 _get_model` → `SentenceTransformer.__init__` → torch.

Found by the cold read of PR #203 (2026-09-20) on a live `pyrite serve` against a scratch data dir; **pre-existing** — `pyrite/services/embedding_service.py` and `kb_service.py` are untouched by that PR and `dev` (24533f4) has identical code. Prewarming the model (`prewarm_embeddings`) makes it vanish, which is why it is not seen in normal use.

## Cause

`_MODEL_CACHE_LOCK` (`pyrite/services/embedding_service.py:200-222`) is taken *after* the model load, not around it, so N concurrent first calls build N models concurrently; torch is not safe under that.

## Acceptance

- The model load is inside the lock (double-checked: check cache → lock → check again → load → cache), so N concurrent first calls load once and N−1 wait.
- A test with a stubbed, slow "model constructor" and 8 concurrent callers asserts the constructor runs exactly once and every caller gets the same object (start barrier + one group deadline, `-n 4`-safe; see `tests/test_task_claim_concurrency.py`).
- A live-server regime noted in the PR: 8 concurrent first writes on a cold process, 0 crashes (heavy: yes — one server).

## Files

`pyrite/services/embedding_service.py:200-222`; new test in `tests/test_embedding.py` or a sibling.

Related: ADR-0035 (proposed) makes writes eventually-embedded, which removes the write path from the first-load race entirely; this fix is still right for the first *search* on a cold process.



---

## Groom 2026-09-23

**Still reproduces on dev 6e505be: the race yes, the stated symptom no.**
- **The race is unchanged.** `pyrite/services/embedding_service.py:198-222` still checks the cache under `_MODEL_CACHE_LOCK`, releases the lock, calls `SentenceTransformer(...)` unlocked (:220), and only then locks again to store the result (:221-222). N concurrent first callers still build N models.
- **The write path in the report no longer reaches it.** ADR-0035 (accepted, implemented) made `_auto_embed` enqueue-only (`kb_service.py:216-239`: "Never embeds inline"), so 8 concurrent `POST /api/entries` no longer load a model.
- **Where it is reachable now:**
  - On the default config (`prewarm_embeddings: false`, `config.py:347`) with an empty `embed_queue` at startup (the startup drain at `api.py:1119` is skipped when nothing is pending), the first N concurrent `--mode semantic`/`hybrid` searches (`search_service.py:495` → `search_similar` → `embed_text` → `_get_model`) run in the threadpool and race.
  - So does a `POST /api/index/sync?wait=true` drain (`admin.py:95`) racing a first search.
  - With `prewarm_embeddings: true`, startup waits for the prewarm, so there is no race.
- The logger-level save/restore at `:214-226` is process-global and races in the same window. It is fixed by the same change.

### Acceptance
1. **Double-checked load:**
   - check the cache;
   - then take a lock and check again;
   - then load, store and release.
   N concurrent first callers for one model name construct it **once**, and N−1 wait and get the same object. A single global lock held across the load is acceptable. A per-model-name lock is allowed but not required.
2. **A failed load poisons nothing.** If the constructor raises, nothing is cached, the lock is released, the logger levels and the progress bar are restored, and the next caller retries.
3. **The constructor is reachable through a patchable seam** (e.g. a module-level `_load_model(name)`), so the tests below run **without** `sentence-transformers` or torch and without the `embeddings` marker.
4. **Tests** (new, e.g. `tests/test_embedding_model_cache_concurrency.py`; follow `tests/test_task_claim_concurrency.py`: start barrier plus one group deadline, `-n 4`-safe, and clear `_MODEL_CACHE` in a fixture):
   - (a) 8 threads on 8 fresh `EmbeddingService` instances call `_get_model` against a stub constructor that sleeps briefly. The constructor count is exactly 1 and all 8 results are the same object.
   - (b) With a raising stub, every caller sees the error or retries, and the cache stays empty. A following call with a working stub succeeds.
5. **Live regime, recorded in the PR body, not a test:** on a cold `pyrite serve` with `prewarm_embeddings: false` and a KB that has embeddings, 8 concurrent first `GET /api/search?mode=semantic` give 0 crashes and one model load in the log.
6. A changelog fragment in `changelog.d/`.

### Touches
- existing: `pyrite/services/embedding_service.py` (`_get_model` and the module-level cache/lock around :167-226)
- new: `tests/test_embedding_model_cache_concurrency.py`, `changelog.d/<n>.bugfix.md`
- Keep `tests/test_embedding.py::TestModelIsSharedPerProcess` passing as it is; it uses the real model.

### Sequence
Independent of #218 and #221: no shared files.

### Model / weight / review
- **Model: sonnet.** A textbook double-checked lock with a fully specified test. Services layer, not storage or auth.
- **heavy: yes, for criterion 5 only.** One live server plus a real model load (~90 MB). It counts against the Playwright/live slot budget. Run it once, at review.
- **Cold read: no.** Small, local, and the concurrency test pins it. The conductor's diff review is enough.

### Out of scope
- Changing the prewarm default, or making the server prewarm when semantic search is enabled. That is a product decision; raise it separately.
- Any change to ADR-0035's queue or drain.
- Bounding memory by unloading models, or adding a model-cache eviction policy.
- Moving embedding onto a background thread (#102 decided against it).
