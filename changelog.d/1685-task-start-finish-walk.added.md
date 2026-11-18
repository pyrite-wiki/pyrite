- **`pyrite task start` claims and starts a task in one write; `pyrite task finish --walk`
  walks it to done in one write (#1685).** `task start <id> -k <kb> -a <who>` moves an open
  task to `in_progress` through `claimed`, decided by the same compare-and-set as `task claim`:
  of two concurrent starts one wins, and the loser exits 1 with `claimed: false` and
  `current_status`, as a lost claim does. A task the same assignee already claimed moves on to
  `in_progress`, only while that assignee still holds it: a claim someone else took meanwhile is
  refused, naming them, and kept. The result carries the task with its body and `parent: {id, title, body}`.
  `task finish <id> -k <kb> --by <holder> [--walk] [--reason R]` moves a held task to `done`: with
  `--walk` through every intermediate state of the task workflow (claimed → in_progress → done, blocked →
  in_progress → done), without it only a direct edge, and the refusal names the path. Both
  record one `status_change_log` item per edge, each judged by the task workflow; the
  transition table is unchanged. Finish requires the caller to hold the task; operators have
  an explicit override (`--operator-override`, MCP `operator_override: true`), recorded on
  each item as `by: operator`, `override: true`. A caller who does not hold the task, or no
  caller, is refused (`FORBIDDEN`) and nothing is written. Finish is guarded on the CLI and
  MCP; REST has no finish route yet (see #1745). Edges are
  judged by the workflow every update uses: a KB's declared task `state_machine`, else the
  core table. A start whose file write is refused puts the index row back as it was,
  holder included; a plain `task claim`'s rollback is unchanged. MCP adds `task_start`
  and `task_finish` (write tier); REST adds `POST /api/tasks/{id}/start`.
