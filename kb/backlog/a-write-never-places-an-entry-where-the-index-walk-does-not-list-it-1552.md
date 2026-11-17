---
id: a-write-never-places-an-entry-where-the-index-walk-does-not-list-it-1552
title: A write never places an entry where the index walk does not list it (#1552)
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

https://github.com/pyrite-wiki/pyrite/issues/1552: an entry titled README is created, found, then retired by the next sync while its file stays on disk; one predicate for what the walk lists, asked by every writer; index health names entry files an earlier version left unlisted.
