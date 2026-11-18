# Coordinate tasks with people and agents

Use a registered, writable KB; this walkthrough uses `test-kb`. To initialize a dedicated task tracking KB, run `pyrite init -t task` or `pyrite init -t research-queue`. Start with
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

A claim is atomic: competing agents do not both acquire an open task. Inspect
the refusal and choose another task when a claim loses the race. The following
second claim exits1; do not treat that as successful ownership.

<!-- expect-exit: 1 -->
```bash
pyrite task claim write-intro -k test-kb --assignee bob
```

## Start and finish in one call

An agent working a queue makes the same walk on every task. `task start` claims
an open task and starts it in one write: the same compare-and-set as `task
claim` decides it, so a lost start exits 1 with `claimed: false`, like a lost
claim, and changes nothing. It records open → claimed → in_progress in the
task's `status_change_log` and returns the task with its body and its parent's.
Retrying `start` on a task you already claimed moves it on to in_progress. Only
the holder moves a claimed task on: if the claim changes hands meanwhile, the
retry is refused, naming who holds it now, and their claim stays.

`task finish` moves a held task to `done` in one write. Without `--walk` it
takes only a direct edge (in_progress → done, review → done); with `--walk` it
passes through every intermediate state of the task workflow and records each
edge, so no state is skipped. A refusal names the path `--walk` would take. A
task nothing holds (open) is not finished; cancel it instead.

Finish requires the caller to hold the task; operators have an explicit
override. The caller is `--by` (MCP: the signed-in account, or `by` with no
signed-in user) and must be the task's assignee; anyone else, or no caller at
all, is refused with `FORBIDDEN` and nothing is written. The operator (the
CLI, stdio MCP, an operator key) can finish a task it does not hold with
`--operator-override` (MCP `operator_override: true`); its log items record
`by: operator` and `override: true`.

Every edge is judged by the core task workflow, also in a KB whose `kb.yaml`
declares its own `state_machine` for tasks: updates do not apply a declared
state machine yet (#1709), so neither do `start` and `finish`. Where the
declared table and the core one disagree, the write is refused and nothing is
written.

```bash
pyrite task create "Fix typo" -k test-kb
pyrite task start fix-typo -k test-kb --assignee alice
pyrite task finish fix-typo -k test-kb --by alice --reason "Typo fixed"
pyrite task create "Fix link" -k test-kb
pyrite task claim fix-link -k test-kb --assignee bob
pyrite task finish fix-link -k test-kb --by bob --walk --reason "Link fixed"
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
blocked or review). Follow the transition refusal rather than skipping states;
`task start` and `task finish --walk` walk the same states in one call and
record each one.
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
| Claim and start | task start | task_start | write |
| Finish (walk to done) | task finish | task_finish | write |
| Update status | task update | task_update | write |
| Release claim | task reset | — (operator CLI) | — |
| Checkpoint | task checkpoint | task_checkpoint | write |
| Split | task decompose | task_decompose | write |
| Blocking dependencies | get result | task_blocked_by | read |
| Critical path | get result | task_critical_path | read |

For the model behind these operations, see
[work coordination](../kb/maps/work-coordination-and-agent-teams.md).
