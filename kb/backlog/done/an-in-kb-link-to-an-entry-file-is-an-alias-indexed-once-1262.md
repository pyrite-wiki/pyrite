---
id: an-in-kb-link-to-an-entry-file-is-an-alias-indexed-once-1262
title: An in-KB link to an entry file is an alias, indexed once (#1262)
type: backlog_item
tags:
- bug
importance: 5
kind: bug
status: done
priority: high
assignee: agent:cloud-worker
effort: S
rank: 0
---

Goal state: one entry per real file. A path inside a KB whose resolved target is another walked entry file of the same KB is an alias of that entry. It is indexed and counted once, at the real file, and reported as an alias, not as a duplicate id and not as malformed. index sync, index health and find_file agree. Today sub/alias.md -> ../a.md makes index sync report 'Duplicate ids: 1'.

Issue: https://github.com/pyrite-wiki/pyrite/issues/1262
