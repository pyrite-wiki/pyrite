- **`pyrite task create` and MCP `task_create` accept `--due-date` and `--link` (#1506).**
  `pyrite task create` takes `--due-date <YYYY-MM-DD>` (`-d`) and `--link <target[:relation]>`
  (`-l`, repeatable), matching `pyrite create`. With `--link`, the JSON document carries
  `"linked"` and `"failed_links"`; a link that does not resolve makes the outcome `partial`
  (exit code 3) with `finish_up` commands while keeping the created task. MCP `task_create`
  supports optional `due_date` and `links` (`[{"target": ..., "relation": ...}]`).
