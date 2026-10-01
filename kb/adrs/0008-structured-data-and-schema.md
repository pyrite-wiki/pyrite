---
id: adr-0008
type: adr
title: "Structured Data and Schema-as-Config"
adr_number: 8
status: accepted
deciders: ["markr"]
date: "2026-02-23"
tags: [schema, structured-data, plugin, yaml]
---

# ADR-0008: Structured Data and Schema-as-Config

## Context

Pyrite's entry model is document-oriented: YAML frontmatter for structured metadata, Markdown body for freeform content. Entry type schemas are defined in Python dataclasses (core types in `core_types.py`, plugin types in extension code). This works well for document-heavy entries (notes, investigations, writeups) but creates friction for structured data objects where the value is in typed fields, not prose.

**The problem:** A user who wants a "meeting" type with a date field, attendee references, and a status dropdown must write a Python plugin. There is no way to define types with typed, validated fields through configuration alone.

**The opportunity:** Every major knowledge tool (Notion, Anytype, Capacities, Tana, Obsidian) has converged on a model where structured properties coexist with freeform content. Notion's "every row is a page" and Anytype's "Relations + Canvas" are the most relevant patterns for Pyrite because they maintain the structured/freeform split that Pyrite already has (frontmatter/body).

**Current state:**
- `TypeSchema` in `schema.py` tracks only `required`, `optional`, `subdirectory` — no field types
- `GenericEntry` already handles arbitrary frontmatter keys via the `metadata` dict
- Plugin entry types define fields as Python dataclass attributes — powerful but requires code
- `kb.yaml` types section exists but is minimal (name, required fields, subdirectory)
- PyYAML (`yaml.safe_dump`) does not preserve comments, quoting style, or blank lines on round-trip

**What we learned from competitor analysis:**
- Notion: 20+ property types, Relations (bidirectional), Rollups (aggregation), every row is a full page
- Anytype: Types + Relations (properties), Sets (live queries by type/property), local-first CRDT storage
- Capacities: Object types with property panels, Object Select for typed relations, two-way property linking
- Tana: Supertags as schema definitions with inheritance (Extend), "Options from supertag" for relations
- Obsidian: YAML frontmatter + Dataview queries + Metadata Menu for rich field types and fileClasses
- Heptabase: Tags as ad-hoc schemas with 9 property types, cards on whiteboards

## Decision

### 1. Three-Layer Schema Model

Schema definitions come from three sources, in order of increasing power:

| Layer | Definition | Power | Audience |
|-------|-----------|-------|----------|
| **Config schema** | `kb.yaml` types section | Field types, constraints, select options, object refs | Users, no code needed |
| **Plugin declared schema** | Plugin's `get_field_schemas()` | Same as config + programmatic generation | Plugin authors |
| **Plugin code** | Python dataclass + methods | Full power: hooks, computed fields, workflows, MCP tools | Plugin developers |

All three produce entries stored as the same Markdown+YAML files in git. The difference is where the schema lives and how much behavior is attached.

Config-defined types use `GenericEntry` (already exists) with automatic field validation from the schema. Plugin-defined types can optionally declare field schemas for auto-validation and UI form generation, reducing boilerplate while keeping custom code for behavior.

### 2. Rich Field Type System

Extend `TypeSchema` with a `fields` dict where each field has a type and optional constraints:

```yaml
# In kb.yaml
types:
  meeting:
    description: "Meeting notes"
    layout: document
    fields:
      date:
        type: date
        required: true
      attendees:
        type: list
        items: { type: object-ref, target_type: person }
      status:
        type: select
        options: [scheduled, completed, cancelled]
        default: scheduled
      action_items:
        type: list
        items: { type: text }
    subdirectory: meetings/
```

**Field types:**

| Type | YAML Storage | Validation |
|------|-------------|------------|
| `text` | string | optional: `format` (email, url, phone), `min_length`, `max_length` |
| `number` | number | optional: `min`, `max` |
| `date` | ISO 8601 string | date format validation |
| `datetime` | ISO 8601 string | datetime format validation |
| `checkbox` | boolean | true/false |
| `select` | string | must be in `options` list |
| `multi-select` | list[string] | each must be in `options` list |
| `object-ref` | `{ref: id}` or string | target must match `target_type` if specified |
| `list` | list | items validated by inner `items` type |
| `tags` | list[string] | (alias for multi-select without fixed options) |

This covers the 80% case. Plugin code handles the rest (computed fields, custom validation logic, complex constraints).

### 3. Two Entry Layouts

| Layout | Description | Use Case |
|--------|------------|----------|
| `document` | Rich frontmatter + substantial Markdown body | Notes, writeups, investigations (default) |
| `record` | Rich frontmatter + minimal/empty body | Contacts, tasks, bookmarks, data objects |

Both are Markdown files with YAML frontmatter. The difference is a hint to the UI: `document` gets a full editor, `record` gets a form-first view with an optional notes area. The storage format is identical.

### 4. Typed Object References

A new field type `object-ref` enables relational queries between entries:

```yaml
# In frontmatter
project: { ref: project-alpha }
attendees:
  - { ref: jane-doe }
  - { ref: bob-smith }
```

Object references can optionally constrain the target type (`target_type: person`). The indexer builds a reference table enabling reverse lookups ("which meetings reference this person?"), powering backlinks, relation views, and graph queries.

### 5. Migrate to ruamel.yaml

Replace PyYAML with `ruamel.yaml` for round-trip-safe YAML serialization:
- Preserves key ordering, comments, quoting style, blank lines
- Critical for git-native storage where noisy diffs undermine the model
- API-compatible for common operations; ~12 call sites to migrate
- Well-maintained, standard choice for round-trip YAML in Python

