---
id: machine-wide-suite-lock-one-full-suite-at-a-time-xdist-workers-bounded-by-memory
title: 'Machine-wide suite lock: one full suite at a time, xdist workers bounded by memory (#168)'
type: backlog_item
tags:
- process
- quality
- testing
- conductor
importance: 5
kind: tech_debt
status: proposed
priority: high
effort: M
rank: 0
github_issue: 168
---

## Current hook contract

The pre-push hook now uses `scripts/test-affected` (core plus affected tests, #356), not an unconditional full suite. [CONTRIBUTING](../../CONTRIBUTING.md#branches-hooks-and-pull-requests) owns the current flow. The full-suite history/proposal below does not describe the current hook; a suite-lock integration must retain the selector and its full-suite fallback.

## Problem

On 2026-09-18 ~11:10Z the conductor loop ran the maintainer's machine (16 GB, 10 cores) out of memory (#168): up to eight concurrent `pytest tests/ extensions/ -n auto` runs (10 xdist workers each, ~80 Python processes, torch where embeddings load) plus a Playwright run. Nothing serialises full suites across worktrees, and `-n auto` sizes itself to the core count, not to memory or to what else is running. The suite is started from four places that do not know about each other: the pre-push hook inside every worker, workers' own verification runs, reviewers, and the conductor.

The interim rule is a WIP limit of one Pyrite task at a time. **This item is what lets that limit ever be raised**: until concurrent suites queue instead of stacking, every raise of the limit is another crash.

## Fix

One wrapper, `scripts/suite.sh`, is the only way the full suite is started on a developer machine. It takes a machine-wide lock (outside any worktree, so every worktree contends for the same one), waits its turn, and runs pytest with an xdist worker count derived from memory, not cores. The pre-push hook calls it. CI does not: CI keeps `-n auto` on its own runner.

## Acceptance

1. `scripts/suite.sh [pytest args…]` runs `python -m pytest tests/ extensions/ -q --tb=short -n <N>` (default targets when none are given) from the calling worktree's `.venv`, holding an exclusive machine-wide lock for the whole run. A second invocation from any worktree **waits** and starts when the first ends; it does not fail and does not run concurrently.
2. The lock is one the kernel releases when the holder dies (`fcntl.flock` on a file held open by the wrapper's process — a small Python helper is fine; macOS has no `flock(1)`). **Never a bare lockfile or `mkdir` lock**: the incident that motivates this item killed every holder at once, and a stale lock that blocks every later push is worse than no lock.
3. The lock path is outside the repo (default `~/.cache/pyrite/heavy.lock`, overridable by `PYRITE_SUITE_LOCK`); the holder writes its pid, worktree path, command and start time into a sidecar the waiter prints once on entry and every 60 s ("waiting for the suite lock held by pid … in … since …"). `PYRITE_SUITE_LOCK_TIMEOUT` (seconds, default 3600) ends the wait with a non-zero exit and that message; it never proceeds without the lock.
4. `N` is bounded by memory: `min(cpu_count, max(2, total_memory_GB // 4))` — 4 on the 16 GB machine, matching the interim rule — overridable by `PYRITE_SUITE_WORKERS`. The wrapper prints the `N` it chose and why. The worker measures peak RSS per xdist worker once during its own verification run and records the number in the PR body so the divisor is evidence, not a guess.
5. The pre-push hook's entry in `.pre-commit-config.yaml` becomes `scripts/suite.sh`; `tests/test_dev_process_config.py` pins that the pre-push hook goes through the wrapper, that the hook no longer names `-n auto`, and that **CI still does** (the existing CI pin stays).
6. The wrapper's exit code is pytest's; a lock wait never turns a red suite green or a green one red.
7. `CLAUDE.md` "Testing" and "Pre-commit Hooks" name the wrapper as the way to run the full suite locally, and say why (#168).

## Groom 2026-09-18 (serial)

**Regimes** (each needs a test that enters it; all of them with a fake command — `python -c "import time; time.sleep(…)"` — and a temp lock path, never the real suite):
- two concurrent callers: the second starts only after the first exits (assert on timestamps written by the fake command, with a start barrier and one group deadline — the `tests/test_task_claim_concurrency.py` pattern);
- the holder is `SIGKILL`ed mid-run: the waiter acquires within a second, no manual cleanup;
- the lock directory does not exist (fresh machine) and is created;
- the timeout expires: non-zero exit, holder named, the command **not** run;
- `PYRITE_SUITE_WORKERS` set to `0`, a non-integer, and a number above the core count;
- **the wrapper's own tests run inside a suite that holds the real lock** — they must use `PYRITE_SUITE_LOCK=<tmp>` or the suite deadlocks on itself under the pre-push hook. A test asserts no test in the file touches the default path;
- a machine where total memory cannot be read (the helper falls back to 2 workers and says so).

**Touches** — existing: `.pre-commit-config.yaml`, `tests/test_dev_process_config.py`, `CLAUDE.md`. New: `scripts/suite.sh`, `scripts/heavy_lock.py` (the flock helper, reusable by the Playwright theme), `tests/test_suite_lock.py`.

**Sequence:** after #163 (CI parity) merges — both edit `tests/test_dev_process_config.py` and `.pre-commit-config.yaml`'s neighbourhood. First in the serial queue after that: nothing else raises the WIP limit.

**Model:** opus (a concurrency primitive whose failure mode is the machine; the stale-lock and self-deadlock regimes are where a plausible implementation is wrong). **heavy:** no for its tests; its one verification run of the real suite goes through the new wrapper and is the evidence for criterion 4. **Cold read:** yes — it changes the gate every push goes through. **Size:** M, ~250 lines (wrapper + helper ~90, tests ~120, config/docs ~40).

**Out of scope:** Playwright under the lock, the skills/agent prompts, the conductor's load/memory refusal (the follow-on item `every-heavy-runner-takes-the-suite-lock-playwright-the-skills-and-the-conductor-health-step`); changing CI's `-n auto`; a job queue or daemon — a lock is enough; raising the WIP limit (the maintainer's call, after both items have landed and a week has run on them).
