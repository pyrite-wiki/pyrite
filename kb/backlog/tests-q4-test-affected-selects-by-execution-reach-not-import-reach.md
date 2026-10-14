---
id: tests-q4-test-affected-selects-by-execution-reach-not-import-reach
title: 'Tests Q4: test-affected selects by execution reach, not import reach'
type: backlog_item
tags:
- quality
- testing
importance: 5
kind: improvement
status: planned
priority: high
effort: L
rank: 0
---

> **Decision 2026-10-14 (Mark):** all eight test themes accepted as planned ("I think all the themes make sense"). Q1 and Q2 tentatively in the next release.

Source: `test-architecture-review-2026-10`, finding 2.

## Problem

`scripts/test-affected` selects every test that imports, through any chain, a module the branch changed. Measured on the last ten `dev` commits (`--list --files <the commit's files>`): 391-392 of 428 files for four commits (a41f1dde with 46 changed files, 7c2dd71b with 3, 05063a0b with 4, each touching the write or search path), the full suite for one (c15f910f added a fixture to `tests/conftest.py`), 202-227 for three, 13-22 for two. The script's docstring (`scripts/test-affected:29-42`) names the limit: `pyrite/services/kb_service.py` reaches 167 of 281 files through `__init__` imports. Pre-push is documented as "feedback, not the gate" (`.pre-commit-config.yaml`), but for half of commits it costs the full 9-11 minutes the maintainer reports, and the stamp only saves the second run of the same tree.

Import reach cannot get much tighter: the packages are wired so that most entry points import most services. Execution reach can: a coverage run with `--cov-context=test` records, per test, which source lines ran; a change to a line selects the tests that executed it. CI already produces coverage on every PR's 3.12 leg (`ci.yml:389-397`).

## Acceptance

- The gating 3.12 run on `dev` (or the queue) writes a test-impact map: for each `pyrite/**` and `extensions/**/src/**` file, the set of test node ids that executed any line of it (`coverage` dynamic contexts, `--cov-context=test`, exported to a compact JSON or SQLite artifact under 10 MB, kept 14 days and attached to the latest `dev` SHA).
- `scripts/test-affected` fetches the map for its merge base when `gh` is available and the map exists (`--map <path>` and `PYRITE_TEST_MAP` for offline use), and selects: the `core` set, plus every test that executed a changed line or changed function, plus every test in a changed test file, plus import-reach for a file the map has never seen (new modules). Without a map it behaves exactly as today and says so.
- Measured target, written into this item on merge: for the same ten commits, the median selection is under 40% of files and no one-module change (`d2a0417b`, `7c2dd71b`, `05063a0b`) selects over 25%. The commands in the design doc's "How this was measured" produce the number.
- The stamp logic (`scripts/test-affected`, `tests/test_test_affected_stamps.py`) is unchanged: a map-based selection that passes stamps the tree with the tests it ran, as now.
- `tests/test_test_affected.py` gains cases on a fixture map: a line change selects its executors; a changed test file selects itself; a module absent from the map falls back to import reach; a stale map (older than the merge base) is refused with a message naming the SHA.
- Fails today: the new cases.

## Regimes

- No network, no `gh`, no map: today's behaviour, one line of output saying "import reach (no execution map for <sha>)".
- The map was built on Linux CI; a test that skips on macOS (Postgres legs) is absent from the map and is selected by import reach as a fallback, not dropped.
- A change to `tests/conftest.py`, `pyproject.toml`, hooks or CI still runs the full suite (`_FULL_SUITE_NAMES`, `scripts/test-affected:125-127`).
- A changed line inside a function with no executor in the map (dead or new code) selects the file's import reach and reports it, so a new branch of code is not silently untested before CI.

## Options

1. Coverage contexts from the CI run (recommended): no local cost, the map is as current as `dev`, one artifact per SHA.
2. A local map built by the first full run on a machine (`--build-map`): no CI change, but every worktree pays a full run first and the map ages with the branch.
3. Tighten import reach by splitting `pyrite/services/__init__.py` and friends: helps every tool, but the measured reach of `kb_service` itself is through real imports, so the ceiling is low.

## Footprint

Existing: `scripts/test-affected` (+200), `.github/workflows/ci.yml` (+30: export and upload the map on `dev`), `tests/test_test_affected.py` (+150), `CONTRIBUTING.md` (+15). New: `scripts/test_impact_map.py` (~120 lines: build from `.coverage`, export). Model: opus (the selection rules and the fallback matrix are design-shaped). Sequence: after Q2 (the durations baseline shows the saving). Must not break: `PYRITE_PUSH_FULL`, `--force`, `--experimental`, the stamp key; `tests/test_dev_process_config.py` pins the hook's `files:` pattern.

Checked: selection counts for ten commits; `--cov-context` is a `coverage` 5+ feature and `pytest-cov` passes it through (`--cov-context=test`). Assumed: the per-test context artifact for ~10,500 tests over ~60k source lines compresses under 10 MB (coverage's SQLite is larger; export only file-to-test-ids). This groom is wrong if the `.coverage` file from `-n auto` loses contexts across xdist workers (then the map is built from the serial coverage job on `workflow_dispatch`, and the theme is a spike first).
