---
id: a-declared-list-field-holds-a-list-on-every-surface-1502
title: A declared list field holds a list on every surface (#1502)
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

A scalar sent to a field the KB schema declares as a list is stored as a one-item list by one coercion in KBService, on the CLI, MCP and REST, and the CLI no longer splits values on commas. qa validate reports a declared list field whose file value is not a list, and never rewrites it.
