---
id: postgres-storage-backend
title: "PostgreSQL as Optional Storage Backend"
type: backlog_item
tags:
- feature
- infrastructure
- storage
- postgres
- deployment
links:
- target: demo-site-deployment
  relation: related
- target: ephemeral-kbs
  relation: related
- target: launch-plan
  relation: related
- target: roadmap
  relation: related
kind: feature
status: done
effort: M
---

## Problem

SQLite is the right default for single-user and agent workflows — zero config, embedded, fast. But multi-user deployments (demo site, corporate teams, hosted solution) need concurrent writes, row-level locking, and operational tooling that SQLite can't provide. The demo site in particular would benefit from Postgres: proper concurrent access for multiple visitors, pgvector for semantic search, and standard hosting on every cloud platform.

## Solution

Add PostgreSQL as an optional storage backend. Most of the data access layer already uses SQLAlchemy, so the core migration is a configuration change. The work is in the areas that bypass SQLAlchemy or use SQLite-specific features.

### What's Already Abstracted (low effort)

- Entry CRUD (SQLAlchemy ORM)
- Settings, starred entries, collections (SQLAlchemy ORM)
- Basic queries and filters (SQLAlchemy query builder)

### What Needs Work

**Full-text search:**
- SQLite: FTS5 virtual tables
- Postgres: `tsvector`/`tsquery` with `GIN` index
- Postgres FTS is actually more capable (ranking, stemming, language-aware)
- Need: abstract FTS behind a search interface, implement both backends

**Semantic search / embeddings:**
- SQLite: custom vector storage in `embed_queue` table, manual cosine similarity
- Postgres: pgvector extension, `vector` column type, `<=>` cosine distance operator
- pgvector is cleaner — native SQL operator instead of application-level math
- Need: abstract embedding storage and similarity queries, implement pgvector backend

**Atomic operations:**
- SQLite: `json_extract()` for metadata queries (e.g., task_claim CAS)
- Postgres: native JSON operators (`->`, `->>`, `jsonb_set()`)
- Need: abstract JSON query operations or use SQLAlchemy's JSON column support

**Ephemeral KBs:**
- Already DB-only (no git) — natural fit for Postgres
- TTL-based garbage collection maps cleanly to Postgres `DELETE` with timestamp comparison
- Concurrent ephemeral KB creation is safer with row-level locking

### Configuration

```yaml
# pyrite.yaml — SQLite (default, no change)
storage:
  backend: sqlite
  path: .pyrite/index.db

# pyrite.yaml — PostgreSQL
storage:
  backend: postgres
  url: postgresql://user:pass@localhost:5432/pyrite
  # Or via environment variable
  url: ${DATABASE_URL}
```

### Migration Path

- `pyrite index sync` rebuilds the database from source files regardless of backend
- No data migration needed — Postgres instance starts empty, index sync populates it
- This is the "derived data" advantage: the DB is always rebuildable from git

### Hosted Solution Angle

A Postgres backend opens the door to a hosted Pyrite offering:
- Multi-tenant with schema-per-KB or row-level security
- Managed Postgres (Neon, Supabase, RDS) for zero-ops
- pgvector included in most managed Postgres offerings
- Standard backup/restore via pg_dump
- Connection pooling (PgBouncer) for high concurrency

This isn't on the immediate roadmap, but having the backend ready keeps the option open.

## Prerequisites

- SQLAlchemy already used for most data access
- Background embedding pipeline (#57, done) defines the embedding storage interface
- FTS5 usage identified and bounded

## Success Criteria

- `pyrite serve` works with `storage.backend: postgres` configuration
- Full-text search and semantic search functional on Postgres
- Ephemeral KBs work on Postgres
- Task claim CAS atomicity works (row-level locking is actually better than SQLite here)
- `pyrite index sync` populates Postgres from git-backed files
- Demo site runs on Postgres
- All existing tests pass against both backends (parameterized test suite)

## Delivered (0.10)

Implemented as `PostgresBackend` in `pyrite/storage/backends/postgres_backend.py` via the `SearchBackend` protocol (ADR-0014).

- **66/66 conformance tests** passing — full protocol parity with SQLiteBackend
- **tsvector/tsquery** with GIN index for weighted full-text search
- **pgvector** with HNSW index for cosine similarity semantic search
- **Hybrid search** combining both with application-level RRF reranking
- **Performance**: ~3x slower indexing, ~2x slower queries vs SQLite — acceptable for server deployments where SQLite's single-writer lock is the real bottleneck
- LanceDB evaluated as alternative but rejected (49-66x slower) — see [ADR-0016](../../adrs/0016-lancedb-evaluation.md)

### What's Deferred

- Demo site deployment on Postgres (#85)
- Multi-tenant schema-per-KB or row-level security
- Ephemeral KBs on Postgres
- pgvector managed hosting optimization

## Launch Context

Valuable for the demo site (#85) — Postgres is standard on Fly.io/Railway and handles concurrent visitors. Also opens the door to a future hosted offering. Could ship as part of 0.8 if demo site needs it, or as a fast-follow.
