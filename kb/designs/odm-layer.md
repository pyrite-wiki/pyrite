---
id: odm-layer
title: "Pyrite ODM: Object-Document Mapper with Schema Migration and Backend Abstraction"
type: design_doc
status: active
tags:
- design
- odm
- storage
- migration
- architecture
links:
- schema-versioning
- postgres-storage-backend
- extension-type-protocols
- bhag-self-configuring-knowledge-infrastructure
---

## Overview

Pyrite's ODM layer sits between the service layer (KBService, TaskService, etc.) and storage backends. It provides schema-versioned document loading, on-load migration (Ming pattern), validation on save, and a pluggable backend interface for knowledge indexing.

The ODM does not replace the file-based source of truth. Files in git remain authoritative. The ODM manages the derived index and provides a stable API that insulates the service layer from backend-specific concerns.

## Architecture

```
┌──────────────────────────────────┐
│         Service Layer            │
│  KBService, TaskService, QA...   │
├──────────────────────────────────┤
│           ODM Layer              │
│  DocumentManager                 │
│  ├── load() — parse + migrate    │
│  ├── save() — validate + write   │
│  ├── search() — delegate         │
│  └── migrate_all() — force load  │
│                                  │
│  MigrationRegistry               │
│  ├── register(type, v1→v2, fn)   │
│  ├── chain(type, from, to)       │
│  └── apply(entry)                │
│                                  │
│  SchemaVersionTracker             │
│  ├── current_version(kb, type)   │
│  ├── entry_version(entry)        │
│  └── needs_migration(entry)      │
├──────────────────────────────────┤
│       Backend Interface          │
│  SearchBackend (protocol)        │
│  ├── SQLiteBackend (default)     │
│  └── PostgresBackend (done)      │
├──────────────────────────────────┤
│       App State (separate)       │
│  SQLAlchemy ORM (unchanged)      │
│  Settings, starred, API keys     │
└──────────────────────────────────┘
```

## Document Lifecycle

### Load Path

```
File on disk
  → FileRepository.load(path)        # parse markdown + frontmatter
  → ODM.load(raw_entry)
    → check _schema_version
    → if version < current:
        → MigrationRegistry.apply(entry, from=entry_version, to=current)
        → mark entry as migrated
    → validate against current schema
    → return typed Entry object
```

### Save Path

```
Entry object
  → ODM.save(entry)
    → validate against current schema (raise on failure)
    → set _schema_version = current
    → FileRepository.write(path, entry)  # serialize to markdown + frontmatter
    → SearchBackend.upsert(entry)        # update index
```

### Search Path

```
Query
  → ODM.search(query, mode, **filters)
    → SearchBackend.search_hybrid(query, vector, **filters)
    → return results (already at current schema version in index)
```

### Migration Path

```
pyrite schema migrate --kb research
  → for each entry in kb:
    → ODM.load(entry)           # triggers on-load migration
    → if entry was migrated:
      → ODM.save(entry)         # writes migrated version to file + index
  → report: "247 entries checked, 31 migrated, 0 errors"
  → git diff shows changes     # reviewable before commit
```

## Schema Version Tracking

### In kb.yaml

```yaml
name: investigation
kb_type: journalism
schema_version: 3

types:
  finding:
    version: 3
    fields:
      confidence: {type: number, required: true, since_version: 2}
      evidence: {type: multi-ref, required: true, since_version: 1}
      methodology: {type: string, required: true, since_version: 3}
```

### In entry frontmatter

```yaml
---
id: finding-001
type: finding
title: "Financial connection between X and Y"
_schema_version: 2
confidence: 0.85
evidence: [doc-001, doc-002]
# note: no 'methodology' field — this entry predates v3
---
```

When loaded, the ODM sees `_schema_version: 2`, current is 3, and runs the v2→v3 migration which adds a default `methodology` field.

### Version rules

- KB-level `schema_version` increments when any type changes
- Type-level `version` tracks per-type schema evolution
- `since_version` on fields distinguishes "required for new entries" from "required everywhere"
- Entries without `_schema_version` are treated as version 0 (pre-versioning, migrated to current)

## Migration Registry

### Registration

Migrations are registered by extensions and core code:

```python
from pyrite.odm import migration_registry

@migration_registry.register(type="finding", from_version=1, to_version=2)
def finding_v1_to_v2(entry_data: dict) -> dict:
    """Add confidence field with default."""
    if "confidence" not in entry_data:
        entry_data["confidence"] = 0.5
    return entry_data

@migration_registry.register(type="finding", from_version=2, to_version=3)
def finding_v2_to_v3(entry_data: dict) -> dict:
    """Add methodology field with default."""
    if "methodology" not in entry_data:
        entry_data["methodology"] = "unspecified"
    return entry_data
```

### Chain resolution

```python
chain = migration_registry.chain(type="finding", from_version=1, to_version=3)
# Returns: [finding_v1_to_v2, finding_v2_to_v3]

# Applied sequentially:
for migration_fn in chain:
    entry_data = migration_fn(entry_data)
```

### Extension migrations

Extensions register their own migrations via the plugin protocol:

