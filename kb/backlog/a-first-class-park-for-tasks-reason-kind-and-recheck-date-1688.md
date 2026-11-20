---
id: a-first-class-park-for-tasks-reason-kind-and-recheck-date-1688
title: "A first-class park for tasks: reason, kind and recheck date (#1688)"
type: backlog_item
tags:
- tasks
importance: 5
kind: feature
status: in_progress
priority: medium
assignee: agent:cloud-worker
effort: M
rank: 0
---

Item 4 of #1680. A task waits on something outside the queue (a reply, a ruling, a person's decision, a date). Adds declared optional fields park_kind and retest_after beside parked_awaiting on the core task type, one pure predicate parked_state(task, today) beside the dispatch classifier (pyrite/models/task_queue.py) that every view reads, task park/unpark on CLI and MCP, and a parked filter on task list (CLI, MCP, REST). Due rechecks are derived, never written back; a person park never returns to agents; unpark removes the keys.
