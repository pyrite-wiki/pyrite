---
id: postgres-backend
title: PostgreSQL Backend
type: note
tags:
- core
- storage
---

Optional multi-user production backend implementing the SearchBackend protocol against PostgreSQL. Uses tsvector for full-text search and pgvector for semantic search, and is conditionally imported only when its dependencies are available.

## Key Methods / Classes

- Same public surface area as `SQLiteBackend` (full SearchBackend protocol)
- Uses SQLAlchemy Session internally for query construction and connection pooling
- `upsert_entry()`, `search()`, `search_semantic()`, `get_backlinks()`, `get_outlinks()`, `get_graph_data()`, and sub-table sync methods

## Consumers

- Deployments configured with a Postgres connection URL (via `PYRITE_DATABASE_URL` or equivalent config)

## Related

- search-backend-protocol — interface this class implements
- sqlite-backend — default single-user backend
- storage-layer — overall persistence architecture
