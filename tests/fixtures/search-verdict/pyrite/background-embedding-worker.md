---
id: background-embedding-worker
title: Background Embedding Worker
type: note
tags:
- core
- service
- ai
---

SQLite-backed embed_queue plus EmbeddingWorker. Despite the name it is a **queue, not a thread**: constructing one is a CREATE TABLE IF NOT EXISTS (~50 ms cold, zero threads spawned, no torch imported), and process_batch()/drain() are plain synchronous methods a caller must invoke.

Under ADR-0035 every write with auto_embed: true goes through KBService._auto_embed, which builds a worker lazily and enqueues one pending row; a write never loads the embedding model. That is what makes a fresh install usable (#13) -- the entry is keyword-searchable immediately and the embedding is owed, not skipped.

settle_embed_queue(db) is the single drain, shared by every surface: pyrite-server on every startup and at the end of POST /api/index/sync?wait=true, and pyrite index embed / sync / build. One implementation on purpose -- two that differ in correctness is how a KB comes to mean different things depending on which command touched it last. It is cheap when nothing is owed (has_pending() is one indexed COUNT and builds no EmbeddingService), which is what makes draining on every startup affordable rather than only under prewarm_embeddings -- that setting defaults to False, so gating on it would mean a stock install never embedded anything.

Two invariants keep embed-status honest. A row leaves the queue only when embed_entry returns truthy: a False return (entry absent from this database, empty body, sqlite-vec missing) raises EmbedRefused and the row is retried, because counting a non-raising False as success deleted work that was never done. And KBService refuses to queue through an overlay/worktree DB, whose writes land in a diff database while embed_queue lives on main -- such a row would name an entry the drain cannot reach.
