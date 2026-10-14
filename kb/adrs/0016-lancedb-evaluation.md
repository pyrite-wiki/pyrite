---
id: adr-0016
type: adr
title: "LanceDB Backend Evaluation — No-Go"
adr_number: 16
status: rejected
date: 2026-03-01
tags: [architecture, storage, search, evaluation]
---

# ADR-0016: LanceDB Backend Evaluation

## Status

**Rejected** — LanceDB backend removed. PostgresBackend (tsvector + pgvector) adopted as the second backend alongside SQLite.

## Context

Pyrite's storage layer was tightly coupled to SQLite (FTS5 + sqlite-vec + ORM). ADR-0015 planned a `SearchBackend` protocol to abstract this. This spike (branch `spike/0.10-schema-odm-lancedb`) validated:

1. **Phase 1**: Extract `SearchBackend` protocol + `SQLiteBackend` from existing code
2. **Phase 2**: Implement `LanceDBBackend` and benchmark against SQLite

## Evaluation Results

### API Parity: PASS

LanceDB passes **66/66 conformance tests** — full protocol parity with SQLiteBackend. Both backends tested with identical parametrized test suite.

### Code Complexity: FAIL

| Metric | SQLiteBackend | LanceDBBackend |
|--------|---------------|----------------|
| Lines of code | 923 | 1,133 |

LanceDB backend is **23% larger**, not smaller. Contributing factors:
- Combined `_fts_text` column needed because native Lance FTS only indexes single fields
- Manual offset handling (LanceDB search has no native offset)
- Placeholder embedding management (`[0.0]*384` for unembedded entries)
- Arrow schema definition and row conversion boilerplate

### Index Build Time: FAIL (49x–100x slower)

| Backend | 500 entries | 1000 entries |
|---------|-------------|--------------|
| SQLite  | 0.63s (791/s) | 1.51s (661/s) |
| LanceDB | 31.0s (16/s)  | 99.8s (10/s)  |

LanceDB is **49x slower at 500 entries and 66x slower at 1000 entries**. The `merge_insert` upsert pattern is extremely expensive per-row. This is because LanceDB is designed for bulk append workloads, not individual upserts. Each `merge_insert` rewrites Lance fragments.

### Query Latency: FAIL (60x–250x slower)

| Backend | Keyword p50 | Keyword p95 | Semantic p50 | Semantic p95 |
|---------|-------------|-------------|--------------|--------------|
| SQLite (500)  | 0.28ms | 0.38ms | 0.27ms | 0.30ms |
| LanceDB (500) | 16.9ms | 18.7ms | 26.2ms | 34.3ms |
| SQLite (1000) | 0.47ms | 0.58ms | 0.18ms | 0.21ms |
| LanceDB (1000)| 34.1ms | 37.7ms | 50.5ms | 53.5ms |

Keyword search is **60–73x slower**. Semantic search is **97–280x slower**. Both well beyond the 2x target.

### Search Quality: PASS

| Backend | Recall@10 | MRR | nDCG@10 |
|---------|-----------|-----|---------|
| SQLite  | 0.525/0.258 | 1.0 | 1.0 |
| LanceDB | 0.523/0.258 | 1.0 | 1.0 |

Identical search quality. Both FTS engines find the same results with equivalent ranking.

### Disk Footprint: FAIL (25x–54x larger)

| Backend | 500 entries | 1000 entries |
|---------|-------------|--------------|
| SQLite  | 0.79 MB | 1.24 MB |
| LanceDB | 19.7 MB | 67.5 MB |

LanceDB is **25x larger at 500 entries and 54x larger at 1000 entries**. The columnar Arrow format with fixed-size embedding columns dominates. Even unembedded entries carry a `[0.0]*384` placeholder.

### Dependency Weight: CONCERN

| Package | Install Size |
|---------|-------------|
| lancedb | 100 MB |
| pyarrow | 120 MB |
| **Total** | **220 MB** |

vs. SQLite (stdlib) + sqlite-vec (~2 MB). LanceDB adds **110x more dependency weight**.

## Summary Table

| Metric | Target | Result | Verdict |
|--------|--------|--------|---------|
| API parity | 100% conformance | 66/66 ✓ | **PASS** |
| Code simplicity | Fewer lines | 1,133 vs 923 (+23%) | **FAIL** |
| Index build time | ≤ 1.5x | 49–66x | **FAIL** |
| Query latency | ≤ 2x | 60–280x | **FAIL** |
| Search quality | ≥ equal | Equal | **PASS** |
| Disk footprint | ≤ 2x | 25–54x | **FAIL** |
| Dependency weight | Documented | +220 MB | **CONCERN** |

## Decision: No-Go

LanceDB is **not suitable as Pyrite's default backend** at current scale (100–5000 entries). The performance characteristics are designed for large-scale ML workloads (millions of vectors, batch ingestion), not for the small, interactive, upsert-heavy pattern Pyrite uses.

### What Worked

1. **SearchBackend protocol** — clean abstraction, zero regressions, both backends pass identical tests
2. **LanceDB API** — merge_insert upsert, native FTS, list column types, hybrid search builder all work correctly
3. **Conformance test suite** — 66 parametrized tests validate any SearchBackend implementation

### What Didn't

1. **Per-row upsert cost** — LanceDB's append-optimized storage makes individual upserts very expensive
2. **Disk overhead** — Arrow columnar format with fixed-dimension embeddings wastes space at small scale
3. **Query overhead** — Each query creates new Arrow readers; SQLite's page cache is much faster for small datasets
4. **FTS limitations** — Native Lance FTS only supports single-field indexing; multi-field requires tantivy+pylance

### Outcome

1. **SearchBackend protocol merged to main** — 66 conformance tests, clean abstraction, decoupled services.

2. **LanceDB backend removed** — Code deleted after evaluation. Not worth maintaining given 49-280x performance penalties across all metrics.

3. **PostgresBackend adopted instead** — PostgreSQL with tsvector (weighted FTS) + pgvector (HNSW cosine) passes 66/66 conformance tests. Benchmarks show ~3x slower indexing and ~2x slower queries vs SQLite — acceptable overhead for multi-user/server deployments where SQLite's single-writer lock is the real bottleneck.

## Consequences

- SearchBackend protocol is the stable abstraction for all knowledge-index operations
- SQLite remains the default backend for local/single-user use
- PostgresBackend is the production backend for multi-user/server deployments
- Future backend experiments (DuckDB, Qdrant, etc.) can reuse the 66-test conformance suite
