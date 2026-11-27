- **`pyrite orient` names the fields a plugin declares for a type (#232).**
  The schema block (`orient`, `kb_orient`, `kb_schema`, REST `orient` /
  `schema`, and `pyrite-admin schema`) lists each declared type's fields from
  the same merge create already uses: core, then `get_field_schemas()`, then
  `kb.yaml`. A `kb.yaml` field replaces the plugin's declaration of that
  field. software-kb publishes the select options its preset already uses, so
  a `backlog_item` lists `kind`, `status`, `priority` and `effort`. A
  plugin's fields stay on the type it named. orient now publishes `type_fields`
  for every declared type, including core types; a title-only create of a
  software-kb type writes the preset's empty fields (`kind: ""`, `effort: ""`,
  etc.).
