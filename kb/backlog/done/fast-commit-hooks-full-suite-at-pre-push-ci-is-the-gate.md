---
id: fast-commit-hooks-full-suite-at-pre-push-ci-is-the-gate
title: Fast commit hooks, full suite at pre-push, CI is the gate
type: backlog_item
tags:
- ci
- testing
- contributor-experience
- dev-process
links:
- target: tests-must-not-inherit-git-env-autouse-fixture
  relation: related_to
  kb: pyrite
importance: 5
kind: improvement
status: done
priority: high
effort: S
rank: 0
---

## Current hook contract

The pre-push hook now uses `scripts/test-affected` (core plus affected tests, #356), not an unconditional full suite. [CONTRIBUTING](../../../CONTRIBUTING.md#branches-hooks-and-pull-requests) owns the current flow. The full-suite history/proposal below does not describe the current hook; a suite-lock integration must retain the selector and its full-suite fallback.

## Problem

The pre-commit config ran the full pytest suite (`tests/ -x`, several minutes)
at the **commit** stage. pre-commit stashes every unstaged edit in the working
tree to `~/.cache/pre-commit/patch*` while commit hooks run and restores them
afterwards. With several Claude sessions sharing one tree (CLAUDE.md,
"Multi-Session Git Hazard") that means:

- Every commit makes every other session's unstaged edits vanish for minutes.
- One session's untracked RED test (TDD step 1) fails `-x` and blocks every
  other session's commit.
- A crash mid-hook strands the edits in the patch file. Observed 2026-09-17:
  the machine went down during a commit and four files of another item's
  in-progress work (`cross_kb_search.py`, `investigation_setup.py`,
  `zettelkasten/plugin.py`, `mcp_server.py`) existed only in
  `~/.cache/pre-commit/patch1789678026-35146` until recovered by hand with
  `git apply`.
- The hook discarded its own output (`2>/dev/null`, `--tb=no`), so a blocked
  commit said only "Tests failed!".
- Hooks did `source .venv/bin/activate`, which is not portable.
- Plain `pre-commit install` installed only the pre-commit hook type, so the
  `commit-msg` rule (`fix:` commits must touch tests/) silently never ran on
  fresh clones.

On the CI side, `test-optional-deps` had become an exact duplicate of the
`test (3.12)` matrix leg once that job installed `.[all]` plus extensions;
superseded runs were never cancelled; and the import-cycle and KB schema checks
ran only in local hooks, which outside PRs never execute and `--no-verify` skips.

## Solution

| Stage | What runs |
|-------|-----------|
| commit | ruff, ruff-format, file hygiene, import cycles, KB schema validate. Seconds. No pytest. |
| commit-msg | `fix:` commits must touch tests/ |
| pre-push | full suite, only when the pushed range touches code/config (`files:` filter) |
| CI | the authority: everything above plus the full matrix |

- `.pre-commit-config.yaml`: `default_install_hook_types`, `default_stages`,
  `pytest-full` moved to `pre-push` with a `files:` filter and visible output;
  local hooks use `.venv/bin/python` with a PATH fallback, no `activate`.
- `.github/workflows/ci.yml`: `concurrency` with `cancel-in-progress`;
  import-cycle and `pyrite schema validate kb/` steps; `test-optional-deps`
  removed.
- `kb/backlog/README.md` gained frontmatter: it was the single error that made
  whole-KB `schema validate kb/` exit 1.
- `tests/test_dev_process_config.py` pins the layout (11 tests).
- CLAUDE.md "Pre-commit Hooks" rewritten to match, including how to recover a
  stranded pre-commit patch.

## Follow-ups (not done here)

- `pytest -n auto`, measured 2026-09-17 on `tests/ extensions/`: **2m19s, 4063
  passed, 1 failed**, against 7m41s serial for `tests/` alone (3160 passed). The
  failure is `test_task_claim_concurrency.py::test_n_processes_race_claim_exactly_one_wins`:
  it spawns N processes with fixed 30s/5s timeouts, passes standalone (13s), and
  starves when xdist saturates the cores. Pre-push stays serial until that test
  is marked to run outside the xdist pool (or its timeouts scale); then switch
  the hook to `-n auto` for a ~3x faster gate.
- 516 schema *warnings* across kb/ (mostly `priority` str-vs-int) are untouched.
