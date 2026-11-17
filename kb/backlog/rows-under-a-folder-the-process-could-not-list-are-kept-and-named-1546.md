---
id: rows-under-a-folder-the-process-could-not-list-are-kept-and-named-1546
title: Rows under a folder the process could not list are kept and named (#1546)
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

A reconcile (index sync, index build, kb reindex) keeps the index rows of every path under a folder it could not list, and of every file it could not stat or read, instead of retiring them. It names those folders and files, exits 3 (or 1 when nothing synced), and index health reports them as unhealthy; a dangling-link probe that could not tell reports unreadable, not missing.
