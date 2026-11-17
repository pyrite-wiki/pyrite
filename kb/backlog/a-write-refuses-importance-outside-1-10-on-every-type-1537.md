---
id: a-write-refuses-importance-outside-1-10-on-every-type-1537
title: A write refuses importance outside 1-10 on every type (#1537)
type: backlog_item
tags:
- bug
importance: 5
kind: bug
status: in_progress
priority: medium
assignee: agent:cloud-worker
effort: S
rank: 0
---

https://github.com/pyrite-wiki/pyrite/issues/1537: the range rule sits on EventEntry only; move it to Entry.validation_errors so the one write gate applies it to every type and surface; a value already on disk is reported, not refused.
