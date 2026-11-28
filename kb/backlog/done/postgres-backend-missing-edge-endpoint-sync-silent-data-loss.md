---
id: postgres-backend-missing-edge-endpoint-sync-silent-data-loss
title: Postgres backend missing edge endpoint sync — silent data loss
type: backlog_item
tags:
- bug
- storage
- postgres
importance: 5
kind: bug
status: done
priority: high
effort: S
rank: 0
---

sqlite_backend calls _sync_edge_endpoints during upsert_entry but postgres_backend does not. Edge endpoint data is silently dropped on Postgres. Methods get_edge_endpoints, get_edges_by_endpoint, get_edges_between exist only in SQLite backend, not in protocol or postgres.
