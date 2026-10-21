- **A corrupt or truncated `index.db` now refuses with `STORAGE_ERROR` instead of a traceback (#922).**
  Every CLI command that touches the index (`get`, `create`, `search` in every
  mode, `backlinks`, `index sync`, `index build`, `index health`, `db backup`,
  `kb validate`, and the rest) now exits `1` with a structured `STORAGE_ERROR`
  refusal naming the index path and pointing to `pyrite index build` to
  rebuild from the Markdown source of truth. A healthy index is never
  refused: the page-count check is skipped while a non-empty `-wal` is
  present (a checkpoint in progress) or when the header count is not current.
  `kb validate` is a check command, so an unreadable index exits `1` rather
  than answering `0` from the YAML alone.
  Known limit: damage inside an interior page that the header check does not
  see surfaces as a `sqlite3.DatabaseError` in the middle of a command; only
  `search` maps it to `STORAGE_ERROR`, the others exit `1` with `NO_OUTCOME`.
  Thanks to @aazminu, whose #1017 this is.
