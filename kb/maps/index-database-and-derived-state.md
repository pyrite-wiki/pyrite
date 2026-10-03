---
id: map-index-database-and-derived-state
title: "What can I delete and rebuild? Topic map: the index, the database and derived state"
type: note
tags:
- map
- design
- index
- database
- storage
- derived-state
- sqlite
- postgres
- migrations
---

# What can I delete and rebuild?

Answers "what can I delete and rebuild?", "where does this row come from?",
"which backend rules apply?". Layer 2 under [[design]] (P4, P5, P7).

## The design today

1. The database holds the derived index and machinery, never the only copy of
   content. Content is the files. **decided** [[adr-0001]], [[adr-0029]].
2. Index operations sit behind the `SearchBackend` protocol with one
   conformance suite. SQLite is the local default; Postgres is the server
   backend. **decided** [[adr-0015]], [[adr-0016]] (LanceDB rejected on
   measurement).
3. Writes go through the ORM; raw SQL is for read-only FTS5, vector and graph
   queries and for plugin DDL. **decided, partly built** [[adr-0013]] Phase 1;
   Phases 2 and 3 (services and plugins off `db.conn`) are open.
4. Schema changes are migration files. **decided** [[adr-0005]] (Alembic plus a
   custom `MigrationManager` table: two mechanisms in the ADR's own text).
5. Fields that protocols or users filter on become indexed columns derived from
   frontmatter (assignee, priority, dates, location, `fips`, `state`).
   **decided** [[adr-0017]], [[adr-0026]].
6. Links, backlinks, edge endpoints and blocks are index rows rebuilt from the
   files and never written into other entries. **decided** [[adr-0022]],
   [[adr-0012]].
7. Rows that are not derived (claim lease times, stars, reviews, users,
   sessions, quotas) are state. A rebuild must not touch them; a declared
   registry of state tables says which they are. **accepted, not built**
   [[adr-0029]] section 4; **decided** ADR-0039 decision 7 and S1 (a generated
   docs list of what a rebuild loses).
8. One reconcile serves `index_kb`, `sync_kb` and `sync_incremental`; staleness
   is mtime or size, with a hash on rebuild and in `index health`; duplicate ids
   are reported and the lexicographically first path wins. **built**
   [[adr-0038]] step 2 and decided questions 3 and 4: `IndexManager.reconcile_kb`,
   with `plan_reconcile` for a read that writes nothing (delete and `index
   health` use it).
9. Indexing writes only `last_indexed` and `entry_count` to a KB's row.
   **decided** ADR-0039 decision 10.
10. List and search reads may lag the files and say when they were indexed.
    **decided** ADR-0042 decision 9.

## Invariants a test could check

- I2 after a reconcile the index has a row for exactly the ids that parseable
  files hold; I3 a second reconcile changes nothing ([[adr-0038]]).
- S1 every table is declared derived or state; dropping the derived tables and
  rebuilding loses no content and no configuration (ADR-0039).
- R3 indexing and every entry save change only `last_indexed` and
  `entry_count` (ADR-0039).
- Every `SearchBackend` implementation passes the same conformance suite
  ([[adr-0016]]).
- Deleting the database and rebuilding loses nothing written in a file
  (ADR-0041 acceptance 5).

## ADRs in reading order

[[adr-0001]], [[adr-0029]] (sections 1 and 4; read its status first),
[[adr-0015]], [[adr-0013]], [[adr-0038]], ADR-0039 (accepted). History:
[[adr-0016]] (the benchmark that settled LanceDB), [[adr-0005]], [[adr-0003]]
(superseded by [[adr-0029]]: the engagement tier became "runtime state").

## Where the code starts

Components [[storage-layer]], [[index-manager]], [[schema-migrations]],
[[search-backend-protocol]], [[sqlite-backend]], [[postgres-backend]]. Paths:
`pyrite/storage/index.py`, `pyrite/storage/backends/protocol.py`,
`pyrite/storage/migrations.py`.

## Tests that pin it

`tests/backends/test_backend_conformance.py`,
`tests/test_storage_invariants.py`, `tests/test_one_reconcile.py` (every
reconcile path against hand-authored KBs), `tests/test_one_reconcile_structure.py`
(fails on a second walk-and-write), `tests/test_migrations.py`,
`tests/test_layer_boundaries.py` (a surface reaches data only through a
service; the allowlist only shrinks), `tests/test_overlay_backend.py`.

## Known gaps

- No registry of state tables exists in code ([[adr-0029]] section 4); the
  KB registry is still bridged through a cache ([[adr-0029]], ADR-0039).
- About 70 raw-connection uses in core and 89 in extensions remain
  ([[adr-0013]] Phases 2 and 3; [[adr-audit-2026-10]]).
- A same-size edit that also keeps the mtime is not seen by a sync; `index
  health` catches it by hash ([[adr-0038]] decided question 4).
- [[adr-0005]]: which migration mechanism is the live path was not verified.
- Not decided: separate `state.db` (deferred with named triggers in
  [[adr-0029]] section 4).
