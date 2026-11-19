---
id: background-embedding-pipeline
type: backlog_item
title: "Background Embedding Pipeline"
kind: improvement
status: done
priority: medium
effort: M
tags: [ai, performance, embedding]
---

# Background Embedding Pipeline

Decouple write latency from embedding computation by moving embedding updates to a background pipeline.

## Context

Currently, `KBService.create_entry()` and `update_entry()` call `EmbeddingService.embed_entry()` synchronously after indexing. For large entries or slow models, this blocks the API response. The embedding is also silently skipped on failure, meaning some entries may lack embeddings without the user knowing.

## Scope

### Background queue (SQLite-backed)
- New `embed_queue` table: `entry_id`, `kb_name`, `queued_at`, `status` (pending/processing/done/failed), `error`, `attempts`
- On create/update: insert into queue instead of embedding synchronously
- Worker loop: poll queue, embed, update status
- Retry with backoff: max 3 attempts before marking failed

### Worker implementation
- `EmbeddingWorker` class with `process_queue()` method
- Runs as a background task in the REST API server (asyncio task started on app startup)
- CLI command `pyrite index embed --background` for manual triggering
- Batch processing: embed up to N entries per cycle (configurable, default 10)
- Graceful shutdown: finish current entry, stop accepting new work

### Status visibility
- `GET /api/index/embed-status` — queue depth, processing count, last error
- `pyrite index embed --status` — CLI equivalent
- WebSocket event `embed_complete` when an entry is freshly embedded

### Fallback behavior
- If no background worker is running (CLI usage, single-request mode): embed synchronously as today
- REST API server always starts the worker
- MCP server does not start the worker (MCP is request/response, not long-running)

## Rationale

Embedding on every save is the primary source of write latency when sentence-transformers is loaded. Moving to a background queue keeps writes fast while ensuring all entries eventually get embeddings. The queue also provides visibility into embedding failures that are currently swallowed silently.

## References

- [Embedding Service](../../components/embedding-service.md)
- [KB Service](../../components/kb-service.md) — current synchronous embedding in CRUD pipeline
- [WebSocket Server](../../components/websocket-server.md) — for embed_complete events
