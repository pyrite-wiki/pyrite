---
id: adr-0013
type: adr
title: "Unified Database Connection and Transaction Model"
adr_number: 13
status: accepted
deciders: ["markr"]
date: "2026-02-25"
tags: [architecture, database, transactions, sqlite]
---

# ADR-0013: Unified Database Connection and Transaction Model

## Context

PyriteDB maintained two separate connections to the same SQLite database:

1. **`self.session`** (SQLAlchemy ORM) — used only by `kb_ops.py` and `starred.py`
2. **`self._raw_conn`** (raw sqlite3) — used by 76+ call sites for everything else

### Problems solved

**Dual-connection non-atomicity.** Operations spanning both connections were not atomic. Writes via `session.commit()` and `_raw_conn.commit()` were separate transactions.

**Raw connection leaks everywhere.** `db.conn` was used directly by services, plugins, and tests for all operations including writes.

**ORM models were decorative.** Entry, Tag, Link, etc. models existed but weren't used for writes — `upsert_entry()` used raw SQL `INSERT ... ON CONFLICT`.

## Decision

### ORM-primary approach

ORM is the primary data access method. Raw SQL is only used for **read-only search** operations that require FTS5/sqlite-vec virtual table access or complex graph CTEs.

### Phase 1 (implemented): ORM for all writes, raw SQL for read-only search

- **`connection.py`**: Derive `_raw_conn` from engine connection (not a separate connection); add `transaction()` context manager for ORM; add `execute_sql()` for raw SQL through session; deprecate `db.conn` property with `DeprecationWarning`
- **`crud.py`**: All Entry CRUD operations use ORM session — `session.get()`, `session.add()`, `session.query().delete()`, `session.flush()` for relationship sync. FTS5 triggers fire on the underlying INSERT/UPDATE/DELETE at SQLite level.
- **`user_ops.py`**: All User, Repo, WorkspaceRepo, EntryVersion operations use ORM session
- **`queries.py`**: Settings methods (get/set/delete) use ORM; all search/graph/analytics methods stay as raw SQL

### What stays as raw SQL (intentionally)

| Location | Why |
|----------|-----|
| `queries.py` search/graph/analytics | FTS5 virtual table, complex graph CTEs, read-only |
| `embedding_service.py` vec queries | sqlite-vec virtual table, read-only |
| `virtual_tables.py` DDL | Virtual table creation has no ORM equivalent |
| `connection.py` plugin table DDL | Dynamic schema from plugins |
| `connection.py` migrations | Schema evolution |
| Extension custom table queries | Plugin-defined tables, no ORM models |

### Phase 2 (future): Migrate service-layer `db.conn` access

Replace direct `db.conn` / `db._raw_conn` usage in services and tests with `db.execute_sql()` for reads or ORM for writes.

### Phase 3 (future): Migrate extension/plugin access

Update plugin protocol to use `execute_sql()` instead of `context.db.conn.execute()`.

## Consequences

### Positive

- **Atomic writes** — all write operations go through ORM session with proper commit/rollback
- **FTS5 triggers work** — ORM writes trigger the same SQLite INSERT/UPDATE/DELETE that fires FTS5 sync triggers
- **Transaction support** — `transaction()` context manager provides rollback on failure
- **Clean API** — `execute_sql()` for read-only queries, deprecation warning on `db.conn`

### Negative

- **Migration effort** — many test files still use `db.conn` (deprecated but working)
- **Slight overhead** — ORM adds a thin layer vs raw SQL for writes

### Risks

- **Session state after errors** — `upsert_entry` handles this with try/rollback/raise pattern
- **Concurrent access** — WAL mode handles read concurrency; write contention with background worker needs monitoring

## Related

- [ADR-0003: Two-Tier Data Durability](0003-two-tier-data-durability.md) — content in git, engagement in SQLite
- [ADR-0005: SQLAlchemy ORM with Alembic Migrations](0005-sqlalchemy-orm-with-alembic.md) — original ORM decision
- Backlog #40: Unify Database Connection and Transaction Management
