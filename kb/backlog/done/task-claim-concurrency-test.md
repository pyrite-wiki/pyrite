---
id: task-claim-concurrency-test
title: "Execute the claim CAS concurrently: N processes race claim_task, exactly one wins"
type: backlog_item
tags: [testing, concurrency, tasks, agents, audit-2026-07]
links:
- target: epic-shared-instance-readiness
  relation: related
  kb: pyrite
importance: 5
kind: tech_debt
status: done
priority: high
effort: S
rank: 0
---

## Problem

The task-claim compare-and-swap is the ONE concurrency guard the
entire multi-agent fleet depends on ("the one concurrency guard in
the whole system" per the investigation-research skill), and it has
never been executed concurrently: zero threading/multiprocessing/
concurrent.futures usage anywhere in tests/. `test_task_service.py:
313-360` tests claim conflicts only as sequential simulation (claim,
then second claim fails). The CAS design is correct
(`UPDATE ... WHERE status = :from_status` + rowcount check), but
SQLite-under-concurrent-writers behavior (WAL mode, busy timeouts,
connection-per-process) is exactly the kind of thing that differs
between "correct design" and "correct under load."

## Fix

- One test: N processes (not threads — agents are processes; use
  multiprocessing with separate connections) race `claim_task` on a
  single open task. Assert exactly one winner, N-1 clean
  CONFLICT-class failures (not exceptions, not corrupted rows), and
  the task file on disk matches the winner.
- Repeat for the release/reset path (stale-claim reset racing an
  active claimer).
- Run against both backends (postgres via the conformance fixture
  once [[ci-make-green-and-load-bearing]] adds the service
  container).

## Acceptance criteria

- The race test exists, runs in CI, and fails if the rowcount check
  is removed (verified by mutation once, in the PR description).

## Progress

- [x] **8-process race test added** (2026-07-06,
  `tests/test_task_claim_concurrency.py`) — 8 OS processes (separate
  SQLite connections) race `claim_task` on one open task; asserts
  exactly one winner and N-1 clean CONFLICT results. Stale-claim
  reset vs. active-claimer race also covered.
- [x] **Mutation-verified** — while writing this test it caught a
  live bug: an uncommitted working-tree edit to
  `KBService.claim_entry()` had dropped the `status_clause` guard
  from the CAS `UPDATE`'s WHERE clause. With the guard missing, all
  8 concurrent claimants "won." Restored the guard (647d1d7); the
  test now demonstrably fails without it and passes with it.
- [ ] **Postgres backend not yet covered.** Test only exercises
  `PyriteDB`/SQLite. `ci-make-green-and-load-bearing` (done, fc241bf)
  added the pgvector-enabled postgres CI service, so running this
  race against `PostgresBackend`'s conformance fixture is now
  unblocked but not done. Leaving this item in_progress until that
  lands.

## Closed 2026-09-20

Verified shipped during the 0.24.2 pre-release review: tests/test_task_claim_concurrency.py exists with the start-barrier pattern; roadmap records done 2026-09-17 (-n auto at pre-push, suite 3m37s -> ~45s).
