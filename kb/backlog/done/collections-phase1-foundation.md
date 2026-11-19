---
id: collections-phase1-foundation
type: backlog_item
title: "Collections Phase 1: Foundation"
kind: feature
status: done
priority: high
effort: M
tags: [architecture, schema, views, collections, core]
---

# Collections Phase 1: Foundation

Make folders into first-class collection objects via `__collection.yaml` and add basic list/table views.

## Scope

### `__collection.yaml` parsing
- IndexManager recognizes `__collection.yaml` files during sync
- Creates a `collection` entry in the index (type: collection)
- Fields: title, description, tags, icon, source (folder/query), view preferences
- `collection` added to built-in entry types in `ENTRY_TYPE_REGISTRY`

### Collection model
- Collection is an entry with extra fields: `source_type` (folder/query), `view_config` (JSON), `entry_filter` (JSON)
- Folder collections automatically include entries in that directory
- `GET /api/collections` — list all collections
- `GET /api/collections/{id}/entries` — list entries in a collection

### Basic views (frontend)
- `list` view — simple entry list with title, date, type badge (default)
- `table` view — columnar view with sortable headers, configurable visible fields
- View switcher component to toggle between views
- New route: `/collections/{id}` page

### Zero-migration guarantee
- Folders without `__collection.yaml` work exactly as before
- No schema changes to existing tables — collections are regular entries

## Does NOT include
- Virtual collections / query DSL (Phase 2)
- Kanban, gallery, timeline views (Phase 3)
- Transclusion embedding (Phase 4)
- Plugin collection types (Phase 5)

## References

- [ADR-0011: Collections and Views](../../adrs/0011-collections-and-views.md)
- Parent: [Collections and Views](collections-and-views.md) (#51)
