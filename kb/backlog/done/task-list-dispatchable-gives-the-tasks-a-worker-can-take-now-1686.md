---
id: task-list-dispatchable-gives-the-tasks-a-worker-can-take-now-1686
title: task list --dispatchable gives the tasks a worker can take now (#1686)
type: backlog_item
tags:
- enhancement
importance: 5
kind: feature
status: done
priority: high
assignee: agent:cloud-worker
effort: M
rank: 0
---

One pure derived view, classify_tasks in pyrite/models/task_queue.py, decides which tasks are dispatchable and why the rest are not; TaskService.task_listing asks it and builds the task-list body the CLI, MCP and REST return. Every task it lists is one task claim accepts now. task list gains --dispatchable (priority order, counts, excluded) and --fields, and without them its output is unchanged.
