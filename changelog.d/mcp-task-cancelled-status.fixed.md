- **MCP `task_update` and `task_list` accept `status: cancelled` (#1504).**
  The MCP tool schemas built their own status enums from a hard-coded list that
  omitted `cancelled`, causing input validation failures when retiring tasks or
  filtering by cancelled status. Both schemas now derive their status enum
  directly from `TASK_STATUSES`, the `task_update` tool description details the
  legal transitions and mentions `task_claim`, and the transition refusal
  guidance clarifies that `cancelled` applies to any unfinished state.
