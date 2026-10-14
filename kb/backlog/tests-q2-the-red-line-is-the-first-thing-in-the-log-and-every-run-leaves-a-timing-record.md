---
id: tests-q2-the-red-line-is-the-first-thing-in-the-log-and-every-run-leaves-a-timing-record
title: 'Tests Q2: the red line is the first thing in the log, and every gating run leaves a timing record'
type: backlog_item
tags:
- quality
- testing
importance: 4
kind: tech_debt
status: planned
priority: high
effort: S
rank: 0
milestone: "0.25.9"
---

> **Decision 2026-10-14 (Mark):** all eight test themes accepted as planned ("I think all the themes make sense"). Q1 and Q2 tentatively in the next release (this item).

Source: `test-architecture-review-2026-10`, findings 3 and 4.

## Problem

- `mypy` runs `continue-on-error` inside the `test` job (`.github/workflows/ci.yml:357-359`) and prints 702-708 `error:` lines before pytest starts (jobs 115564565902, 115475155888, 115205058559). A reader looking for the red line scrolls past them every time, and the annotations it emits sit beside the test failure's.
- No gating job passes `--durations` or writes junit for the core suite (`ci.yml:394`); only the `experimental` job writes `experimental.xml`. "Which tests are slow" and "did this PR make a test slower" have no answer without a local run. The queue's legs on one commit finished 13:15, 19:24 and 20:38 after start (run 38505895947), and nothing says where the six minutes went.
- The warnings summary (12,274 warnings on the 3.12 leg) comes between the failure and the summary line.

## Acceptance

- mypy runs in its own job, advisory as now, so the `test` job's log opens with install, lint and pytest only. `gate` does not need it.
- Every pytest invocation in `ci.yml` that gates (`test`, the queue legs, `smoke`) runs with `--durations=25 --durations-min=1.0` and `-o junit_family=xunit1 --junitxml=<job>.xml`, and uploads the junit as an artifact kept 30 days. The step summary shows the 25 slowest tests.
- Warnings are summarised after the short test summary, not before it (`-p no:warnings` is not the answer; `--disable-warnings` on the gating run plus a separate advisory count is acceptable if the `-W error::DeprecationWarning:pyrite` escalation still fails the run; the worker shows the two orders side by side in the PR).
- `tests/test_dev_process_config.py` pins the layout: mypy is not a step of `test`; every gating pytest line carries `--durations` and `--junitxml`.
- A measured number written into this item on merge: the slowest 25 tests on the first queue run after landing, with their times, as the baseline Q4 and T2a are scored against.

## Regimes

- A job that times out before pytest writes junit: `if-no-files-found: ignore` on the upload, as the experimental job does.
- The `verify-red` job runs pytest twice on a throwaway tree; it keeps its own evidence file and is out of scope here (check: `scripts/verify_red_ci.py` writes `test-evidence.json` and does not read durations).

## Footprint

Existing: `.github/workflows/ci.yml` (+40/-10), `tests/test_dev_process_config.py` (+40), `CONTRIBUTING.md` "On the pull request" row (+1). Model: sonnet. Sequence: independent; land before Q4 so its target has a baseline. Must not break: the `queue_passed` skip logic (`ci.yml:127-141`) and `gate`'s `needs` list; a docs-only PR must still go green in about a minute.

Checked: the mypy line counts and step order from the three job logs; `--durations` absent by grep on `ci.yml`. This groom is wrong if GitHub's log viewer already folds the mypy step for readers (it folds by step, but `gh run view --log`, which agents read, does not).
