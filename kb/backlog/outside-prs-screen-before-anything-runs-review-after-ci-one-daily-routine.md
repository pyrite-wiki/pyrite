---
id: outside-prs-screen-before-anything-runs-review-after-ci-one-daily-routine
title: 'Outside PRs: a security screen before any of their code runs, a public review only after CI — one daily routine'
type: backlog_item
kind: process
tags:
- process
- security
status: proposed
priority: high
effort: M
---

## Problem (retro 8, amended 2026-09-23 with the maintainer)

Two gaps in how outside PRs are handled. Response time is not one of them: a
first review within about two days is the expectation, and cloud routine runs
are limited to a few a day.

1. **Untrusted code runs before anyone has read it for danger.** CI itself is
   low-risk: `ci.yml` runs on `pull_request`, not `pull_request_target`, uses
   no secrets and has a `contents: read` token, and first-time contributors'
   runs wait for the maintainer's approval (`first_time_contributors`). The
   exposed surface is **our own review lane**. The conductor runs the
   contributor's suite on the maintainer's Mac, where the process can read the
   `gh` token, SSH keys and Claude credentials; on 2026-09-23 it ran #318's
   suite there before any screen. A cloud routine running the suite holds a
   GitHub token too. And the maintainer's approval of a first-timer's CI run
   has nothing to base it on except the maintainer reading the diff.
2. **A review published before the tests run is premature.** #317's review
   said "recommend merge" before the gate ran, and the gate then found the
   `--help` colour trap.

## Design: one daily cloud routine, two stages

**Stage 1: screen (PRs new since the last run; nothing is executed).** Read the
diff for anything that runs code:
- `conftest.py` and tests;
- `pyproject.toml` / `setup.*` build hooks;
- `.github/workflows/`;
- `package.json` scripts, `scripts/`, the `Makefile`;
- new or changed dependencies.

Look for network calls, access to the environment or credentials, filesystem
access outside the repo, subprocess calls, and obfuscation (encoded blobs,
`eval`/`exec`, dynamic imports). The verdict goes **privately to the
maintainer**, never on the PR: *safe to approve the CI run and to run locally*,
or *hold: <what, file:line>*. The channel is email, or the private desk repo
once it exists. The maintainer then approves the workflow run.

**Stage 2: review (PRs whose CI completed since the last run).** Run the full
review.md pass with the CI result in hand, including the cold read, and post it
publicly. No review is posted before CI has run on the PR's current head.

The routine never merges, dispatches, grooms, or runs code the stage-1 screen
has not cleared for that head.

## Acceptance

- The routine is scheduled once a day, and its prompt lives in the repo:
  `scripts/` (or in the maintainer's loop, outside this repo).
- **Stage 1:** for each new outside PR, a private verdict naming every
  executable file the diff touches, each marked clear or held. Planted-payload
  check before go-live: a test diff that adds an outbound request to
  `conftest.py` is held.
- **Stage 2:** posts only when the PR's latest CI run on the current head has
  completed. The template states the CI result.
- **review.md rule for the interactive loop:** no local suite run on an outside
  PR's head until a stage-1 screen of that head is clear. The screen can be
  the routine's, or the conductor's own read against the same checklist.
- CONTRIBUTING states the expectation: a first review within about two days,
  and the maintainer merges.

## Expected effect, and when to revert

- Outside PRs whose code ran anywhere (local, cloud or CI) before a screen:
  from 1 on 2026-09-23 (#318, local) to **0**.
- Reviews posted before CI completed: **0**.
- First review: **≤ 2 days** for every outside PR.
- Revert to the interactive-only lane if the routine misses a day more than
  once a week, or if the maintainer finds the stage-1 verdicts are not worth
  reading.
