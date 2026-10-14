---
id: tests-q6-a-flake-is-recorded-not-re-run
title: 'Tests Q6: a flake is recorded, not re-run - a same-SHA red-then-green files an issue, and a quarantine marker expires'
type: backlog_item
tags:
- quality
- testing
importance: 4
kind: improvement
status: planned
priority: medium
effort: M
rank: 0
---

> **Decision 2026-10-14 (Mark):** all eight test themes accepted as planned ("I think all the themes make sense"). Q1 and Q2 tentatively in the next release.

Source: `test-architecture-review-2026-10`, "The gates today". Model: the experimental ratchet (`scripts/experimental_ratchet.py`, `ci.yml:743-886`), which already files and updates issues from a `dev` run.

## Problem

Flakiness has no home. PR #826's first run failed `tests/test_mcp_sse_session.py::TestSessionLifetime::test_session_expiry_ends_the_stream` with `anyio.EndOfStream` through a 40-line `ExceptionGroup` traceback (run 38370201767); the re-run passed; nothing recorded the test's name anywhere. CLAUDE.md tells a worker to push `--no-verify` past an unrelated local failure and "file a GitHub issue for the test only if it fails that way again", which depends on the same person seeing it twice. The two classes already fixed at the root (the shared rate limiter, `tests/conftest.py:41-65`; index-worker threads at teardown, `make_client`) were found only after days of "fails in a batch, passes alone".

A same-SHA red-then-green is the one signal that is cheap to detect and certain to be a flake: the code did not change between the two runs.

## Acceptance

- A job on `dev` pushes and on queue runs (or a scheduled job reading the last day of runs) finds, for each SHA, a gating pytest job that failed and a later one on the same SHA and job name that passed; for each test that failed in the first and not in the second (from the junit Q2 adds), it opens or comments on a `flaky-test` issue named after the test node id, with the run URLs and the failure's first line. One issue per test, updated, never duplicated (the `experimental_ratchet.py file-issues` pattern).
- A `@pytest.mark.flaky_quarantine(issue=N, until="YYYY-MM-DD")` marker: a quarantined test still runs, its failure is reported in the step summary and does not fail the job; a marker past its date fails the run with the issue number; a bare marker (no issue, no date) fails collection. `tests/test_dev_process_config.py` pins that no marker is older than 30 days.
- The flake detector ignores the `experimental` job (it has the ratchet) and `e2e` (its own issue flow is Playwright's, not in scope).
- `tests/test_flake_detector.py` on fixture junit pairs: red-then-green on one SHA yields one issue body naming the test; red-then-red yields nothing (that is a real failure); green-then-red yields nothing (a re-run after a flake is the previous case seen from the other side; the worker states which run order the job sees and why only one direction counts).
- Fails today: the detector tests.

## Regimes

- A re-run that passes because `dev` moved under the queue: the SHA differs, so it is not a pair; nothing is filed. Correct.
- A test that is red on 3.11 and green on 3.12 in the same run: different job names, not a pair; that is a real interpreter difference and the matrix catches it.
- A `--lf` local re-run: out of scope; the local signal is the worker's PR note, as CLAUDE.md says.
- Issue noise: a flake seen once gets one issue; the issue body counts occurrences; the conductor's daily PR review reads the label.

## Footprint

New: `scripts/flake_detector.py` (~200 lines), `tests/test_flake_detector.py` (~150), `.github/workflows/ci.yml` (+40, one job with `issues: write` on `dev` only, like `experimental-issues`), `tests/conftest.py` (+25, the marker), `kb/standards/testing-standards.md` (+8), `CLAUDE.md` (-2/+3: the "file an issue only if it fails that way again" line points at the label). Model: sonnet. Sequence: after Q2 (needs the junit artifact). Must not break: `gate`'s `needs`; the least-privilege `permissions:` pattern (`issues: write` in the one job that files).

Checked: run 38370201767 and its re-run; the ratchet's issue-filing code path. Assumed: `gh api` can list runs by `head_sha` and job name within the workflow token's rate budget (the `queue` step at `ci.yml:132` already does one such call). This groom is wrong if GitHub's re-run creates a new run id rather than a new attempt of the same run (then the pair is run-attempt 1 and 2 of one run, which is simpler: `gh run view --attempt`).
