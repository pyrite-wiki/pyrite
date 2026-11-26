---
id: sync-incremental-parses-every-file-even-when-not-stale
title: sync_incremental parses every file even when not stale
type: backlog_item
tags:
- tech-debt
- performance
- storage
importance: 5
kind: bug
status: done
priority: medium
effort: S
rank: 0
---

storage/index.py sync_incremental fully parses every markdown file to get entry ID before checking staleness. For a 1000-entry KB with 3 changes, this parses 1000 files. Should stat files first, then only load stale ones.
