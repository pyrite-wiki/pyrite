---
id: task-start-and-task-finish-walk-1685
title: task start and task finish --walk (#1685)
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

Add task start, which claims and starts an open task in one compare-and-set write, and task finish --walk, which walks a held task through every declared edge to done in one write with one status_change_log item per edge. CLI, MCP and REST (start) return one shape from one service function.
