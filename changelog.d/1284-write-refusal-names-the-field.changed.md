- A write refused because a field is missing or out of range now names that
  field on every surface (#1284). The CLI, MCP and REST answers carry
  `errors: [{field, rule, message?, expected?}]` beside the existing message and
  `suggestion`, so a caller can point at the field to fix instead of reading
  prose. `str(exc)` is unchanged, and `errors` is absent when the refusal is
  not about a field. `got` and `expected` are always JSON-safe: a date arrives
  as its ISO string and a non-finite float as its text, so an unquoted value in
  frontmatter cannot turn the refusal into an unrenderable answer. The CLI's
  `add --validate-only` builds its sentence from the rows and carries them with
  the refusal, instead of printing a row as a Python dict. The bulk answers keep
  the rows on the record they refused -- `kb_bulk_create` and
  `links bulk-create` results, REST `error_details` items -- and the link, update,
  `--field` and unknown-key refusals that named a field in prose now carry a row
  too, as do an enum value refused on an update and an update or create path no
  parser reads. The software-kb and journalism-investigation tool handlers keep
  those rows on the record they refused (`results[i].errors`,
  `entity_errors[i].errors`) instead of answering a code and a sentence. See
  `docs/json-contracts.md`.
