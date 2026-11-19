---
id: collections-phase2-virtual-collections
type: backlog_item
title: "Collections Phase 2: Virtual Collections"
kind: feature
status: done
priority: medium
effort: M
tags: [architecture, schema, views, collections, core]
---

# Collections Phase 2: Virtual Collections

Define collections by query instead of folder path.

## Scope

### Query DSL
- `source: query` in `__collection.yaml` or collection entry metadata
- Filter operators: `entry_type`, `tags` (any/all), `date` (range), `kb_name`, `status`, field comparisons
- Sort: by any field, ascending/descending
- Limit: max entries returned

### Virtual collection evaluation
- `GET /api/collections/{id}/entries` evaluates the query and returns matching entries
- Query cached with TTL (configurable, default 60s) to avoid repeated evaluation
- Query validation: reject invalid field names, unsupported operators

### Frontend
- Collection creation UI: form to define query filters
- Live preview: shows matching entries as filters are adjusted
- Virtual collections appear alongside folder collections in navigation

### CLI
- `pyrite collections list` — list all collections with entry counts
- `pyrite collections query "type:backlog_item status:proposed"` — ad-hoc query

## Depends on
- Collections Phase 1 (foundation)

## References

- [ADR-0011: Collections and Views](../../adrs/0011-collections-and-views.md)
- Parent: [Collections and Views](collections-and-views.md) (#51)