```python
class JournalismPlugin(PyritePlugin):
    def get_migrations(self) -> list[Migration]:
        return [
            Migration(type="finding", from_version=1, to_version=2, fn=finding_v1_to_v2),
            Migration(type="lead", from_version=1, to_version=2, fn=lead_v1_to_v2),
        ]
```

The ODM collects migrations from all installed extensions and core, builds the full chain per type.

## SearchBackend Protocol

```python
from typing import Protocol, Iterable

class SearchBackend(Protocol):
    """Index backend for knowledge entries."""

    # Write operations
    def upsert_entry(self, entry: EntryRecord, embedding: list[float] | None = None) -> None: ...
    def delete_entry(self, entry_id: str, kb_name: str) -> None: ...
    def rebuild(self, kb_name: str, entries: Iterable[EntryRecord]) -> None: ...

    # Search operations
    def search_fts(self, query: str, kb_name: str, limit: int = 20, **filters) -> list[SearchResult]: ...
    def search_semantic(self, vector: list[float], kb_name: str, limit: int = 20, **filters) -> list[SearchResult]: ...
    def search_hybrid(self, query: str, vector: list[float], kb_name: str, limit: int = 20, **filters) -> list[SearchResult]: ...

    # Query operations
    def get_entry(self, entry_id: str, kb_name: str) -> EntryRecord | None: ...
    def query_entries(self, kb_name: str, type: str | None = None, **filters) -> list[EntryRecord]: ...
    def get_backlinks(self, entry_id: str, kb_name: str) -> list[BacklinkRecord]: ...
    def get_blocks(self, entry_id: str, kb_name: str) -> list[BlockRecord]: ...

    # Metadata
    def entry_count(self, kb_name: str) -> int: ...
    def health_check(self) -> dict: ...
```

Note: `SearchBackend` is itself a structural protocol (ADR-0014). A backend satisfies it by implementing the methods — no inheritance required.

### SQLiteBackend (current, wrapped)

Wraps existing `PyriteDB` + `IndexManager` + FTS5 + embedding pipeline behind the `SearchBackend` interface. Minimal code changes — routing, not rewriting.

### PostgresBackend (done)

- Entries in a `knowledge_entries` table with JSONB metadata
- pgvector for cosine similarity embeddings (built with an HNSW index; since #1822 exact search, no index)
- tsvector/tsquery with GIN index for weighted FTS
- Hybrid search combining both with application-level RRF reranking
- 66/66 conformance tests passing — full parity with SQLiteBackend
- ~3x slower indexing, ~2x slower queries vs SQLite — acceptable for server deployments

**Note:** LanceDB was evaluated and rejected (49-66x slower indexing, 60-280x slower queries). See [ADR-0016](../adrs/0016-lancedb-evaluation.md).

## Migration from Current Architecture

### Phase 1: Define interfaces, wrap existing code (M)

- Define `SearchBackend` protocol
- Implement `SQLiteBackend` wrapping existing `PyriteDB`, `IndexManager`, FTS5, embedding code
- Define `DocumentManager` with load/save paths
- Route `KBService` through `DocumentManager` for load/save
- Route search calls through `SearchBackend`
- **No behavior change** — existing tests pass, same SQLite underneath

### Phase 2: Schema versioning + migration registry (M)

- Add `_schema_version` to entry frontmatter on save
- Implement `MigrationRegistry` with register/chain/apply
- Add `get_migrations()` to plugin protocol
- Implement on-load migration in `DocumentManager.load()`
- `pyrite schema migrate` command
- `pyrite ci` schema-version-aware validation

### Phase 3: LanceDB backend — REJECTED

Evaluated and rejected. 49-66x slower indexing, 60-280x slower queries, 25-54x larger disk. See [ADR-0016](../adrs/0016-lancedb-evaluation.md).

### Phase 4: Postgres backend (M) — DONE

- Implemented `PostgresBackend` satisfying `SearchBackend` protocol (66/66 conformance tests)
- tsvector/tsquery with GIN for FTS, pgvector HNSW for cosine similarity (the HNSW index was dropped in #1822: semantic search is exact)
- ~3x slower indexing, ~2x slower queries vs SQLite — acceptable for server deployments

## Relationship to Existing Architecture

- **ADR-0003 (Two-Tier Durability)** — unchanged. Files are source of truth. DB is derived index. The ODM formalizes and strengthens this.
- **ADR-0013 (Unified DB Connection)** — evolves. The unified SQLite connection model becomes one backend option among several. The ODM is the new unification point.
- **ADR-0014 (Structural Protocols)** — `SearchBackend` is itself a protocol. The ODM is the first major consumer of the protocol pattern.
- **ADR-0015 (this design's ADR)** — the architectural decision. This doc is the implementation detail.

## Open Questions

- **Object versioning**: Should entries track modification history (v1 created, v2 migrated, v3 edited)? Or just current schema version? Ming tracked both.
- **Partial migration**: If a migration fails on one entry, should the script continue or abort? Ming continued with error reporting.
- **Index rebuild during migration**: After migrating files, should `pyrite schema migrate` also rebuild the search index? Probably yes — the index should reflect migrated content.
- **Future backends**: DuckDB, Qdrant, etc. can reuse the 66-test conformance suite if needed.
