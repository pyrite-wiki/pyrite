---
id: typed-object-references
type: backlog_item
title: "Typed Object References and Relation Indexing"
kind: feature
status: done
priority: high
effort: M
tags: [schema, relations, database, core]
---

# Typed Object References and Relation Indexing

Add an `object-ref` field type that creates typed links between entries, with DB indexing for reverse lookups and relation queries.

## Problem

Pyrite's existing `links` list stores semantic relationships (related, cites, refutes) but doesn't support structural relationships tied to specific fields (attendees, project, author). There's no way to query "which meetings reference this person" or "all investigations for this organization."

## Scope

- `object-ref` field type storing `{ref: entry-id}` in frontmatter
- Optional `target_type` constraint on object-ref fields
- New `entry_refs` DB table for indexed references
- Update indexer to extract object-ref fields using field schemas
- Reverse lookup API: `get_references_to(entry_id)` grouped by source type and field
- Update backlinks API endpoint to include object-ref sources
- Update graph data API to include object-ref edges

## Key Files

- `pyrite/storage/database.py` — entry_refs table, queries
- `pyrite/storage/index.py` — extract and index object-refs
- `pyrite/server/endpoints/entries.py` — backlinks enrichment, graph edges

## Dependencies

- Depends on: Schema-as-Config (field type system must exist for object-ref extraction)

## Acceptance Criteria

- [ ] Object-ref fields store and round-trip correctly in frontmatter
- [ ] Indexer extracts object-refs and populates entry_refs table
- [ ] Reverse lookup returns all entries referencing a given entry, grouped by field
- [ ] Backlinks API includes object-ref sources with field name
- [ ] Graph API includes object-ref edges with field-based relation labels
- [ ] Validation warns (not errors) when target doesn't exist

## References

- [ADR-0008](../../adrs/0008-structured-data-and-schema.md)
- [Design Doc](../../designs/structured-data-and-schema.md)
