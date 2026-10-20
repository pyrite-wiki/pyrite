- A create now refuses a field Pyrite or the type manages (a task's
  `status_change_log`, `evidence`, `agent_context`, `assigned_at`; an ADR's
  `adr_number`; `file_path`, `provenance`, `extra_frontmatter`) at every
  spelling (top level, dotted `metadata.k`, under `metadata`, or an alias) on
  every create path: MCP `kb_create`/`kb_bulk_create`, CLI `create -f` and
  `import`, REST `POST` and import, `task create`, and the in-process
  `create_entry` (an owner such as `sw new-adr` names the field it sets with
  `owns=`), instead of writing or silently dropping it (#1010). `pyrite add
  FILE` is the one exception: it adopts a hand-written file's own values.
  An import (`pyrite import`, `POST /api/entries/import`) is the other
  half: a file an export wrote carries keys about the index and location
  (`kb_name`, `file_path`, `indexed_at`, `content_hash`, `outlinks`,
  `backlinks`) and a type's managed fields (a task's `assigned_at`,
  `status_change_log`); an import drops them and names each one under
  `ignored` for its entry, so an export imports into another KB and nothing
  is dropped silently. The direct create surfaces still refuse them.
