- **A corrupt or truncated `index.db` now refuses with `STORAGE_ERROR` instead of a traceback (#922).**
  Every CLI command that touches the index (`get`, `create`, `search` in every
  mode, `backlinks`, `index sync`, `index build`, `index health`, `db backup`,
  and the rest) now exits `1` with a structured `STORAGE_ERROR` refusal naming
  the index path and pointing to `pyrite index build` to rebuild from the
  Markdown source of truth. Thanks to @aazminu, whose #1017 this is.
