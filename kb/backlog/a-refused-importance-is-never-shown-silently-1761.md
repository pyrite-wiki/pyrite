---
id: a-refused-importance-is-never-shown-silently-1761
title: A refused importance is never shown silently (#1761)
type: backlog_item
tags:
- bug
importance: 5
kind: bug
status: in_progress
priority: medium
assignee: agent:cloud-worker
effort: M
rank: 0
---

A file whose importance is refused (7.5, 0, 11, high, NaN) keeps its legacy reading in readers but is never silent: qa validate reports importance_range with the whole-number 1-10 message and no rename advice; get and search JSON carry a warning on that entry; CSV writes a stderr note. Maintainer decision C on #1761.
