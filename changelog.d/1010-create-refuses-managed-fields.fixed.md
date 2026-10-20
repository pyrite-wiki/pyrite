- A create now refuses a field Pyrite or the type manages (a task's
  `status_change_log`, `evidence`, `agent_context`, `assigned_at`; an ADR's
  `adr_number`; `file_path`, `provenance`, `extra_frontmatter`) at every
  spelling (top level, dotted `metadata.k`, under `metadata`, or an alias) on
  every create path: MCP `kb_create`/`kb_bulk_create`, CLI `create -f` and
  `import`, REST `POST` and import, `task create`, and the in-process
  `create_entry` (an owner such as `sw new-adr` names the field it sets with
  `owns=`), instead of writing or silently dropping it (#1010). `pyrite add
  FILE` is the one exception: it adopts a hand-written file's own values.
  Export output is not importable as-is: it carries read-only fields
  (`kb_name`, `file_path`, `indexed_at`, `content_hash`, `outlinks`,
  `backlinks`, a task's `assigned_at`) that an import now refuses; a decision
  on the round trip is pending.
