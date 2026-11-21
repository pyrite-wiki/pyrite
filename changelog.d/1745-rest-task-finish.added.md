- **REST `POST /api/tasks/{task_id}/finish` finishes a held task with the same rules as
  CLI `task finish` and MCP `task_finish` (#1745).** Delegates directly to
  `TaskService.finish_task` beside `/claim` and `/start`, accepting `?kb=<kb>` and JSON
  body `{walk?, reason?, by?, operator_override?}`. Enforces the holder rule (a signed-in
  caller acts as themselves; a non-holder is refused with 403 `FORBIDDEN` and nothing is
  written) and the explicit operator override (`operator_override: true`, recorded as
  `by: "operator", override: true`), walks the declared workflow to `done` with evidence,
  returns the same answer shape as CLI/MCP, and answers refusals with the canonical REST
  error shape. Guarded via the migrated ADR-0037 `authorize(Action.KB_WRITE, KB)`
  dependency so `REST_NOT_YET_MIGRATED_SIZE` does not grow.
