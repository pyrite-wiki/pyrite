- A create now refuses a field Pyrite or the type manages (a task's
  `status_change_log`, `evidence`, `agent_context`, `assigned_at`; an ADR's
  `adr_number`; `file_path`, `provenance`, `extra_frontmatter`) at the top
  level as well as in `metadata`, on MCP `kb_create`/`kb_bulk_create`, CLI
  `create -f` and `import`, and REST import, instead of writing or silently
  dropping it (#1010).
