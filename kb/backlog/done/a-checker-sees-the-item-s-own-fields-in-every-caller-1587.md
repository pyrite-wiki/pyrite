---
id: a-checker-sees-the-item-s-own-fields-in-every-caller-1587
title: A checker sees the item's own fields in every caller (#1587)
type: backlog_item
tags:
- bug
importance: 5
kind: bug
status: done
priority: high
assignee: agent:cloud-worker
effort: M
rank: 0
---

One builder in pyrite/services/checker_entry.py makes the entry a rubric checker sees from the index: the item's metadata with its column-held fields (priority, status, assignee, the dates, ...) and tags laid over it, plus _tag_count, _outlink_count and _links. The board.yaml gate, the gate's dry run and QA (validate_entry and the whole-KB pass) all use it, so a checker gives the file's verdict in every caller.
