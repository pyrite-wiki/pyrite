---
id: map-work-coordination-and-agent-teams
title: "How does an agent claim work? Topic map: tasks, kanban, gates and state machines for agent teams"
type: note
tags:
- map
- design
- tasks
- kanban
- claims
- gates
- state-machine
- software-kb
- coordination
---

# How does an agent claim work?

Answers "how does an agent claim work?", "what is a lane, a gate, a
milestone?", "where do tasks live?". Layer 2 under [[design]] (P4, P8). This is
the software-kb and task design, not the repository's branch flow (see
[[map-development-process-and-releases]]).

## The design today

1. Work is pulled, not assigned: kanban with a WIP limit at the review stage,
   not sprints; `milestone` groups items by goal. **decided** [[adr-0019]].
2. Classify before modelling: a milestone is an entry, lanes are board
   configuration, the review queue is a computed view. **decided** [[adr-0020]].
3. Standards split by whether a check can be written: a
   `programmatic_validation` has a pass or fail gate; a `development_convention`
   is judgment. **decided** [[adr-0019]], [[adr-0020]] phase 1. Whether the split
   shipped is not verified; this KB still uses `standard`.
4. Quality gates sit in `board.yaml`, keyed by target status, evaluated at claim
   and transition; the default policy is `warn`; criteria are `checker`,
   `judgment` or `agent_responsibility`. **decided** [[adr-0021]].
5. A type may carry its own state machine, strict or relaxed; state membership is
   always enforced. **decided** [[adr-0027]].
6. A claim is a compare-and-swap on status and assignee; it is the one
   concurrency guard. A lease (`lease_expires_at`) sits on top, renewed by writes
   and `task checkpoint`; reclaim is itself a CAS. **decided** [[adr-0029]]
   section 4; the lease is **not built**.
7. Tasks live in coordination KBs or ephemeral KBs, never loose in canon, with
   `retention: record` or `lock`. **accepted, not built** [[adr-0029]] section 3.
8. Coordination KBs and runtime state are never routed through a user's
   worktree. **decided** [[adr-0029]] section 6; ADR-0044 marks them by
   `landing: canonical` (**proposed**).
9. A claim compares `status` against the file under a lock, not the index row.
   **proposed** ADR-0042 decision 10 (asks to amend [[adr-0029]] section 4).
10. A parent's completion, a task's blocked or ready state and a subtree's
    evidence are derived, not written by hooks. **proposed** ADR-0042 decision 4,
    ADR-0045 decision 7. Today `_parent_rollup` writes the parent after save.

## Invariants a test could check

- Of two agents racing to claim one task, exactly one succeeds (ADR-0045
  acceptance; [[adr-0029]] section 4).
- A status outside a type's `states` fails in strict and relaxed mode
  ([[adr-0027]]).
- A transition into a gated status evaluates that gate and returns the result;
  `warn` does not block, `enforce` does ([[adr-0021]]).
- No task lives loose in a canon KB; a `lock` task is reaped ([[adr-0029]],
  unbuilt).
- A claim in one user's overlay must not be invisible to another user's claim
  ([[adr-0029]] section 6): task state is shared.

## ADRs in reading order

[[adr-0019]] (the why), [[adr-0020]], [[adr-0021]], [[adr-0027]],
[[adr-0029]] sections 3 and 4, [[adr-0014]] (the `workflow`, `atomic_claim`,
`rollup` primitives), ADR-0045 (proposed). [[adr-0030]] (proposed) for runs.

## Where the code starts

Components [[task-service]], [[software-kb-extension]], [[rubric-checkers]],
[[qa-service]]. Paths: `pyrite/services/task_service.py`,
`pyrite/models/task.py` (`TASK_WORKFLOW`, `validate_status_change`),
`extensions/software-kb/src/pyrite_software_kb/board.py`,
`pyrite/services/kb_service.py` (`claim_entry`).

## Tests that pin it

`tests/test_task_claim_concurrency.py` (start barrier, one group deadline),
`tests/test_task_claim_endpoint.py`, `tests/test_task_service.py`,
`tests/test_task_dag.py`, `tests/test_gates.py`,
`tests/test_shipped_schemas_accept_workflows.py`.

## Known gaps

- Leases, `retention`, the link rule and the state-table registry are
  [[adr-0029]] work for 0.26 and absent; stale-claim handling still relies on
  conductor procedure.
- Hooks write parent status and `unblock_dependents` and evidence aggregation
  have no callers outside tests; ADR-0045 makes them derived (proposed).
- The `status:` word names three things across [[adr-0028]], [[adr-0020]] and a
  milestone ([[adr-0030]] open question 1).
- The alpha supported-surface entry (proposed) marks `task` and the `task_*`
  tools experimental, though Pyrite's own process runs on them.
- Not decided: the trust tiers and WIP calibration [[adr-0019]] describes (data
  needed, by its own text).
