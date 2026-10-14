---
id: map-types-and-schema
title: "How do I declare a type and its fields? Topic map: types, schema, enums, edge types and state machines in kb.yaml"
type: note
tags:
- map
- design
- types
- schema
- kb-yaml
- validation
- enums
- fields
- state-machine
---

# How do I declare a type and its fields?

Answers "how do I declare a type in `kb.yaml`?", "what validates a field?",
"where does a type's behaviour come from?". Layer 2 under [[design]] (P6, P8).
For code-defined types, protocols and plugins see
[[map-plugins-protocols-and-extensions]].

## The design today

1. A type's schema comes from three layers of increasing power: `kb.yaml`
   (no code), a plugin's declared field schemas, plugin code. All produce the
   same Markdown files. **decided** [[adr-0008]] section 1.
2. Field types: `text`, `number`, `date`, `datetime`, `checkbox`, `select`,
   `multi-select`, `object-ref`, `list`, `tags`. A `document` or `record`
   layout is a UI hint. **decided** [[adr-0008]] sections 2 to 4.
3. Validation is automatic from the schema (required, type, options, object-ref
   as a soft warning); plugin validators add domain rules. **decided**
   [[adr-0008]] section 6.
4. Declared enums: `options:` is canonical and `values:` a permanent alias; one
   function makes every finding; `validation.enforce_enums` is on by default;
   an off-list value already on disk is a warning when the update leaves it
   unchanged, an error when it is added. **decided** [[adr-0008]] amendment
   2026-10-01.
5. Types carry `ai_instructions`, field descriptions and display hints;
   `kb.yaml` overrides plugin metadata; hints are not contracts. **decided**
   [[adr-0009]].
6. KB policy (minimum sources, enforcement) stays in the KB, never in a plugin.
   **decided** [[adr-0009]] decision 7.
7. A type may declare `protocols: [...]` and gain those fields; protocol
   fields and `fips`/`state` become indexed columns. **decided** [[adr-0017]],
   [[adr-0026]].
8. A type may carry its own `state_machine` (strict or relaxed transitions;
   membership of the state list is always enforced); the question is type-level,
   not a KB toggle. **decided** [[adr-0027]].
9. A relationship that carries data is an edge type: declared endpoints,
   optional `accepts`, all endpoints required, derived backlinks in the index
   only, a deleted endpoint leaves a broken reference and a QA warning.
   **decided** [[adr-0022]].
10. Schema versions migrate on load, and `pyrite schema migrate` forces a load
    of every entry. **decided** [[adr-0015]]. ADR-0042 decision 7 (accepted)
    makes migration an explicit command and never a side effect of a save.
11. A type's aliases, migrations, create-time defaults and reference fields are
    declared in its schema, not in class attributes. **decided** ADR-0045
    decisions 8 and 9.

## Invariants a test could check

- Every declared-enum finding comes from `enum_findings`; `schema validate`,
  the write path, `qa validate` and `index health` agree ([[adr-0008]]).
- Every `validation.rules` enum in `docs/*.yaml`, the init templates and the
  plugin presets matches the vocabularies of the types it applies to
  ([[adr-0008]] point 6).
- A state outside a type's `states` fails in both strict and relaxed mode
  ([[adr-0027]]).
- An edge-type entry with a missing or wrong-typed endpoint is rejected
  ([[adr-0022]]).
- Only `enforce_enums: false` (a YAML boolean) turns enum enforcement off
  ([[adr-0008]] point 3).

## ADRs in reading order

[[adr-0008]], [[adr-0009]], [[adr-0027]], [[adr-0022]], [[adr-0017]] and
[[adr-0026]] (columns), [[adr-0015]] (versioning). ADR-0045 (accepted) for
aliases and migrations. History only: [[adr-0011]] (collections), [[adr-0020]]
(an example of deciding entry versus config).

## Where the code starts

Components [[schema-validation]], [[schema-service]], [[entry-factory]],
[[entry-model]], [[protocols-module]]. Paths: `pyrite/schema/kb_schema.py`,
`pyrite/schema/field_schema.py`, `pyrite/schema/enum_check.py`,
`pyrite/models/core_types.py`, `pyrite/models/task.py` (state machine).

## Tests that pin it

`tests/test_enum_enforcement.py`, `tests/test_schema_fields.py`,
`tests/test_schema_validate.py`, `tests/test_schema_versioning.py`,
`tests/test_schema_edge_types.py`, `tests/test_edge_endpoint_validation.py`,
`tests/test_shipped_schemas_accept_workflows.py`.

## Known gaps

- Plugin vocabularies are code-owned and outside the enum switch
  ([[adr-0008]] point 3); a KB made by `pyrite init -t software` is registered
  as generic and gets no status check (#572, named in [[adr-0008]]).
- Aliases: `field_aliases(type, kb_schema)` (`pyrite/schema/field_aliases.py`)
  resolves `{alias: target}` from core schema < plugin `get_type_metadata()` <
  `kb.yaml` (#697). The `FRONTMATTER_ALIASES` class attribute is a deprecated
  shim that warns. A `kb.yaml` alias is declared and validated (`pyrite schema
  validate`) now; it is honoured on write in B6, and not at load (a generic
  class does not read it). Link-item keys (`to` -> `target`) are not covered yet (B6).
- On-load migration may write the migrated entry back ([[adr-0015]]) against
  P3; ADR-0042 decision 7 is accepted.
- Which of [[adr-0011]]'s five phases shipped was not verified
  ([[adr-audit-2026-10]]); "New collection" returns an error in the web UI
  (the alpha supported-surface entry cites #480).
- Decided 2026-10-03: the alias declaration is `field_aliases: {alias: target}`
  (ADR-0045 question 3). Test: `tests/test_field_aliases.py`.
