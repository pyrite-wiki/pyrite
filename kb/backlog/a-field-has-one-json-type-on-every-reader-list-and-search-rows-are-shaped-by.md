---
id: a-field-has-one-json-type-on-every-reader-list-and-search-rows-are-shaped-by
title: "A field has one JSON type on every reader: list and search rows are shaped by one function (#1503)"
type: backlog_item
tags:
- bug
importance: 5
kind: bug
status: in_progress
priority: high
assignee: agent:cloud-worker
effort: M
rank: 0
---

Every reader of index entry rows (ORM and raw SQL, SQLite and Postgres) returns its rows through one shaper in pyrite/storage/, so metadata is an object, aliases a list and declared fields are lifted the same way on every MCP tool, CLI --format json reader and REST route. A TEXT column value is cast by the field's declared type (KBSchema.type_fields) in that shaper, and the single-entry read goes through it too, so kb_get and the list readers agree and a value read and written back never changes the file's type.
