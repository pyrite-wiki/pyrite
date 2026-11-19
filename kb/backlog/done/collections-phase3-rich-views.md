---
id: collections-phase3-rich-views
type: backlog_item
title: "Collections Phase 3: Rich Views (Kanban, Gallery, Timeline)"
kind: feature
status: done
priority: medium
effort: L
tags: [ui, views, collections]
---

# Collections Phase 3: Rich Views

Add kanban, gallery, and thread views to collections.

## Scope

### Kanban view
- Group entries by a field (status, priority, custom field)
- Drag-and-drop between columns updates the field value
- Column headers show count
- Configurable: which field to group by, column order

### Gallery view
- Card layout with entry title, summary/excerpt, optional image
- Grid responsive to viewport width
- Click card to navigate to entry

### Thread view
- Chronological view for discussion-style collections
- Entry body displayed inline (not just title)
- Reply action creates new entry linked to parent

### View configuration
- `__collection.yaml` `view` field specifies default view and per-view config
- Frontend view switcher persists preference
- Each view type has its own config schema (e.g., kanban: group_by field)

## Depends on
- Collections Phase 1 (foundation — list/table views)

## References

- [ADR-0011: Collections and Views](../../adrs/0011-collections-and-views.md)
- Parent: [Collections and Views](collections-and-views.md) (#51)
