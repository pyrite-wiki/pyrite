- **`pyrite task start` claims and starts a task in one write; `pyrite task finish --walk`
  walks it to done in one write (#1685).** `task start <id> -k <kb> -a <who>` moves an open
  task to `in_progress` through `claimed`, decided by the same compare-and-set as `task claim`:
  of two concurrent starts one wins, and the loser exits 1 with `claimed: false` and
  `current_status`, as a lost claim does. A task the same assignee already claimed moves on to
  `in_progress`. The result carries the task with its body and `parent: {id, title, body}`.
  `task finish <id> -k <kb> [--walk] [--reason R] [--by who]` moves a held task to `done`: with
  `--walk` through every declared intermediate state (claimed → in_progress → done, blocked →
  in_progress → done), without it only a direct edge, and the refusal names the path. Both
  record one `status_change_log` item per edge, each judged by the task workflow; the
  transition table is unchanged. MCP adds `task_start` and `task_finish` (write tier); REST adds
  `POST /api/tasks/{id}/start`.
