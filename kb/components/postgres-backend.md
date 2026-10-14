---
id: postgres-backend
title: PostgreSQL Backend
type: component
kind: service
path: pyrite/storage/backends/postgres_backend.py
owner: core
dependencies:
- pyrite.storage.backends.protocol
- psycopg2
- pgvector
tags:
- core
- storage
---

> **Experimental in 0.25.x.** The Postgres backend has no migration for columns added after alembic revision 006, so an existing Postgres database may not upgrade between releases ([#718](https://github.com/pyrite-wiki/pyrite/issues/718)). Pyrite says so once per process, on stderr and in the server log, when the backend is built; set `PYRITE_POSTGRES_EXPERIMENTAL_ACK=1` to silence it.

Experimental multi-user backend, not yet selectable in 0.25.x (nothing builds it outside the tests and benchmarks), implementing the SearchBackend protocol against PostgreSQL. Uses tsvector for full-text search and pgvector for semantic search, and is conditionally imported only when its dependencies are available.

## Key Methods / Classes

- Same public surface area as `SQLiteBackend` (full SearchBackend protocol)
- Uses SQLAlchemy Session internally for query construction and connection pooling
- `upsert_entry()`, `search()`, `search_semantic()`, `get_backlinks()`, `get_outlinks()`, `get_graph_data()`, and sub-table sync methods

## Consumers

- None in 0.25.x: `PyriteDB` always builds `SQLiteBackend`, and the `search_backend` and `database_url` settings are read nowhere. Today only the conformance tests (`tests/backends/`) and `benchmarks/run_all.py` construct it.

## Related

- [[search-backend-protocol]] — interface this class implements
- [[sqlite-backend]] — default single-user backend
- [[storage-layer]] — overall persistence architecture
