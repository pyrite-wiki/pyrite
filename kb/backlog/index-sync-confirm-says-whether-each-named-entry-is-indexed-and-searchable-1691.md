---
id: index-sync-confirm-says-whether-each-named-entry-is-indexed-and-searchable-1691
title: index sync --confirm says whether each named entry is indexed and searchable (#1691)
type: backlog_item
tags:
- enhancement
importance: 5
kind: feature
status: in_progress
priority: medium
assignee: agent:cloud-worker
effort: M
rank: 0
---

After a direct file write, an agent runs index sync and cannot tell whether its entries made it into the index. index sync --confirm <id-or-path> syncs, then reports each named entry as indexed, not_found, malformed, duplicate or skipped, with its schema findings and embedding state, and exits 3 when any is not confirmed, the same on the CLI, MCP and REST.
