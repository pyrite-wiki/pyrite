---
id: collections-phase4-embedding
type: backlog_item
title: "Collections Phase 4: Embedding and Composition"
kind: feature
status: deferred
priority: low
effort: M
tags: [ui, editor, collections]
---

# Collections Phase 4: Embedding and Composition

Embed collections within entries using transclusion syntax.

## Scope

### Transclusion syntax for collections
- `![[collection-id]]` — embed the collection's default view inline
- `![[collection-id]]{ view: "table", limit: 5 }` — embed with view options
- Rendered as read-only inline component in TipTap editor

### Collection nesting
- A collection can contain other collections (subcollections)
- Nested display: expandable/collapsible subcollection cards

### Dashboard entries
- An entry composed primarily of embedded collections
- Example: "Project Dashboard" with embedded backlog (kanban), timeline, and metrics

## Depends on
- Collections Phase 2 (virtual collections)
- Block References Phase 3 (transclusion rendering)

## References

- [ADR-0011: Collections and Views](../adrs/0011-collections-and-views.md)
- Parent: [Collections and Views](done/collections-and-views.md) (#51)
