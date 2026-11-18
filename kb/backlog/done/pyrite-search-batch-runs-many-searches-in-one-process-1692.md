---
id: pyrite-search-batch-runs-many-searches-in-one-process-1692
title: pyrite search --batch runs many searches in one process (#1692)
type: backlog_item
tags:
- enhancement
importance: 5
kind: feature
status: in_progress
priority: high
assignee: agent:cloud-worker
effort: M
rank: 0
---

Add a --batch option to pyrite search that reads a JSON list of query objects and runs them in one process, one config load and one SearchService, returning each slot's result in input order. A refused query fills its own slot with the canonical error shape and does not fail its siblings.
