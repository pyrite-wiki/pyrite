---
id: adr-0005
type: adr
title: "SQLAlchemy ORM with Alembic Migrations"
adr_number: 5
status: accepted
deciders: ["markr"]
date: "2025-09-01"
tags: [architecture, storage, database]
---

## Context

The storage layer was using raw SQLite with hand-written SQL. As the schema grew (FTS5, links, tags, plugin tables), migration management became error-prone.

## Decision

Migrate to SQLAlchemy ORM for the data model and add Alembic for schema migrations. Keep the raw connection available for FTS5 queries which SQLAlchemy doesn't handle natively.

## Consequences

- Schema changes are tracked as migration files
- Plugin tables still use raw DDL (IF NOT EXISTS) since they're dynamic
- Dual access pattern: ORM for standard CRUD, raw connection for FTS5 and plugin queries
- MigrationManager handles version tracking with a custom migrations table
- Amended 2026-11-19 (#718): SQLite keeps `MigrationManager`; Alembic is the upgrade path for Postgres only, run by `ensure_schema`. Every SQLite migration is answered once on the alembic side (a revision's `sqlite_versions`, or `NO_REVISION` in `pyrite/storage/alembic_chain.py`), and `tests/test_alembic_upgrade_path.py` enforces it
