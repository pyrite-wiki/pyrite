# Coordinate tasks with people and agents

Use a registered, writable KB; this walkthrough uses `test-kb`. Start with
[getting started](getting-started.md). Explicit KB names keep identical task
IDs in different KBs unambiguous. Results and refusal fields belong to
[JSON contracts](json-contracts.md), rather than this walkthrough.

## Create, inspect and claim

```bash
pyrite task create "Write intro" -k test-kb
pyrite task list -k test-kb
pyrite task get write-intro -k test-kb
pyrite task claim write-intro -k test-kb --assignee alice
```

Tasks can also be scheduled with `--due-date <YYYY-MM-DD>` (`-d`) and linked to
other entries upon creation with `--link <target[:relation]>` (`-l`, repeatable).
MCP `task_create` accepts optional `due_date` and `links`.

A newly created task always starts in the `open` status. Setting a non-initial
status on creation (via `pyrite create -f status=...`, a body file's frontmatter,
or MCP `kb_create`) is refused; use `task claim` or `task update --status` to advance
its status.

A claim is atomic: competing agents do not both acquire an open task. Inspect
the refusal and choose another task when a claim loses the race. The following
second claim exits1; do not treat that as successful ownership.

<!-- expect-exit: 1 -->
```bash
pyrite task claim write-intro -k test-kb --assignee bob
```

## Record progress and release a stale claim

A checkpoint records work without pretending the task is complete. Reset is an
operator action for an abandoned in-progress or blocked task: it clears the
assignee and records why. It is not a routine way to steal a live agent's work.

```bash
pyrite task update write-intro -k test-kb --status in_progress
pyrite task checkpoint write-intro -k test-kb --message "Outline ready" --confidence 0.7
pyrite task reset write-intro -k test-kb --reason "Worker stopped" --operator reviewer
```

## Split work and inspect completion

```bash
pyrite task create "Publish guide" -k test-kb
pyrite task decompose publish-guide -k test-kb --child "Draft guide" --child "Review guide"
pyrite task claim draft-guide -k test-kb --assignee alice
pyrite task update draft-guide -k test-kb --status in_progress
pyrite task update draft-guide -k test-kb --status done
pyrite task claim review-guide -k test-kb --assignee bob
pyrite task update review-guide -k test-kb --status in_progress
pyrite task update review-guide -k test-kb --status done
pyrite task get publish-guide -k test-kb
```

The normal stored-state path is open → claimed → in_progress → done (or failed,
blocked or review). Follow the transition refusal rather than skipping states.
A parent's completion is derived from its children. Completing the children
makes the derived result complete without rewriting the parent's stored
status. Read both the stored status and `derived`; do not infer parent
completion from a stored status alone. `task status` is a deprecated alias;
new workflows use `task get`.

## Dependencies and MCP equivalents

Dependency traversal is exposed by MCP; the CLI get result also includes the
task's dependency IDs. Use full KB names for tool arguments.

| Job | CLI subcommand | MCP tool | Tier |
|---|---|---|---|
| Create | task create | task_create | write |
| List | task list | task_list | read |
| Inspect | task get | task_status | read |
| Claim | task claim | task_claim | write |
| Update status | task update | task_update | write |
| Release claim | task reset | — (operator CLI) | — |
| Checkpoint | task checkpoint | task_checkpoint | write |
| Split | task decompose | task_decompose | write |
| Blocking dependencies | get result | task_blocked_by | read |
| Critical path | get result | task_critical_path | read |

For the model behind these operations, see
[work coordination](../kb/maps/work-coordination-and-agent-teams.md).
