- Opening an index now adds any column or index the current schema expects
  that the file lacks, whatever schema version it records. An index built on
  `arch/2026-10` before the sprint merge (schema 28 without
  `link.target_prefix`) no longer crashes `get`, `backlinks` and `index health`
  with `OperationalError`, and `index build` on it no longer exits 0 with every
  file "could not be parsed" and an empty index: a database fault during a
  build is now a `STORAGE_ERROR` with a suggestion, and the rows already there
  are kept (#1260). Where the repair cannot run (a column SQLite cannot add in
  place, a migration that fails), the open is refused once with a
  `STORAGE_ERROR` that says which version and column, and how to rebuild.
- Two Pyrite processes opening an old index at the same moment no longer
  crash with `duplicate column name` or `database is locked`: the migration and
  repair run under one cross-process lock, and the second process waits and
  finds the index current (#1302).
