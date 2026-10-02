---
id: adr-0015
type: adr
title: "Object-Document Mapper (ODM) Layer with Schema Versioning and On-Load Migration"
adr_number: 15
status: accepted
deciders: ["markr"]
date: "2026-03-01"
tags: [architecture, storage, odm, migration, schema-versioning]
links:
- schema-versioning
- postgres-storage-backend
- extension-type-protocols
---

# ADR-0015: Object-Document Mapper (ODM) Layer with Schema Versioning and On-Load Migration

## Context

Pyrite stores knowledge as typed documents (markdown + YAML frontmatter) but indexes them through SQLAlchemy ORM on SQLite — a relational database. This creates an impedance mismatch:

- Flexible frontmatter fields stored as JSON blobs, queried via `json_extract()`
- Separate FTS5 virtual tables for full-text search
- A separate background embedding pipeline managing vectors in another table
- Three query systems (ORM, FTS5, embedding similarity) duct-taped to a relational engine

Additionally, there is no schema migration story. Adding a required field to a type invalidates every existing entry. There's no way to evolve schemas without breaking existing content.

### Prior art: Ming + Allura (SourceForge)

Ming was an ODM built for MongoDB at SourceForge. Coupled with Allura's Artifact base class, it provided:

- **Schema-versioned documents**: Each document tracked its schema version
- **On-load migration**: Documents migrated when accessed, not in batch. The system tolerated mixed versions.
- **Migration scripts**: A command that touched every document, forcing on-load migration, producing a clean "everything is migrated" checkpoint
- **Pythonic API**: Clean object layer insulating application code from MongoDB's raw API

This pattern is proven at scale (SourceForge operated one of the largest MongoDB deployments at the time) and maps directly to Pyrite's needs.

### Storage backend flexibility

Multiple storage backends are planned or desirable:

