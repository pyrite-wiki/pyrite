- A write refused because a field is missing or out of range now names that
  field on every surface (#1284). The CLI, MCP and REST answers carry
  `errors: [{field, rule, message?, expected?}]` beside the existing message and
  `suggestion`, so a caller can point at the field to fix instead of reading
  prose. `str(exc)` is unchanged, and `errors` is absent when the refusal is
  not about a field. `got` and `expected` are always JSON-safe: a date arrives
  as its ISO string and a non-finite float as its text, so an unquoted value in
  frontmatter cannot turn the refusal into an unrenderable answer. The CLI's
  `add --validate-only` builds its sentence from the rows and carries them with
  the refusal, instead of printing a row as a Python dict. See
  `docs/json-contracts.md`.
