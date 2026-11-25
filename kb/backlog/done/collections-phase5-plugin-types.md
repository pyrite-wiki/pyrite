---
id: collections-phase5-plugin-types
type: backlog_item
title: "Collections Phase 5: Plugin Collection Types"
kind: feature
status: done
priority: low
effort: S
tags: [plugins, collections]
---

# Collections Phase 5: Plugin Collection Types

Allow plugins to define custom collection types with custom views.

## Scope

### Plugin protocol extension
- `get_collection_types()` method on PyritePlugin protocol
- Returns collection type definitions with: name, default view, fields, AI instructions
- `extends: collection` in kb.yaml for type inheritance

### Plugin-defined views
- Plugins can register custom Svelte view components
- View components receive collection data and render custom UI
- Example: investigation plugin registers "evidence board" view

### AI instructions for collections
- Collection types carry AI instructions (via type metadata system from #42)
- AI agents understand how to manage domain-specific collections

## Depends on
- Collections Phase 1 (foundation)
- Type Metadata and AI Instructions (#42, done)

## References

- [ADR-0011: Collections and Views](../../adrs/0011-collections-and-views.md)
- Parent: [Collections and Views](collections-and-views.md) (#51)
