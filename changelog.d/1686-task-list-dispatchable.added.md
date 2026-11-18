- **`pyrite task list --dispatchable` lists the tasks a worker can take now (#1686).**
  Open tasks with no assignee, no `parked_awaiting` reason, every dependency resolved and no
  children, highest priority first, then oldest. The JSON adds `counts` (tasks per reason left
  out) and `excluded` (each open task left out, with its reason: `orphaned`, `has_children`,
  `assigned`, `parked` or `blocked`). `--fields id,priority` projects each row, as on search
  (`id` and `kb_name` are always kept). MCP `task_list` takes `dispatchable` and `fields`, and
  REST `GET /api/tasks` takes `?dispatchable=true&fields=...`; all three give the same answer.
  Without the new options `task list` returns what it did before.