- **SQLite**: Current default, good for single-user and agent workflows
- **PostgreSQL**: Multi-user deployments, demo site (#91)
- ~~**LanceDB**~~: Evaluated and rejected — 49-66x slower indexing, 60-280x slower queries, 25-54x larger disk. See [ADR-0016](0016-lancedb-evaluation.md)

Without an abstraction layer, each backend requires rewriting the service layer. With an ODM, the service layer talks to a stable API and backends are configuration choices.

## Decision

### Schema versioning is decoupled from the full ODM refactor

**Addendum (2026-02-28):** The original decision bundled schema versioning with the full ODM/backend abstraction as a single phased effort. After evaluating the critical path to 0.8 (Announceable Alpha), we're decoupling them:

- **Schema versioning ships independently** (pre-0.8), hooking into the existing `KBRepository` and `IndexManager` load/save paths. No `DocumentManager` or `SearchBackend` abstraction required. The `MigrationRegistry`, `_schema_version` tracking, `since_version` semantics, and `pyrite schema migrate` command all work against the current architecture.

- **The ODM layer ships post-launch** (0.9+) as a refactor. It's still the right architecture for backend abstraction, but it's not on the critical path. Routing all of `KBService`, `SearchService`, `IndexManager`, embedding service, QA service, and MCP handlers through a new abstraction layer is the largest architectural change since service layer enforcement — and taking that on right before launch is unnecessary risk.

The migration pattern (Ming-style on-load migration with forced-load scripts, reviewable via git diff) is the same regardless of whether it hooks into a `DocumentManager` or directly into `KBRepository`. The pattern is what matters, not the abstraction layer it runs through.

### Introduce a Pyrite ODM layer between KBService and storage

The ODM sits between the service layer (KBService, TaskService, etc.) and the storage backends. It handles:

1. **Schema-versioned loading**: Load from file → parse frontmatter → check `_schema_version` → apply migration chain → return typed entry
2. **Validation on save**: Entry → validate against current schema → serialize → write file + update index
3. **Object versioning**: Track which schema version created an entry, which version last modified it
4. **Migration registry**: Ordered chain of migration functions per type, keyed by version range
5. **Backend abstraction**: Index operations delegated to pluggable backends (SQLite, Postgres)

### On-load migration pattern (from Ming)

Documents migrate lazily when loaded. The migration chain is a sequence of versioned transform functions:

```python
@migrate(type="finding", from_version=1, to_version=2)
def add_confidence_field(entry):
    if "confidence" not in entry.metadata:
        entry.metadata["confidence"] = 0.5
    return entry

@migrate(type="finding", from_version=2, to_version=3)
def rename_sources_to_evidence(entry):
    if "sources" in entry.metadata:
        entry.metadata["evidence"] = entry.metadata.pop("sources")
    return entry
```

On load, if an entry is at version 1 and current schema is version 3, the ODM runs both migrations in sequence. The entry in memory is always at the current version. If the entry was modified by migration, the ODM optionally writes the migrated version back to the file.

### Migration script

```bash
# Dry run — show what would change
pyrite schema migrate --kb research --dry-run

# Migrate all entries — forces load of every entry, triggering on-load migration
pyrite schema migrate --kb research

# Result: every entry is at the current schema version
# git diff shows exactly what changed in each file
```

The migration script is just a forced load of every entry. On-load migration does the actual work. After the script completes, every entry is at the current version — the "everything is migrated" event.

Because the source of truth is files in git, the migration produces a reviewable diff. You can run the migration on a branch, `git diff` the results, and merge when satisfied. This is something the original Ming/MongoDB pattern couldn't provide — git-backed storage turns schema migration into a reviewable PR.

### Two-layer storage architecture

The ODM splits storage into two concerns:

**Application state** (relational, needs ACID):
- Settings, user preferences
- Starred entries
- API keys (hashed)
- Collection user state
- Session data (web UI auth)
- Backend: SQLite or Postgres via SQLAlchemy (existing code, minimal changes)

**Knowledge index** (document-shaped, needs search):
- Entry metadata and content index
- Full-text search index
- Vector embeddings
- Block table
- Backlink graph
- Backend: pluggable via `SearchBackend` protocol — SQLite/FTS5 (default), Postgres/pgvector (server deployments). LanceDB evaluated and rejected (ADR-0016).

`pyrite index sync` rebuilds the knowledge index from source files, regardless of backend. Application state persists independently (backed up via `pyrite db backup`).

### Backend interface

The ODM defines a `SearchBackend` protocol that any index backend implements:

```python
class SearchBackend(Protocol):
    def upsert_entry(self, entry: Entry, embedding: list[float] | None) -> None: ...
    def delete_entry(self, entry_id: str, kb_name: str) -> None: ...
    def search_fts(self, query: str, kb_name: str, **filters) -> list[SearchResult]: ...
    def search_semantic(self, vector: list[float], kb_name: str, **filters) -> list[SearchResult]: ...
    def search_hybrid(self, query: str, vector: list[float], kb_name: str, **filters) -> list[SearchResult]: ...
    def get_entry(self, entry_id: str, kb_name: str) -> EntryRecord | None: ...
    def query_entries(self, kb_name: str, **filters) -> list[EntryRecord]: ...
    def get_backlinks(self, entry_id: str, kb_name: str) -> list[str]: ...
    def get_blocks(self, entry_id: str, kb_name: str) -> list[Block]: ...
    def rebuild(self, entries: Iterable[Entry]) -> None: ...
```

Current SQLite/FTS5 implementation wraps existing code behind this interface. Postgres/pgvector backend implements it with tsvector and pgvector. The service layer calls `search_backend.search_hybrid()` and doesn't know which engine is underneath.

### Configuration

```yaml
# pyrite.yaml
storage:
  # Application state (relational)
  app_backend: sqlite  # or postgres
  app_url: .pyrite/app.db  # or postgresql://...

  # Knowledge index (document search)
  index_backend: sqlite  # or postgres
  index_path: .pyrite/index.db  # or postgresql://...
```

Default is SQLite for both (current behavior, zero config). Postgres is opt-in for server deployments.

## Consequences

### Positive

- **Schema migration works.** On-load migration with a forced-load script, reviewable via git diff. Proven pattern from Ming/Allura.
- **Backend flexibility.** SQLite and Postgres as configuration choices. No service layer changes when switching backends.
- **Impedance mismatch resolved.** Document-shaped data through a document-native API. No more `json_extract()` for metadata queries.
- **Clean abstraction boundary.** The ODM is the stable API. Backends can evolve, be replaced, or run side-by-side without rippling through application code.

### Negative

- **Significant refactor.** The current `PyriteDB` class, `IndexManager`, and direct SQLAlchemy usage in services all need to route through the ODM. This is the largest architectural change since the service layer enforcement (#47).
- **Two data stores in production.** Application state in SQLite/Postgres + knowledge index potentially in a different backend. Two things to back up, monitor, and debug.
- **Migration complexity.** On-load migration is elegant but has edge cases: what if a migration fails mid-entry? What if the migration script is interrupted? Need rollback strategy (git provides this — the files haven't been committed yet).

### Implementation sequence (revised)

1. **Schema versioning (pre-0.8)**: `MigrationRegistry`, `_schema_version` tracking, `since_version` field semantics, `pyrite schema migrate` command. Hooks into existing `KBRepository` load/save paths. No new abstraction layer required.
2. **ODM interfaces + SQLite wrapping (0.9+)**: Define `SearchBackend` protocol, implement `SQLiteBackend` wrapping existing code, introduce `DocumentManager`. Route services through ODM. Move schema versioning hooks from `KBRepository` into `DocumentManager`.
3. **Alternative backends (0.9+)**: Postgres backend implementing `SearchBackend` (done, 66/66 conformance tests). LanceDB evaluated and rejected (ADR-0016).

Step 1 is the risk-reducing deliverable — schemas can evolve without breaking existing KBs. Steps 2-3 are architectural improvements that enable backend flexibility but aren't blocking launch.

## Related

- [Schema Versioning backlog item](../backlog/done/schema-versioning.md) — the migration story (decoupled, pre-0.8)
- [PostgreSQL Storage Backend](../backlog/done/postgres-storage-backend.md) — Postgres as app_backend and/or index_backend (done, 66/66 conformance)
- [Extension Type Protocols (ADR-0014)](0014-structural-protocols-for-extension-types.md) — `SearchBackend` itself is a protocol
- [ADR-0013: Unified Database Connection Model](0013-unified-database-connection-and-transaction-model.md) — current DB architecture being evolved
- [ADR-0003: Two-Tier Data Durability](0003-two-tier-data-durability.md) — files as source of truth, DB as derived index (unchanged by this ADR)
