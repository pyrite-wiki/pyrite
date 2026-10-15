---
id: every-heavy-runner-takes-the-suite-lock-playwright-the-skills-and-the-conductor-health-step
title: 'Every heavy runner takes the suite lock: Playwright, the skills and agent prompts, the conductor health step (#168)'
type: backlog_item
tags:
- process
- quality
- testing
- conductor
importance: 5
kind: tech_debt
status: in_progress
priority: high
effort: S
rank: 0
github_issue: 168
---

## Problem

`machine-wide-suite-lock-one-full-suite-at-a-time-xdist-workers-bounded-by-memory` gives the machine a lock and puts the pre-push hook behind it. The other three things that stacked up in #168 are not the hook: Playwright (uvicorn + vite + chromium, and `workers: undefined` locally means five browser workers on ten cores), the agent prompts that tell every worker, reviewer and outside-PR review agent to run `pytest tests/ extensions/ -n auto` themselves, and a conductor health step that recorded a load average of 14 and started a tick anyway.

## Acceptance

1. `scripts/e2e.sh [playwright args…]` runs `npx playwright test` from `web/` under the **same** lock as `scripts/suite.sh` (one heavy thing on the machine, whichever kind), with the same waiting message and timeout. `web/package.json`'s `test:e2e` script calls it, so `npm run test:e2e` cannot bypass the lock.
2. `web/playwright.config.ts` bounds local workers: `workers: process.env.CI ? 1 : Number(process.env.PW_WORKERS ?? 2)` (today `undefined` = half the cores).
3. Every instruction to run the suite says `scripts/suite.sh`, and every instruction to run Playwright says `scripts/e2e.sh`: `.claude/skills/pyrite-dev/SKILL.md` and `CONTRIBUTING.md` here (the maintainer's loop prompts and skills live outside this repo and follow the same rule). A test greps those files and fails on a bare `pytest tests/ extensions/` with `-n auto` or a bare `npx playwright test` outside a code block marked as CI.
4. The maintainer's loop health step (outside this repo) has a stated refusal: no worker, review suite or Playwright run is started when the 1-minute load average is above the core count or free+inactive memory is under 4 GB; the tick log records the refusal. (Prose in the skill plus `scripts/machine_ok.sh` printing the two numbers and exiting non-zero — so the rule is a command, not a judgement.)
5. Outside-PR review agents are told the conductor runs the suite once and hands them the result (the interim rule in #168, made permanent).

## Groom 2026-09-18 (serial)

**Regimes:** a Playwright run requested while a (fake) suite holds the lock — it waits, then runs; `PW_WORKERS` unset, `0`, non-numeric; `scripts/machine_ok.sh` on a machine where `vm_stat`/`sysctl` are absent (Linux CI: reads `/proc/meminfo`, or exits 0 with a notice — never crashes a tick); the grep test against a file that legitimately quotes the old command inside a `ci.yml` excerpt.

**Touches** — existing: `web/package.json`, `web/playwright.config.ts`, the eight skill/agent/contributor files in criterion 3, `tests/test_dev_process_config.py`. New: `scripts/e2e.sh`, `scripts/machine_ok.sh`, tests beside `tests/test_suite_lock.py`.

**Sequence:** after the lock item merges (imports `scripts/heavy_lock.py`; same test file). Before any Playwright theme (G, #153, H) if it can be — those are the next heavy runs — but it does not block them: under the WIP limit of one they are safe without it.

**Model:** sonnet (mechanical once the lock exists). **heavy:** yes, once — one single-spec Playwright run through the wrapper to prove criterion 1; it takes the machine's one slot. **Cold read:** no. **Size:** S, ~150 lines, mostly one-line edits across the prompt files.

**Out of scope:** raising the WIP limit; rewriting the conductor's tick structure; the CI `e2e` job (package H).

## Re-scoped 2026-10-14 (feature/machine-wide-suite-lock)

Done on the same branch as the lock. `scripts/suite.sh` does not exist (test-affected replaced it); instructions name `scripts/test-affected --run`, `scripts/heavy_lock.py -- <command>` for a full suite, and `scripts/e2e.sh`. The grep test is `tests/test_suite_lock.py::TestInstructions`; it also covers the pyrite-dev skill's testing/tdd/debugging pages and `CLAUDE.md`. `scripts/machine_ok.sh` uses the conductor skill's existing limits (load 0.8 x cores, 30% available memory) rather than this item's "load above cores / under 4 GB", which were older than the skill text. Criterion 5 (outside-PR review agents get the conductor's result) was already in the conductor skill's machine budget.
