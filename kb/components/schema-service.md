---
id: schema-service
title: Schema Service
type: component
kind: service
path: pyrite/services/schema_service.py
owner: core
dependencies:
- pyrite.config
- pyrite.schema
tags:
- core
- service
---

Programmatic management of kb.yaml type schemas. Provides add, remove, show, and set operations for type definitions without manual YAML editing.

## Key Methods

- `add_type(kb_name, type_name, definition)` — adds a type definition to kb.yaml
- `remove_type(kb_name, type_name)` — removes a type definition
- `show_schema(kb_name)` — returns the full schema for a KB
- `set_field(kb_name, type_name, field_name, field_def)` — adds/updates a field on a type
- `migrate_entries(kb_name, *, entry_type, dry_run)` — the schema migration of a KB's entry files (#955, B6 P4): each file whose `_schema_version` is behind its type's gets the registered chain's difference (`migration_operations`: a `Set` per key added or changed, an `Unset` per key dropped, `_schema_version`) written onto its own bytes through `file_operations.apply` and published with compare-and-replace; returns `checked`, `migrated` and `errors` (per file). `pyrite schema migrate` prints these and exits on the errors; no CLI reaches storage for it.

## Consumers

- CLI: `pyrite schema diff`, `pyrite schema migrate`
- MCP: `kb_schema` tool (read), `kb_manage` tool (write)

## Related

- [[schema-validation]] — KBSchema that this service manages
- [[config-system]] — KBConfig provides path to kb.yaml