### 6. Schema Validation is Automatic

When a type has field schemas (from config or plugin), core validation automatically:
- Checks required fields are present and non-empty
- Validates field values match declared types
- Validates select/multi-select/list values are in the options list (see the 2026-10-01 amendment)
- Validates object-ref targets exist (soft warning, not hard error — targets may be in another KB)

Plugin validators remain for domain-specific rules beyond what the type system expresses.

### 7. Backward Compatibility

- Existing entries continue to work unchanged
- Types without field schemas work exactly as today (required/optional field names only)
- `GenericEntry` metadata promotion is unchanged — unknown keys still round-trip
- Plugins that don't declare field schemas continue to work
- Field schemas are additive — adding them to existing types doesn't break existing entries

## Consequences

### Positive

- Users can define structured types without writing Python code
- Plugin authors get automatic validation and UI form generation by declaring field schemas
- The web UI can auto-generate forms for any type based on its schema
- MCP tools can expose typed schemas to AI agents for structured data creation
- Database views (table/kanban/gallery) can use field types for column rendering
- Dataview-style queries can filter/sort/group by typed fields
- Object references enable relation views, backlinks, and graph visualization
- ruamel.yaml eliminates formatting noise in git diffs
- The extension builder skill can scaffold types with rich field schemas

### Negative

- ruamel.yaml is a new dependency (though it replaces PyYAML for serialization)
- Field schema validation adds complexity to the schema system
- Two layouts (document/record) adds a concept users need to understand
- Object reference resolution requires indexing infrastructure

### Risks

- Schema migration: if field type definitions change, existing kb.yaml files may need updates
- Performance: validating object-ref targets requires DB lookups (mitigated by soft warnings)
- Scope creep: temptation to add computed fields, rollups, formulas before basics are solid

## Amendment 2026-10-01: declared enums are read, checked once, and enforced per KB (#555, #47)

Measured before this amendment: a `values:` list was silently dropped by the parser, `validation.rules[].enum` was never evaluated, `list` fields were never checked, `schema validate` kept its own select-only copy of the check, and `index health` looked only at `status`. One research KB had 70+ off-list values on one field with every validator reporting clean. The maintainer's decision (2026-09-28): fix, warn, clean up, then enforce, with enforcement a per-KB option that is on by default.

1. **Forms read.** `options:` is canonical. `values:` is a permanent alias with no runtime warning (the kb-lifecycle skill taught it to real KBs). If both are given and differ, `options` wins and `schema validate` reports a schema-level warning naming the type and field. A `list` field is constrained by its own `options`/`values` or by `items: {options|values: [...]}`, checked per element. `validation.rules` entries with `enum: [...]` apply to any entry that has the field, per element for a list.
2. **One function.** `pyrite/schema/enum_check.enum_findings` makes every declared-enum finding (`field`, `rule`, `expected`, `got`, `origin`). `KBSchema.validate_entry` (the write path, `qa validate`, `ci`), `schema validate` and `index health` all call it.
3. **The switch.** `validation.enforce_enums` defaults to `true` and governs enum findings only: an off-list value is an error (write refused) when on, a warning when off. `validation.enforce` keeps governing everything else, so `enforce: false` (every `pyrite init` template) does not turn enums off. `allow_other: true` still makes a field's finding a warning. The switch does not extend to plugin validators' enums: plugin vocabularies stay code-owned. Only a YAML boolean `false` turns it off; any other value (`no`, `off`, `0`, `"false"`) leaves it on, and `schema validate` warns naming the value.
4. **Values already on disk.** On update, an off-list error (kb.yaml or a plugin's `rule: "enum"`, never a type error that shares a rule id) on a field whose value is the same after the update as before is returned as a warning, not re-validated, so an entry that is already off-list can still be updated (#47). The comparison is by value, so an echoed read counts; for list-valued fields it is per element, and an added off-list element is refused. A select keeps its value only by sending the same value, never by wrapping it in a list. Create has no before-state and is always strict. Non-enum findings on untouched fields behave as before.
5. **Health.** `index health` reports `off_list_values` rows from the index row plus its metadata JSON. kb.yaml rows roll up as `unhealthy` with the switch on and `warning` with it off. Plugin rows always roll up as `warning`, consistent with `invalid_statuses` and with point 3. `allow_other` rows are `info` and do not change the status. `status` stays in `invalid_statuses` and is never reported twice.

6. **Rule enums are KB-wide.** A `validation.rules` enum applies to every entry that has the field, whatever its type (type-scoped rules are a separate decision). A schema whose types share a field name with different vocabularies constrains it per type, with field `options:`, not with a rule. The shipped schemas follow this, and a test checks every rule enum in `docs/*.yaml`, the init templates and the plugin presets against the vocabularies of the types each declares: the desk schema's `status` rule lists every task workflow state, the `software` template and preset drop their ADR-only `status` rule (the software-kb validator checks each type's status), and the `movement` template's `status` enum moves onto `practice`.

Upgrade: a KB whose data drifts from its declared enums starts refusing off-list writes and its `index health` goes `unhealthy`. Set `validation.enforce_enums: false`, clean up guided by `qa validate` / `index health`, then remove the line.

## Related

- **ADR-0001**: Git-native storage — structured data stays in YAML frontmatter, preserving the model
- **ADR-0002**: Plugin system — plugins can declare field schemas alongside Python code
- **ADR-0003**: Two-tier durability — query results and views are computed, not stored
- **ADR-0007**: AI integration — MCP tools and AI features benefit from typed schemas
- **Backlog**: Dataview-Style Queries, Database Views, Extension Builder Skill
