- **`pyrite task list --dispatchable` lists the tasks a worker can take now (#1686).**
  Open tasks with no assignee, no `parked_awaiting` reason, every dependency resolved and no
  children, highest priority first, then oldest. The JSON adds `counts` (tasks per reason left
  out) and `excluded` (each open task left out, with its reason: `empty_status`, `orphaned`,
  `has_children`, `assigned`, `parked`, `blocked` or `unreadable`). Every task it lists is one
  `task claim` accepts now. `--fields id,priority` projects each row, as on search (`id` and
  `kb_name` are always kept; an unknown name is named on stderr). MCP `task_list` takes
  `dispatchable` and `fields`, and REST `GET /api/tasks` takes `?dispatchable=true&fields=...`;
  all three give the same answer.
  Without the new options `task list` returns what it did before.
