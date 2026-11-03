---
id: entry-protocol-mixins
title: "Entry Protocol Mixins: Composable Field Patterns"
type: backlog_item
kind: feature
status: done
priority: high
effort: L
tags:
  - architecture
  - protocols
  - type-system
  - core
links:
  - target: "0017-entry-protocol-mixins"
    relation: "implements"
  - target: "extension-type-protocols"
    relation: "prerequisite_for"
---

## Summary

Implement 5 composable protocol mixin dataclasses (Assignable, Temporal, Locatable, Statusable, Prioritizable) that entry types compose via multiple inheritance instead of duplicating fields. Enables cross-type queries, consistent DB indexing, and lets user-defined types in kb.yaml opt into standardized fields.

## Completed Work

- [[adr-0017]] documenting the 5 protocols, field names, DB column plan
- `pyrite/models/protocols.py` with 5 mixin dataclasses and PROTOCOL_REGISTRY
- Promoted `importance` to base Entry class
- Migrated core types (EventEntry, PersonEntry, OrganizationEntry, DocumentEntry) to use protocol mixins
- Added 7 indexed DB columns for protocol fields + migration v9
- Plugin `get_protocols()` and registry `get_all_protocols()` aggregation
- `protocols` field on TypeSchema for kb.yaml user-defined types
- Migrated 3 extensions: task, software-kb, cascade
- 4 cross-type query methods: find_by_assignee, find_overdue, find_by_status, find_by_location
- 4 MCP tools for protocol-aware queries
- Protocol declarations in CORE_TYPE_METADATA
- 45+ new tests
