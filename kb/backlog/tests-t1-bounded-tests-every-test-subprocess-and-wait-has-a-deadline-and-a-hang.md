---
id: tests-t1-bounded-tests-every-test-subprocess-and-wait-has-a-deadline-and-a-hang
title: 'Tests T1: bounded tests - every test, subprocess and wait has a deadline, and a hang names itself'
type: backlog_item
tags:
- quality
- test
importance: 5
kind: tech_debt
status: in_progress
priority: high
effort: M
rank: 0
---

Source: test-architecture review 2026-09-25 (`desk/notes/reviews/test-architecture-2026-09-25.md`, theme T1). Milestone 0.26, as the maintainer decided on 2026-09-24. Related: #372 (a 2h45m full-suite run that named no culprit).

## Problem

There is no per-test timeout and no `faulthandler_timeout`. 76 of the 81 subprocess calls in tests have no timeout. Two tests are written so that a regression makes them hang instead of fail (`test_websocket_delivery.py::_event_then_marker`, `test_task_dag.py::test_handles_cycle_gracefully`), and there are two bare `t.join()` calls (`test_websocket_delivery.py:248, 287`). CI's main pytest job has no `timeout-minutes`; only the e2e and Playwright jobs do. In #372 a run took 2h45m and named no culprit.

## Target shape

- `faulthandler_timeout` in the pytest config, so a stuck test dumps every thread's stack.
- pytest-timeout, pinned in the dev extras, with a generous suite default and `timeout_func_only`.
- Every `subprocess.run`/`check_output` in tests has a `timeout=`.
- Websocket receives go through a helper with a deadline.
- The cycle test runs its call under a deadline, so a regression fails instead of hanging.
- CI's main pytest job has `timeout-minutes`.

## Groom 2026-09-25

**Acceptance** (verbatim from the review):
1. `pyproject.toml` sets `faulthandler_timeout` (for example 300) and pytest-timeout with a default of at most 600 s. A deliberately sleeping test in a temp dir, run as a subprocess by the new rules test, fails with a traceback naming it.
2. An AST rules test fails if any `subprocess.run|check_output|check_call|call` under `tests/`/`extensions/*/tests` lacks `timeout=`. It fails on a synthetic offender and passes on the tree.
3. `test_websocket_delivery.py` and `test_websocket_scoping.py` receive through a helper that raises after N seconds. Removing the fix for #326 makes `test_entry_created` *fail* within the deadline, not hang.
4. `test_handles_cycle_gracefully` fails within a bounded time when the cycle guard in `task_service` is removed (guard-removal proof in the report).
5. `.github/workflows/ci.yml` pytest jobs have `timeout-minutes`.

**Touches:**
- Existing: `pyproject.toml`, `tests/test_websocket_delivery.py`, `tests/test_websocket_scoping.py`, `tests/test_task_dag.py`, `tests/test_worktree_service.py` (21 calls), `tests/test_kb_commit.py` (12), `tests/test_version_service.py` (8), the other subprocess-calling files the rules test lists (among them `test_git_env_isolation.py`, `test_test_affected.py`, `test_verify_red_ci.py`, `test_logging_configuration.py`, `test_writes_never_block_on_embedding.py`), and `.github/workflows/ci.yml`.
- New: `tests/test_test_rules.py`. T3, T7, T5 and T8 add their rules to this file later.

**Sequence:**
- After #387 (#377) lands, because `pyproject.toml` and the root conftest trigger the full suite and #387 changes session setup.
- After #374 (verify-red single owner) lands, because both edit `.github/workflows/ci.yml` and `tests/test_verify_red_ci.py`.
- The first of the test themes: T3, T2a and T7 follow it in that order.
- Independent of the private security batch: the websocket files test delivery, not auth.

**Model:** Sonnet (mechanical, many files). **Size:** M. **Heavy:** yes (full suite; no Playwright). **Cold read:** no (test-only plus CI config).

**Out of scope:**
- Finding #372's spinner by hand. This theme makes the next occurrence name itself. Close #372 once a loaded run either completes or names its culprit.
- Changing any product code. The cycle guard, the websocket code and the git services stay as they are.
- Widening an existing timeout to make a test pass.
- The `time.sleep` and polling-budget rule. That is T7.
