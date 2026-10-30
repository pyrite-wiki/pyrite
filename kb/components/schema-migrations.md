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
  `NOT NULL` without a server default, a non-constant default or a `STORED`
  generated column on a table that has rows), a failed migration, or a file
  that refuses the write is one `IndexSchemaRefusedError` (`STORAGE_ERROR`, a
  message naming versions and `table.column` only, and a suggestion). A
  non-constant default (`CURRENT_TIMESTAMP`) or a `STORED` generated column is
  accepted by SQLite on an empty table; the repair counts the table's rows and
  refuses at plan time only when it has one (#1318).
- A second open of a current index runs no DDL (`PRAGMA schema_version`
  unchanged); `tests/test_index_schema_drift.py` checks it per shape.
- `schema_lock` (`flock` on `<index>.schema-lock`) is held from before the
  first connection to the end of the schema step, so two processes never
  migrate one file at once, and never both switch a restored, non-WAL file to
  WAL (SQLite refuses the second with "database is locked" whatever the busy
  timeout). Windows and filesystems without `flock` run unlocked.
- **A new migration handler returns early when its table is absent**
  (`tests/test_migrations.py` drives the manager on bare connections). A new
  model column that is nullable or has a constant server default needs no
  handler to reach old indexes: the repair adds it. A `NOT NULL` column without
  a server default cannot be added in place, so it refuses every old index:
  give it a server default, or a migration that rebuilds the table. A
  non-constant default is addable only to an empty table, so a migration must
  add such a column without its default and backfill it. The repair adds a
  column without its `REFERENCES` clause. Declare a new index on the model
  (`__table_args__`) so the repair can create it too.
- Every `except` under `pyrite/` that turns a SQLite, SQLAlchemy or
  `MigrationError` fault into a Pyrite error raises what `storage_error_from`
  (`pyrite/storage/errors.py`) returns: a lock is a retryable
  `StorageBusyError` that says to wait, a read-only or full disk gets the disk
  suggestion, and only a schema this build cannot reach in place, or a file
  SQLite cannot read, is told to move the index aside, naming what only the
  index holds (`INDEX_ONLY_STATE`: `kb add` registrations, accounts, stars,
  reviews, settings, version history) and `pyrite db backup` first. Pass the
  advice as `suggestion=` (and `disk_suggestion=` when the disk at fault is
  not the index's, as in `db backup`'s destination); never set `.suggestion`
  on the result. `tests/test_index_schema_drift.py::test_every_conversion_site_raises_the_classifier_result`
  reads every module and fails on a handler that does otherwise, unless
  `NOT_THE_CLASSIFIERS_JOB` gives the reason.
- `pyrite db backup` (`services/index_backup.py`) is the first command a
  refusal sends a user to, so it tells the index's faults (header,
  `integrity_check`, a lock) from the output path's (read-only, full, not a
  database) and bounds Python's `Connection.backup`, which otherwise retries a
  lock for ever.
- `<index>.schema-lock` is never deleted: unlinking a flocked file lets two
  holders in.
- The index shapes old builds left on disk are fixtures in
  `tests/fixtures/index-shapes/`, rebuilt by `build_index_shapes.py` from the
  refs; add a shape when a release changes the schema.

## Consumers

- `ConnectionMixin` — invokes migrations during database init
- Repository classes at startup
- Test fixtures that need a fresh schema

## Related

- [[storage-layer]] — overall persistence architecture
