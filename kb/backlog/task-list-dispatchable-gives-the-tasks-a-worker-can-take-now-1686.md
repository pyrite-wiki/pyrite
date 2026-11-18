---
id: task-list-dispatchable-gives-the-tasks-a-worker-can-take-now-1686
title: task list --dispatchable gives the tasks a worker can take now (#1686)
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

One pure derived view, classify_tasks in pyrite/models/task_queue.py, decides which tasks are dispatchable and why the rest are not, and TaskService.list_tasks asks it for the CLI, MCP and REST. task list gains --dispatchable (priority order, counts) and --fields, and without them its output is unchanged.
