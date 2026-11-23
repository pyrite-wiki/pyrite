---
id: structured-data-schema
title: "Schema-as-Config: Rich Field Types in kb.yaml"
type: backlog_item
tags: [schema, structured-data, core]
kind: feature
status: done
priority: high
effort: M
---

# Schema-as-Config: Rich Field Types in kb.yaml

Extend `TypeSchema` and `kb.yaml` to support typed field definitions. Users define entry types with typed, validated fields through configuration alone — no Python plugin required.

## Scope

- New `FieldSchema` dataclass with type, required, default, constraints
- Extend `TypeSchema` with `fields: dict[str, FieldSchema]` and `layout: str`
- Update `KBSchema.from_dict()` to parse field definitions from kb.yaml
- Update `KBSchema.validate_entry()` to validate field values against schemas
- Add `get_field_schemas()` optional method to `PyritePlugin` protocol
- Update `to_agent_schema()` to include field type information
- Support 10 field types: text, number, date, datetime, checkbox, select, multi-select, object-ref, list, tags
- Backward compatible: existing kb.yaml files without fields continue to work

## Key Files

- `pyrite/schema.py` — TypeSchema, FieldSchema, KBSchema, validation
- `pyrite/plugins/protocol.py` — new get_field_schemas() method
- `pyrite/plugins/registry.py` — aggregate field schemas from plugins

## Dependencies

None — this is foundational.

## Acceptance Criteria

- [ ] `kb.yaml` with field definitions parses correctly
- [ ] Entries validated against field schemas (type, required, constraints)
- [ ] Plugin types can declare field schemas via `get_field_schemas()`
- [ ] kb.yaml field overrides merge with plugin schemas
- [ ] Existing entries and types continue working unchanged
- [ ] `to_agent_schema()` includes field type information
- [ ] Tests cover all 10 field types and constraint combinations

## Completed

Implemented in Wave 2. New `FieldSchema` dataclass with 10 field types. Extended `TypeSchema` with `fields` and `layout`. `KBSchema.from_dict()` parses rich field definitions from kb.yaml. `validate_entry()` validates field values (type, range, select options, dates, checkboxes). Added `get_field_schemas()` to plugin protocol and registry. `to_agent_schema()` exports field type info. 34 new tests, 410 total passing. Fully backward compatible.

## References

- [ADR-0008](../../adrs/0008-structured-data-and-schema.md)
- [Design Doc](../../designs/structured-data-and-schema.md)
