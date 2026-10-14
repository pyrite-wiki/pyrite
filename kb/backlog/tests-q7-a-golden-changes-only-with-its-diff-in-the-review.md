---
id: tests-q7-a-golden-changes-only-with-its-diff-in-the-review
title: 'Tests Q7: a golden changes only with its normalised diff in the review (fixes #858)'
type: backlog_item
tags:
- quality
- testing
importance: 3
kind: bug
status: planned
priority: medium
effort: S
rank: 0
---

> **Decision 2026-10-14 (Mark):** all eight test themes accepted as planned ("I think all the themes make sense"). Q1 and Q2 tentatively in the next release.

Source: `test-architecture-review-2026-10`, the process section; GitHub #858.

## Problem

`tests/characterization/goldens/` holds 17 JSON files, the recorded REST and MCP responses per principal. They are regenerated with `PYRITE_CHARACTERIZATION_REGENERATE=1`, and review of a change is "read the JSON diff in the PR". Two holes:

- #858: under `-n4`, a regenerate rewrote two of nine files and reported 19 passed; the next run failed on the rest. `tests/characterization/golden_io.py` saves per scope under a lock, and the per-principal parametrisation under xdist splits one file's keys across workers.
- Nothing distinguishes "the response changed because the behaviour changed" from "someone regenerated to go green". A PR that touches a golden and nothing under `pyrite/` that the golden's surface maps to is the second kind, and no check says so.

## Acceptance

- #858 fixed: a regenerate at `-n 4` records every key for every principal, or refuses to run under xdist with a message naming the serial command. A test reproduces the loss on a fixture golden with two principals split across two workers and fails today (`tests/characterization/test_golden_io.py`, new).
- A golden file carries, per key, the SHA of the source files its surface maps to (from `surfaces.py`'s route and tool to module mapping) at the time it was recorded, or one header per file. A test fails when a golden's key changed and the recorded source SHA equals the current one (the behaviour did not change; the golden did), and when a mapped source changed and the key did not (advisory: a response may legitimately be unchanged; report, do not fail).
- The PR's step summary (the `test` job on `pull_request`) prints the normalised diff of every changed golden (`tests/characterization/normalize.py` is the normaliser), so the reviewer reads the behaviour change in the summary, not in a 400-line JSON diff.
- `docs/testing.md` or the module docstrings of `test_mcp_matrix.py`/`test_rest_matrix.py` document the one regenerate command that works.

## Regimes

- A new principal or route with no golden yet: the regenerate records it; the check treats a key with no prior SHA as new, not changed.
- A golden changed by a `dev` push with no PR (none exist: `dev` takes PRs only); the summary step is `pull_request` only.
- Normalisation itself changes (`normalize.py`): every golden "changes" with no source change; the worker adds `normalize.py` to every surface's mapped sources.

## Footprint

Existing: `tests/characterization/golden_io.py` (+40/-10), `tests/characterization/surfaces.py` (+30, the surface to module map; shared with Q5 later), `tests/characterization/test_mcp_matrix.py` and `test_rest_matrix.py` (docstrings), `.github/workflows/ci.yml` (+15, the summary step), `tests/characterization/goldens/*.json` (headers). New: `tests/characterization/test_golden_io.py` (~100). Model: sonnet. Sequence: independent; before Q5 touches `surfaces.py`, or after it with the map living in the registry. Must not break: the goldens' current keys (a header line is additive; `golden_io.load` ignores unknown top-level keys, which the worker checks first).

Checked: #858's text; the 17 goldens by `find`. Assumed: `golden_io.save` is the one write path (the issue says so). This groom is wrong if the loss in #858 is a lock bug rather than a scope-split bug (then the fix is in the lock and the xdist test still reproduces it).
