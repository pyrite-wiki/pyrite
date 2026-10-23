---
id: schema-migrations
title: Schema Migrations
type: component
kind: module
path: pyrite/storage/migrations.py
owner: core
dependencies: []
tags:
- core
- storage
---

Sole source of truth for Pyrite's SQLite schema evolution. Tracks applied versions in a `schema_version` table and provides ordered, idempotent forward/rollback migrations, currently at version 29 (`entry.aliases`). `CURRENT_VERSION` in the module is the number to read; this page does not track it.

## Key Methods / Classes

- `Migration` (dataclass) — version, description, up SQL, down SQL
- `MigrationManager.migrate()` — apply all pending migrations in order
- `MigrationManager.rollback()` — revert to a prior version
- `MigrationManager.get_current_version()` — return the highest applied version
- `MigrationManager.get_pending_migrations()` — list unapplied migrations
- `MigrationManager.status()` — human-readable migration state summary

## Repair on open, under one lock (#1260, #1302)

A recorded version says which migrations *this file* ran, not what the model
expects: twice two branches took one migration number, and an index from
`arch/2026-10` at df6372f2 records 28 (meaning `entry.aliases` there) without
dev's 28 column `link.target_prefix`. So `ConnectionMixin._prepare_schema`
runs, in order and each as its own step: `create_all` (missing tables), the
virtual tables, the numbered migrations, then `repair_schema`
(`pyrite/storage/schema_repair.py`), which adds every `Base.metadata` column
and declared index still missing, and last the plugin tables. Keep the steps
separate: #718's explicit-upgrade mode must refuse before the first write.

- The repair only adds. A column SQLite cannot add in place (key, `UNIQUE`,
  `NOT NULL` without a server default), a failed migration, or a file that
  refuses the write is one `IndexSchemaRefusedError` (`STORAGE_ERROR`, a
  message naming versions and `table.column` only, and a suggestion).
- A second open of a current index runs no DDL (`PRAGMA schema_version`
  unchanged); `tests/test_index_schema_drift.py` checks it per shape.
- `schema_lock` (`flock` on `<index>.schema-lock`) is held from before the
  first connection to the end of the schema step, so two processes never
  migrate one file at once, and never both switch a restored, non-WAL file to
  WAL (SQLite refuses the second with "database is locked" whatever the busy
  timeout). Windows and filesystems without `flock` run unlocked.
- **A new migration handler returns early when its table is absent**
  (`tests/test_migrations.py` drives the manager on bare connections). A new
  column on the model needs no handler to reach old indexes: the repair adds
  it. Declare a new index on the model (`__table_args__`) so the repair can
  create it too.
- The index shapes old builds left on disk are fixtures in
  `tests/fixtures/index-shapes/`, rebuilt by `build_index_shapes.py` from the
  refs; add a shape when a release changes the schema.

## Consumers

- `ConnectionMixin` — invokes migrations during database init
- Repository classes at startup
- Test fixtures that need a fresh schema

## Related

- [[storage-layer]] — overall persistence architecture
