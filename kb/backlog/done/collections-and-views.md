---
id: collections-and-views
type: backlog_item
title: "Collections and Views"
kind: feature
status: done
priority: high
effort: XL
tags: [architecture, schema, views, ui, core]
---

## Summary

Make folders and queries into first-class collection objects with configurable views. `__collection.yaml` in any folder gives it identity, metadata, and view preferences. Virtual collections define queries instead of folder paths. Collection types (investigation package, kanban board, discussion thread) are defined via kb.yaml and plugins.

See [ADR-0011](../../adrs/0011-collections-and-views.md) for the full design.

## Subsumes

This item unifies and replaces several existing backlog items:

- **#28 Dataview-Style Queries** — virtual collections with `source: query` are dataview
- **#29 Database Views (Table/Board/Gallery)** — collection view types (table, kanban, gallery, etc.)
- **#43 Display Hints for Types** — view configuration is now per-collection, not just per-type

Parts of **#17 Block References and Transclusion** are also covered (collection embedding via `![[collection]]{ view options }`), but #17 retains block-level transclusion which is orthogonal.

## Phases

### Phase 1: Foundation (M) — DONE
- `collection` built-in entry type
- `__collection.yaml` parsing in KBRepository and IndexManager
- Folder collections indexed as entries
- `list` and `table` views in web frontend
- Blocked by: #42 (type metadata), #5 ✅ (schema)

### Phase 2: Virtual Collections (M) — DONE
- `source: query` parsing and execution
- Query DSL: entry_type, tags, date range, field comparisons
- `GET /api/collections/{id}/entries` endpoint
- CLI: `pyrite collections list`, `pyrite collections query`
- Blocked by: Phase 1

### Phase 3: Rich Views (L) — DONE (Kanban + Gallery)
- Kanban view with drag-and-drop field updates
- Gallery view with responsive card grid
- View switcher extended for kanban/gallery
- PATCH /api/entries/{id} for field updates
- Thread view deferred to Phase 4+
- Blocked by: Phase 1

### Phase 4: Embedding and Composition (M)
- `![[collection-id]]{ view options }` transclusion syntax
- Inline collection rendering in editor
- Collection nesting
- Blocked by: Phase 2, #17 (transclusion basics)

### Phase 5: Plugin Collection Types (S)
- `extends: collection` in kb.yaml
- Plugin-defined view types (custom Svelte components)
- AI instructions for collection management
- Blocked by: Phase 1, #42 (type metadata)

## Acceptance Criteria

- [ ] `__collection.yaml` in a folder creates an indexed, searchable collection object
- [ ] Virtual collections with `source: query` return matching entries
- [ ] At least 4 view types: list, table, kanban, gallery
- [ ] Collections embeddable in other entries via transclusion syntax
- [ ] Plugin-defined collection types with custom views
- [ ] Existing folders work unchanged (zero migration)
