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
- A locked database met while opening, upgrading or repairing the index, or
  while `index build` writes a row, is now the retryable `STORAGE_ERROR` that
  says to wait, not a refusal. A read-only or full disk is told to check the
  disk. Advice to move the index aside and rebuild is given only where nothing
  else works, and it now says to run `pyrite db backup` first and names what a
  rebuild does not restore: KBs added with `pyrite kb add`, users, sessions,
  API keys, permissions, stars, reviews, settings and version history.
- `pyrite db backup` no longer tells you to move a healthy index aside when the
  fault is the backup's output path (a read-only directory or file, a full
  disk, a file there that is not a database): it says the output path is at fault and
  says the index was not changed. A locked index or output file is the
  retryable `STORAGE_ERROR` that says to wait, where a lock on the output file
  used to hang the command, and a failed backup no longer deletes a file that
  was already at the output path.
