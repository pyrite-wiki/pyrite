---
id: search-backend-protocol
title: SearchBackend Protocol
type: component
kind: protocol
path: pyrite/storage/backends/protocol.py
owner: core
dependencies: []
tags:
- core
- storage
- protocol
---

The pluggable index-layer abstraction. Defines 31 methods across entry CRUD, full-text search, semantic/vector search, graph traversal, tags, timeline, and folder queries. All index operations go through this protocol so backends can be swapped (SQLite ↔ Postgres).

## Key Methods

**Entry CRUD**

- `upsert_entry(entry_data)` — write/update an entry and sync sub-tables (links, tags, embeddings)
- `delete_entry(entry_id, kb_name)` — remove entry and all sub-table data
- `get_entry(entry_id, kb_name)` — fetch single entry as dict

**Search**

- `search(query, kb_name, ...)` — FTS5 full-text search with optional type/tag filters
- `search_semantic(query_embedding, kb_name, ...)` — vector KNN search over stored embeddings

**Graph**

- `get_backlinks(entry_id, kb_name)` — entries linking TO this entry
- `get_outlinks(entry_id, kb_name)` — entries this entry links TO
- `get_graph_data(kb_name, ...)` — full graph nodes + edges for visualization

**Aggregation**

- `get_all_tags(kb_name)` — tag aggregation across the KB
- `get_timeline(kb_name, ...)` — date-ordered event listing

## Implementors

- `SQLiteBackend` (`pyrite/storage/backends/sqlite_backend.py`) — default backend using FTS5 + sqlite-vec
- `PostgresBackend` (`pyrite/storage/backends/postgres_backend.py`) — backend using pg_vector. **Experimental in 0.25.x**: an existing Postgres database may not upgrade between releases ([#718](https://github.com/pyrite-wiki/pyrite/issues/718)); it says so once per process at construction.

## Consumers

- `PyriteDB` (`pyrite/storage/database.py`) — delegates all index reads/writes through this protocol
- Embedding services — call `upsert_entry` to persist generated vectors

## Related

- [[storage-layer]] — PyriteDB and the overall storage architecture
- [[document-manager]] — write-path coordinator that triggers upserts
