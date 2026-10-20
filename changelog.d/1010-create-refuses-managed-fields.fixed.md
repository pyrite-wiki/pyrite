- A create now refuses a field Pyrite or the type manages (a task's
  `status_change_log`, `evidence`, `agent_context`, `assigned_at`; an ADR's
  `adr_number`; `file_path`, `provenance`, `extra_frontmatter`) at every
  spelling (top level, dotted `metadata.k`, under `metadata`; an alias of a
  managed field is folded too, though none is registered today) on every
  create path: MCP `kb_create`/`kb_bulk_create`, CLI `create -f`, `task create`
  and the in-process `create_entry`, instead of writing or silently dropping it
  (#1010). Two rules relax this. An in-process owner sets its own field with
  `create_entry(..., owns=frozenset({...}))` (`sw new-adr` names `adr_number`).
  An import (`pyrite import`, `POST /api/entries/import`) drops the keys an
  export carries about the index and location (`kb_name`, `file_path`,
  `indexed_at`, `content_hash`, `outlinks`, `backlinks`, ...) and a type's
  managed fields, and names every dropped key under `ignored` for its entry,
  so an export imports into another KB and nothing is dropped silently.
  `pyrite add FILE` still adopts a hand-written file's own values (pending the
  maintainer's explicit OK). REST `POST /api/entries` refuses only the
  `metadata` spelling: its body model ignores a managed field sent as a
  top-level key (200, nothing written); a follow-up makes it refuse.
