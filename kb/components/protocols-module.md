---
id: protocols-module
title: "Entry Protocol Mixins"
type: component
tags:
- core
- architecture
- protocols
links:
- target: "0017-entry-protocol-mixins"
  relation: "implements"
kind: module
path: "pyrite/models/protocols.py"
owner: core
dependencies:
- "pyrite/models/base.py"
---

Composable mixin dataclasses for entry types ([[adr-0017]]). Five protocols:

- **Assignable** — `assignee`, `assigned_at`
- **Temporal** — `date`, `start_date`, `end_date`, `due_date`
- **Locatable** — `location`, `coordinates`
- **Statusable** — `status`
- **Prioritizable** — `priority`

Entry types compose protocols via multiple inheritance (e.g. `class TaskEntry(Assignable, Temporal, Statusable, Prioritizable, NoteEntry)`). Each mixin provides `_<protocol>_to_frontmatter()` and `_<protocol>_from_frontmatter()` helpers.

Exports `PROTOCOL_REGISTRY` (name→class mapping) and `PROTOCOL_FIELDS` (name→field list mapping) for runtime protocol discovery.

Protocol fields are stored in dedicated indexed DB columns (migration v9), enabling cross-type queries via `find_by_assignee()`, `find_overdue()`, `find_by_status()`, `find_by_location()` in `pyrite/storage/queries.py`.

## Decided, not yet built

- [[adr-0045]] decisions 2, 6 and 7: the mixins still supply `_protocol_to_frontmatter()` methods; protocol-derived operations are not yet one uniform protocol interface. Task completion is already derived, so this is the remaining general interface gap, not a claim that rollup still writes.
