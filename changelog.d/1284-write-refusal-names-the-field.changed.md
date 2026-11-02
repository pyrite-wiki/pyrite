- A write refused because a field is missing or out of range now names that
  field on every surface (#1284). The CLI, MCP and REST answers carry
  `errors: [{field, rule, message?, expected?}]` beside the existing message and
  `suggestion`, so a caller can point at the field to fix instead of reading
  prose. `str(exc)` is unchanged, and `errors` is absent when the refusal is
  not about a field. See `docs/json-contracts.md`.
