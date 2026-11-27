---
id: extract-basebackend-to-deduplicate-sqlite-and-postgres-backends
title: Extract BaseBackend to deduplicate sqlite and postgres backends
type: backlog_item
tags:
- tech-debt
- architecture
- storage
importance: 5
kind: refactor
status: done
priority: medium
effort: M
rank: 0
---

sqlite_backend.py (1139 lines) and postgres_backend.py (1107 lines) share substantial code: _entry_to_dict, _get_entry_sources, _get_entry_links, tag/source/link sync. Only FTS5 vs tsvector and vec vs pgvector differ. Extract shared ORM code into BaseBackend.
