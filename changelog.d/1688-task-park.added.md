- **A first-class park for tasks: `pyrite task park` / `task unpark`, with a kind and a recheck
  date (#1688).** `task park <id> -k <kb> --reason R [--kind person|external]
  [--retest-after YYYY-MM-DD]` records why a task waits, who must act and when to look again,
  in two new optional fields of the core `task` type (`park_kind`, `retest_after`) beside
  `parked_awaiting`; the status never changes, and `task unpark` removes the three keys. One
  predicate decides a task's park state (`none`, `parked`, `due`, `person`) from those fields
  and today's date, and every view reads it: `task list --dispatchable` leaves a parked task
  out until its date, then lists an open one with `recheck: true` and every other live one
  (in progress, claimed, blocked) under the new `due_rechecks`; a `person` park never returns
  to agents. `task list --parked/--unparked` (MCP `task_list` `parked`, REST
  `GET /tasks?parked=`) filters by it. MCP adds `task_park` and `task_unpark` (write tier).
  `qa validate` reports an unknown `park_kind`, a `retest_after` that is not a date and a date
  with no reason (rule `task_park`). A whitespace-only `parked_awaiting` now reads as no reason
  on every `task list` path.
