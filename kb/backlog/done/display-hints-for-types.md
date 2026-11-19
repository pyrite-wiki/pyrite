---
id: display-hints-for-types
type: backlog_item
title: "Display Hints for Types"
kind: feature
status: retired
priority: medium
effort: S
tags: [ui, types, schema]
---

# Display Hints for Types

Implement the `display` block from ADR-0009 so types can declare UI rendering preferences without code.

## Scope

- Parse `display` section from kb.yaml type definitions and plugin metadata
- Store display hints in TypeSchema
- Frontend reads display hints from schema API response
- Implement: `default_sort`, `card_fields`, `icon`, `preferred_view`
- Later: `color`, `group_by` (depends on more UI infrastructure)

## Display Vocabulary

| Hint | Values | Used by |
|------|--------|---------|
| `default_sort` | `date_desc`, `date_asc`, `title_asc`, `updated_desc`, `created_desc` | Entry list |
| `card_fields` | list of field names | Search results, sidebar cards |
| `icon` | icon name string | Type badges, sidebar |
| `preferred_view` | `editor`, `form`, `split`, `table` | Entry page default |

Hints are non-binding — UI uses sensible defaults when hints are absent.

## Blocked by

- #5 Schema-as-Config (TypeSchema needs rich fields first)
- Type Metadata and AI Instructions (display is part of type metadata)

## References

- [ADR-0009](../../adrs/0009-type-metadata-and-plugin-documentation.md)
