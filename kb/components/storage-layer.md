---
id: storage-layer
type: component
title: "Storage Layer"
kind: module
path: "pyrite/storage/"
owner: "markr"
dependencies: ["sqlalchemy", "sqlite3", "sqlite-vec", "psycopg2 (optional)", "pgvector (optional)"]
tags: [core, storage]
---

Three-layer persistence architecture. At the bottom, `backends/` define a `SearchBackend` protocol implemented by SQLite and Postgres backends (Postgres is experimental in 0.25.x: an existing Postgres database may not upgrade between releases, [#718](https://github.com/pyrite-wiki/pyrite/issues/718)). In the middle, `PyriteDB` composes six mixin classes into a single facade — app-state tables use SQLAlchemy ORM while virtual tables (FTS5, vec_entry) use raw SQL. At the top, `KBRepository` handles file I/O, `IndexManager` syncs files to DB, and `DocumentManager` composes both into an atomic write path.

## Architecture

```
DocumentManager (write coordination)
├── KBRepository (file I/O: markdown + YAML)
├── IndexManager (file → DB sync, wikilinks, protocol fields)
└── PyriteDB (facade over 6 mixins)
    ├── ConnectionMixin (engine, backend init)
    ├── CRUDMixin (entry insert/update/delete)
    ├── QueryMixin (search, graph, analytics)
    ├── KBOpsMixin (KB registration, stats)
    ├── UserOpsMixin (users, repos, versions)
    └── ReviewOpsMixin (peer review state)
        └── SearchBackend protocol
            ├── SQLiteBackend (FTS5 + sqlite-vec)
            └── PostgresBackend (tsvector + pgvector)
```

## Key Modules

- `database.py` — PyriteDB mixin-composed facade
- `repository.py` — KBRepository file-based entry read/write
- `document_manager.py` — atomic save-register-index path
- `index.py` — IndexManager incremental sync
- `migrations.py` — SQLite schema evolution (16 versions)
- `models.py` — SQLAlchemy ORM table definitions

## Related

- [[search-backend-protocol]] — pluggable backend interface
- [[kb-repository]] — file layer
- [[document-manager]] — write coordination
- [[schema-migrations]] — schema evolution
