---
id: gate-evaluation-2026-11
type: design
title: "Gate evaluation, 2026-11: friction against payback"
status: draft
author: 'pyrite-architect (Claude Fable 5.2), for markr'
date: "2026-11-17"
reviewers: []
tags: [gates, ci, testing, process, quality]
links:
- target: test-architecture-review-2026-10
  relation: related
  kb: pyrite
- target: code-audit-2026-11-16
  relation: related
  kb: pyrite
---

# Gate evaluation, 2026-11: friction against payback

The maintainer asked on 2026-11-17: "We should also evaluate programmatic gates
in terms of friction vs payback." This is the first pass. A gate here is
anything that can turn a run red or block a commit, push, merge or release
without a person deciding. It builds on
[[test-architecture-review-2026-10]] and does not repeat it. The rule it
judges by is ADR-0032 section 3a: a step may cost more than the one before it
only if it buys information the earlier step could not.

Window: 2026-11-01T00:00Z to 2026-11-17T16:10Z, on `pyrite-wiki/pyrite`.
Nothing was changed and no suite was run. Every number names its source in
"How this was measured".

## Summary

In 17 days CI ran 460 times (283 pull-request runs, 109 merge groups, 68
pushes) and used about 21,900 runner-minutes (365 hours). The three `test`
shards are 80% of that. 84 commits landed on `dev`, each one squashed pull
request, so a landed change costs about 260 runner-minutes.

**Where the gates paid.** The suite on the pull request caught regressions
in existing tests on five PRs and two breaking dependency majors. The wider
matrix on an infrastructure PR caught the 0.26.3 release defect on 3.11 and
3.12. The source-reading tests caught a broken rule on seven PRs; this is
the family with the best catch count for its cost, and review would have
missed most of them. The frontend job caught two PRs. One pin
(`test_readme_install_tag`) caught a release that would have told users to
install the old tag.

**Where the gates cost and bought nothing.**

- **Tests that fail on a slow runner.** 19 red run-attempts from five
  tests: three assert on a clock, one on a 60-second subprocess timeout,
  and one Postgres test whose cause is unknown. No defect was found behind
  any. Twelve were on the required check, all inside
  20 hours on 11-16 and 11-17, when CI load went from about 7 PR runs a day
  to 60 and then 121. Each cost a re-run or a re-queue.
- **The `fix:` commit check.** 9 red runs on 7 PRs. In 7 of the 9 the branch
  carried its test, in another commit. The queue squashes every PR to one
  commit, so the check judges commits that never reach `dev`.
- **The merge queue and the push to `dev`.** Together 39% of all runner
  minutes. In 106 merge groups and 65 pushes they caught no defect that
  belonged to a pull request. They did catch one upstream break (Typer 0.29,
  20 red groups in 52 minutes), which the lock file has since moved to the
  weekly `unlocked` run.

**Who pays.** An outside author's first decided CI run was red 35% of the
time (22 of 62 branches); the loop's was red 9% (4 of 45). Every red from
`ruff format` (8 runs), from a guard on test sources (16 runs) and from a
golden (6 runs) fell on an outside PR, and 8 of 9 from the `fix:` check.
Twelve of the 23 outside PRs that went red never failed for a product
reason: only process gates or flakes. The loop's own reds have two main
causes: source-reading tests, which the pre-push selection can miss (7 of
20), and flakes (7 of 20).

**Two gaps**, where a gate is green without having judged.

- `smoke` was red on the commit tagged v0.26.2. ADR-0032 says a red smoke
  blocks the release; `scripts/release.py` requires only `gate` (#1454,
  open).
- A docs-only pull request skips `test`, though tests run the docs.
  #1627 changes only `docs/agent-write-path.md`, which
  `tests/test_doc_agent_contracts.py` executes; its `gate` is green with
  `test` skipped (run 44122817671). The merge group is the first run that
  judges the page.

**The three changes** (drafted below): no test's verdict is a wall clock;
the pre-push selection runs the source-reading tests; the `fix:` check
judges the pull request. Most gates should be left alone, and the list says
which.

## The table

Cost is the median for one run unless it says otherwise. "Caught" means a
defect or a broken rule the gate found in the window. "Friction" is red that
was not a defect, round trips, and conflicts.

### Commit and commit-msg (local hooks)

| Gate | Cost | Caught in window | Friction in window | Verdict |
|---|---|---|---|---|
| `ruff` and `ruff-format` | 0.13 s and 0.06 s over the whole tree | not measurable (no log) | not measurable | Keep. |
| File hygiene (six hooks) | seconds, not timed | not measurable | not measurable | Keep. |
| `import-cycle-check` (also CI shard 1) | 0.26 s | 0 CI reds | 0 | Keep. |
| `pyrite-schema-validate --changed` (also the CI `kb` job and shard 1) | CI `kb` job 0.2 min | 0 reds in 243 `kb` jobs | 0 | Keep. |
| `fix-commit-has-tests` (commit-msg, and CI on pull requests) | instant | 2 PRs with no test at all (#1390; #1446, a type-only fix) | 9 red runs on 7 PRs, 8 of them outside. 7 runs on 5 PRs had the test in another commit (#1404, #1416 three times, #1543, #1581, #1603). #1543 could not be repaired (no force-push on upstream branches) and was replaced by #1551. #1618 improved the message today; the rule is unchanged. | **Change what it checks** in CI: judge the PR's range, not each commit (ticket 3). |

### Push (local hook)

| Gate | Cost | Caught in window | Friction in window | Verdict |
|---|---|---|---|---|
| `pytest-full` (`scripts/test-affected --run`) | Not measured: the pass stamp holds no duration. A one-file change selects 86 to 610 of 698 test files (probe on four files). | 1 recorded (#1570: two existing tests broke; stopped before CI) | 1 recorded: a text-only docs PR deferred for three ticks because the machine was memory-BUSY. The security-release recipe carries a standing `--no-verify`. Local reds are not logged anywhere. | Keep. **Change the selection** so it runs the source-reading tests (ticket 2). Write the run's seconds into the stamp so the next evaluation can cost it. |

### Pull-request CI

| Gate | Cost | Caught in window | Friction in window | Verdict |
|---|---|---|---|---|
| `changes` classifier, `lock`, `kb`, `gate` | 0.1 to 0.2 min each | 0 reds in 296 `lock` jobs | `gate` shows red on 34 superseded (cancelled) runs; no final head was affected | Keep as is. |
| The classifier's docs-only skip (`backend` filter has no `docs/**`, root `*.md` or `changelog.d/**`) | saves about 67 runner-min on a docs PR | nothing: `test` does not run | No red. A gap: 4 merged PRs in the window had no backend path and changed a doc a test names (#1444, #1453, #1493, #1596; matched by file name, rough). None failed in the queue. #1627 is the live case. | **Change what it checks**: a docs-only PR runs the tests that name the changed files (see "Next in line"). Until then the merge queue is the gate for docs. |
| `ruff check` and `ruff format --check` (step in `test` shard 1) | seconds, after a full install | 0 lint findings | 8 red runs on 7 PRs, all outside, all "Would reformat". The log does not say how to repair it. A red here skips shard 1's tests, so the author meets the next failure a round later (#1416: five red runs in a row; #1404: four). | Keep blocking. **Move** to its own job and print the repair command (see "Next in line"). |
| `test` on 3.12, three shards | 13.6 min per shard; 40 runner-min; 10,127 runner-min in the window | Regressions in existing tests on 5 PRs (#1408, #1438, #1449, #1492, #1612); 2 breaking dependency majors (#1413, #1452); 4 PRs whose own new test was red (read from the test names) | 8 flake red attempts on 7 PRs from three wall-clock tests (below) | Keep. Fix the wall-clock tests (ticket 1). |
| Wider matrix (3.11, 3.13) when a PR touches test infrastructure | about 70 extra runner-min on those PRs | #1492: the 0.26.3 batch failed on 3.11 and 3.12 only | 0 | Keep. It paid for itself once. |
| Guards on test sources: wiring baseline (160 lines), mock allowlist (52), `cli_help.plain`, security vocabulary, deadlines, fragments by name: the ten guards in CONTRIBUTING's table. | about 60 s of test time | 12 PRs added a test that broke a test rule. No product defect. | 16 red runs, all on outside PRs, none on the loop (the loop meets them at pre-push). The messages are good: file, line and what to use. The cost is when the author learns: 15 to 27 minutes into CI, at about line 17,000 of a shard log. `tests/production_wiring_baseline.txt` has not come down since it was written on 2026-10-20. | Keep the rules. **Move earlier** for outside authors (the fast job in "Next in line"). In grooms, point to a test that already uses the world. |
| Source-reading tests and ratchets: 56 files use `ast`, `rglob` or `getsource`; 16 single-integer pins | at most 20% of recorded suite time (whole files; the scanning functions are a small part) | 7 PRs in 10 red runs (#1351, #1435, #1438, #1525, #1543, #1553, #1576). Review would have missed most. | 7 of the 10 runs were loop PRs: the pre-push selection can miss these tests for the file that changed (reproduced for #1553; reported for #1543). One brittle text pin ("update the mutation", #1553). Pin lines: changed 7 times in 84 `dev` commits; made 2 conflicts (#1595 with #1599; #1569 now). | Keep; these earn their place. **Move earlier** (ticket 2). Pins: #1608, already in build as #1614. |
| Goldens (19 files) and the MCP schema snapshot | under 60 s of test time | 3 PRs changed a public response (#1381, #1480, #1525) and had to show it | 6 red runs, all outside. Goldens changed in 13 of 84 `dev` commits; no open PR conflicts on one today. | Keep as is. The golden diff is how a reviewer sees a contract change. |
| `collect` and the shard-union check in `shard-report` | about 2 runner-min per interpreter; about 940 in the window (4%) | 0 failures in 357 `shard-report` jobs | 0 | **Keep and cheapen**: run the union check on the push to `dev` and on infrastructure PRs, where the split can change. |
| `mypy (advisory)` | 2.4 min; 812 runner-min, 260 of them in the queue | Red on 15 of 216 PR jobs. Named in comments on 8 PRs. | Red on 3 of 3 pushes to `main` and 3 of 106 merge groups, where nobody reads it. | Keep on pull requests. **Remove** from the merge queue and from `main`. |
| `verify-red` (advisory) | 1.1 min; 395 runner-min | Never red. Its evidence on 79 PR branches: 47 (59%) had at least one new test that passes without the change; 5 `fix:` PRs had no red test at all. The conductor reads it at each review (49 comments on 28 PRs). | 0 | Keep advisory. Do not gate at a 59% hit rate; that waits for #1104. |
| Diff coverage (advisory) | about 3 runner-min (tracer on three shards) | Below 80% on 1 of 35 PRs that had a figure; median 97% | 0 | Keep as is. Low information, low cost. |
| Changelog fragment warning (advisory) | seconds | Not measurable (an annotation) | 0 from the gate. Reviews ask for a fragment often; that is a person's ask. | Keep as is. |
| `experimental` and its ratchet (not required) | 6.2 min; 1,735 runner-min | 0 that `test` did not also catch. The known-failures list did not change. | 6 red attempts from one Postgres test (#1401, open since 11-02) on 4 PRs and `dev`. Outside authors see a red check that is not theirs. | Keep. Make red mean news again: list or fix #1401's test (ticket 1). |
| `frontend` | 1.1 min | #1495 (install), #1600 twice (build) | 0 | Keep as is. |
| CodeQL (default setup) | 203 runs | not evaluated; not required | not evaluated | No verdict. |

### Merge queue

| Gate | Cost | Caught in window | Friction in window | Verdict |
|---|---|---|---|---|
| Queue rules (squash, all green, at least 2 entries or 3 min, 40 min timeout) and its run: `test` on 3.13, `frontend`, `kb`, `mypy` | 14.8 min wall (p90 19.4); 45 runner-min; 4,247 in the window | 0 defects that belonged to a PR, in 106 completed groups. 20 red groups on 2026-11-10 from Typer 0.29 (11 PRs held 52 minutes; fixed by #1433, and by the lock since 11-11). | 3 flake reds; each put a PR out of the queue until a tick re-queued it | Keep. It is the only run on the exact commit that lands, and the first run of the suite for a docs-only PR. Drop `mypy` from it. |

### Push to `dev`, weekly, release

| Gate | Cost | Caught in window | Friction in window | Verdict |
|---|---|---|---|---|
| `test` on 3.11 and 3.12 after the merge | 72 runner-min; 3,404 in the window (16%) | 0 in 65 pushes | 1 flake made `dev` red (a5f942bd, 11-17 15:22Z, the SSE test). The last three pushes of the window waited 8 to 24 min for a runner before a test leg started; earlier that afternoon the wait was 0 to 7 min. | Keep 3.11. The 3.12 legs repeat what the PR ran. **Decision for the maintainer** (see "Next in line"). |
| `smoke`: 19 live-process tests and two tutorials | 3.8 min | 2 reds on 11-11, both a true signal: 0.26.2 changed KB registration over REST and the live test had not followed (#1455) | 0 flakes | Keep. **Make it block the release**, as ADR-0032 3a says it does (#1454). |
| `experimental` on `dev`, filing issues | 6.3 min | 0 | 1 flake (#1401) | Keep. |
| `unlocked.yml`, weekly | two legs of about 33 min | 0 from dependencies in its one run | That run was red on the SSE timing test (#1574) | Keep. Readable only after ticket 1. |
| `scripts/release.py` steps a, b, b2, c (ten preconditions, verified commit, push rehearsal, install and tutorials) | minutes, once per release | 3 releases. 0.26.1 was held only for the changelog date, as designed. 0.26.3: dry run, then execute, clean. | None recorded. The README install tag was caught by a test on the release PR, not by the script (#1436). | Keep as is. Add `smoke` to the required checks. |
| Tests that pin the process (`test_dev_process_config`, `test_release_script`, `test_release_rehearsal`, `test_verify_red_ci`, `test_test_affected`, `test_ci_workflow_triggers`) | about 5 min of test time | #1449 (ten rehearsal tests), #1612 | 0 | Keep as is. |

### The intermittent tests, counted

| Test | Red attempts | Where | State |
|---|---|---|---|
| `test_rest_import_off_the_loop` (`< 0.8` s) | 7 | 5 PR, 2 queue | Fixed today, #1617 |
| `TestSemanticKnnBudget ... [postgres]` (experimental) | 6 | 5 PR, 1 `dev` | #1401 open, cause unknown |
| `test_session_expiry_ends_the_stream` (1.5 s window) | 3 | 1 PR, 1 `dev`, 1 weekly | Fixed after the window: #1616, 16:37Z (closed #1574). First seen in October. |
| `test_mcp_dispatch_off_the_loop` (`< 0.7` s) | 2 | 2 PR | no issue |
| `test_named_rubric_checkers` (60 s subprocess) | 1 | 1 queue | no issue |

One more, not counted: a 600-second timeout on #1571 whose cause was not
established.

## The three changes

Each is one ticket. None changes what `gate` requires.

### 1. No test's verdict is a wall clock

**Observation.** 19 red run-attempts in the window came from five tests
(the table above). Three compare elapsed time with a ceiling or open a
window measured in seconds; one waits 60 seconds for a subprocess; one is
a Postgres test whose cause nobody has looked for. Twelve reds were on the
required check. No defect was found behind any.

**Principle.** The testing standard's deadline rule, and CONTRIBUTING: a
test that passes alone and fails under load is a bug in the test. Design
principle 8: the rule needs one enforcement point, and today each flake is
fixed when it is next seen.

**User's model.** "A red check means my change broke something."
**Implementation model.** A test asserts order (an event, a barrier), never
seconds; a guard in `tests/test_test_rules.py` fails a new ceiling.

```
Acceptance:   (a) Done outside this ticket: #1617 (import test) and #1616 (SSE
              expiry, #1574). Check that #1616 covered the sibling tests in
              TestSessionLifetime that set a wall-clock offset before opening
              a stream; if not, they are in scope.
              (b) test_mcp_dispatch_off_the_loop asserts order with events, as
              #1617 did for the import test. (c) test_named_rubric_checkers no
              longer decides on a 60 s subprocess timeout for `index sync`.
              (d) #1401's test is fixed, or listed in
              tests/experimental_known_failures.txt with the cause it is listed
              under. (e) A guard fails any assert that compares an elapsed-time
              name with a number, outside tests marked slow or e2e, unless the
              site is on a reviewed list. Fails today: the guard, run on dev,
              names tests/test_mcp_dispatch_off_the_loop.py:113.
Regimes:      xdist under load (the failures were 1.4 to 4.1 s against 0.7 and
              0.8 s); a broken build must fail on a generous deadline, not hang.
Shapes:       `assert <elapsed> < <ceiling>`: 13 sites in 6 files (grep below);
              7 are in test_performance.py, marked slow. A wall-clock window
              before an action (`timedelta(seconds=N)`): 3 sites. A subprocess
              timeout that decides a verdict: classify the 282 `timeout=30..120`
              sites as deadline (fine) or verdict. The reviewed list is rows
              compared as a set, with no count beside it (#1608).
Touches:      existing: tests/test_mcp_sse_session.py (only if (a) finds siblings),
              tests/test_mcp_dispatch_off_the_loop.py,
              tests/test_named_rubric_checkers.py, tests/test_test_rules.py,
              tests/experimental_known_failures.txt, CONTRIBUTING.md (Guards
              table)   new: none
Sequence:     independent. #869 (record a flake) stays open.
Model:        sonnet
heavy:        no
Cold read:    no
Out of scope: tests/e2e/test_fresh_install_write.py (#1251 owns it; e2e is not
              in gate). test_qa_duplicates.py's 10 s bounds (50 times the
              measured time; put them on the reviewed list with that reason).
```

Predicted footprint: 4 or 5 test files and one doc, about 120 lines. Two of
the five tests were fixed by outside contributors on 11-17 from groomed
issues; the two with no issue (`test_mcp_dispatch_off_the_loop`,
`test_named_rubric_checkers`) and the guard are what remains.

### 2. The pre-push run includes the tests that read the source

**Observation.** Ten red PR runs came from source-reading tests. Seven were
loop PRs, whose authors run the pre-push hook. Reproduced:

```
scripts/test-affected --list --files extensions/software-kb/src/pyrite_software_kb/board.py
```

selects 86 files and none of `test_kb_reads_are_one_function`,
`test_link_reads_pass_a_scope`, `test_response_text_is_pyrites`. Those are
the three that failed on #1553 in CI (runs 43985141356, 43988113479). For
`pyrite/services/qa_service.py` it misses three of the twelve checked.

**Principle 8.** The selection is the one place the rule "run what your
change can break" lives. Today the gap is covered by a row in the worker
skill (#1554), which is a rule each worker must remember, and outside
authors never read it.

**User's model.** "If my push passes locally, CI will not surprise me with
a rule about the source." CONTRIBUTING already promises this for guards.
**Implementation model.** Import reach cannot see a test that reads files.
A marker says which tree a test reads; a changed path under that tree
selects it.

```
Acceptance:   A change to any file under pyrite/, extensions/*/src/ or scripts/
              selects every test function that scans that tree. `--explain`
              says so. A test function that scans a source tree and carries no
              marker fails a completeness test. Fails today: a parametrised
              test over (changed file, scanning test) pairs, starting with the
              three above.
Regimes:      a deleted source file; a change only under tests/ (guards, as
              today); a full-suite trigger (unchanged); the stamp must still
              cover the selection.
Shapes:       56 files call ast.parse, rglob or inspect.getsource (grep below).
              Classify every scanning function: marked | not a scanner (the
              call parses test input) | out of scope and why. Two layouts: a
              file that is all scanners (test_layer_boundaries.py) and a
              behaviour file with one scanner (test_index_schema_drift.py: 39
              tests). Mark functions, not modules, as `guard` does. Trees read:
              pyrite/, extensions/*/src, scripts/, docs/, tests/.
Touches:      existing: scripts/test-affected, tests/test_test_affected.py,
              pyproject.toml (marker), the scanning test files (one decorator
              each), CONTRIBUTING.md (Guards), .claude/skills/pyrite-dev
              (the row becomes a pointer)   new: none
Sequence:     independent of ticket 1. After #1614 if both edit
              tests/test_cli_numeric_options_declare_a_range.py.
Model:        sonnet
heavy:        no
Cold read:    no (it only adds tests to a selection)
Out of scope: selection by execution reach (tests-q4). Registry-walking tests
              that import what they walk; import reach already finds them
              (checked: test_cli_numeric_options and
              test_every_entry_point_passes_the_policy are selected for a
              pyrite/ change).
```

Open question for the worker: one marker with an argument
(`guard(reads="pyrite")`) or a second marker. Predicted footprint: about 40
files, most of them one line; `scripts/test-affected` about 60 lines.
Changing a script widens that PR's matrix to three interpreters.

### 3. The `fix:` check judges the pull request

**Observation.** 9 red runs on 7 PRs. In 7 runs on 5 PRs the branch held
the test in a different commit. The queue's merge method is squash (ruleset
"dev: green and current"); all 84 commits on `dev` in the window are one PR
each. So no PR commit reaches `dev` on its own, and the per-commit rule
protects a history that does not exist there.

**Framing.** #1583 called the per-commit rule deliberate and fixed the
message (#1618, merged today). The message now tells an author how to
rewrite history. An upstream branch cannot be rewritten (the no-force-push
ruleset), so for the loop the only repair is a new branch. The property
worth holding is "a fix lands with a test", and the unit that lands is the
PR.

**User's model.** "I wrote the failing test, then the fix." CONTRIBUTING
teaches that order. **Implementation model.** In `--range` mode the check
passes when the range holds a `fix:` commit and some commit in the range
touches a test path. It fails, naming each `fix:` commit, when none does.

```
Acceptance:   A branch of `test: ...` then `fix: ...` passes in CI. A range
              whose only commits are fixes with no test path fails and names
              them. The local commit-msg hook is unchanged. Fails today: a
              range test for the test-then-fix shape.
Regimes:      a PR that merged dev into itself (CI passes HEAD^1 HEAD of the
              merge ref; dev's commits are not in the range); a range with no
              fix commit; frontend test paths (web/**/*.test.ts).
Shapes:       test then fix; fix then test; fix with its test; fix alone; two
              fixes and one test; fix(scope)!:; a docs-only "fix". Parametrise
              tests/test_check_fix_commit_has_tests.py over them. In the window:
              5 PRs test-in-another-commit, 2 PRs no test.
Touches:      existing: scripts/check_fix_commit_has_tests.py (check_range and
              the docstring), tests/test_check_fix_commit_has_tests.py,
              CONTRIBUTING.md (the line #1618 added; and "Rebase is the default
              merge method", which the queue's squash contradicts),
              .github/pull_request_template.md   new: none
Sequence:     independent
Model:        sonnet
heavy:        no
Cold read:    no
Out of scope: verify-red as a gate (#1104). The local hook: a commit that is
              not yet pushed can still be amended.
Decision:     Does the per-commit rule stay? Recommendation: judge the PR in
              CI. Counter-case: a fix with no test of its own can ride on an
              unrelated test edit in the same PR. verify-red already reports
              that case ("0 red" on a fix PR: 5 of 79 branches in the window),
              and the reviewer reads it.
```

Predicted footprint: 4 files, about 60 lines.

## Next in line (not drafted)

- **The fast checks get their own job and answer together.** `ruff`, the
  import-cycle check, KB validation, the `fix:` check and the guard-marked
  tests run in one job that needs no test shard, reports in about two
  minutes, prints the repair command, and never skips a shard's tests. This
  is the change that most helps outside authors (28 of their 37 classified
  red attempts involved one of these; 17 involved nothing else). It edits `ci.yml`, which is the
  maintainer's file and is pinned by two test files, so it is not "least
  risk".
- **A docs-only PR runs the tests of the pages it changes.** A small job,
  when the classifier says docs-only: `scripts/test-affected --list --files
  <changed files>` already selects the tests that name a changed non-Python
  file; run those. Design principle 9 says a doc that teaches a command is
  run as that command's test; on a pull request today it is not.
- **`smoke` blocks a release** (#1454): `release.py --require-check` adds
  `smoke` for the SHA on `dev`. ADR-0032 3a already says so.
- **`mypy` leaves the merge queue and `main`.** One line in the job's `if`.
- **The 3.12 legs on the push to `dev`.** The PR ran 3.12 and the queue ran
  the landing commit on 3.13. Dropping 3.12 from the push saves about 37
  runner-minutes a merge and eases the runner starvation in #1227, which is
  the likely trigger of the clock failures. The cost is the #400 risk for
  3.12. The maintainer set this matrix on 2026-10-21; it is his call.
- **`needs-rebase`.** 14 open PRs carry the label. 6 of them merge cleanly
  onto `dev` today, and the queue accepts a PR that is behind (four are
  queued now, 4 to 7 commits behind). The label is also set when `gate` is
  red (tick 16:17Z). The conductor can update a clean branch itself. This is
  in `pr_state.py` and the conductor skill, outside this repository.

## Leave alone

These are doing their job. No change is proposed.

- The commit-stage hooks. Under half a second for the three that were timed.
- `lock`, `kb`, `frontend`, the change classifier and `gate`.
- The full suite on the pull request, and the wider matrix for
  infrastructure changes.
- The source-reading tests and the entry-point policy ratchets. Ticket 2
  only moves where an author first meets them. The rules stay.
- The guards on test sources. The rules and messages are right. Only the
  timing for outside authors is wrong.
- Goldens and the MCP schema snapshot.
- The merge queue. It caught nothing of its own in this window, and it is
  still the only proof of the commit that lands.
- `smoke` and the tutorials as tests.
- `verify-red` and diff coverage as advisory.
- `experimental` as a non-blocking job.
- Every precondition in `scripts/release.py`, and the tests that pin the
  process.

## Keeping this table current

One script and one line per tick would rebuild every column. The script
does not exist; it is a small ticket (`scripts/gate_report.py --since
<date>`, read-only, GitHub API only).

| Column | Query |
|---|---|
| Inventory | `yq '.jobs \| keys' .github/workflows/ci.yml`; hook ids in `.pre-commit-config.yaml`; `git grep -lE 'ast\.parse\|rglob\(\|inspect\.getsource' -- tests extensions`; `git grep -nE '^[A-Z_]*(SIZE\|COUNT\|PINNED)[A-Z_]* *= *[0-9]+' -- tests`; `git grep -c 'pytest.mark.guard' -- tests` |
| Cost | `gh api repos/{repo}/actions/runs/{id}/jobs`: sum `completed_at - started_at` per job name, per event |
| Red runs | `gh api '.../actions/workflows/ci.yml/runs?event=<e>&created=>=<date>'`, then the failed jobs and their failed step names |
| Hidden reds | runs with `run_attempt > 1`: read `.../runs/{id}/attempts/1/jobs`. `gh run list` shows only the last attempt; six flakes were invisible without this. |
| Cause | the `FAILED` lines of each failed job's log, mapped by test file to a gate family. The map (about 25 rows) is the one thing a person maintains. |
| Flake or defect | automatic for a same-run red then green, and for a merge group red then green with the same PR head. Otherwise the tick's line. |
| Who | `head_repository.full_name` differs from the repository: outside |
| Conflicts | `git merge-tree --write-tree --name-only origin/dev refs/pull/N/head` for each open PR |
| Pin churn | `git log --since=<date> -G'^<PIN> *= *[0-9]+' -- tests` per pin |
| verify-red | the `test-evidence` artifact of each PR's last run (kept 90 days) |

The tick log has carried a line "Gates that went red without a defect"
since 16:08Z on 11-17. Keep it: it is the only place a person's judgment
(flake or real) is written down at the time. Also add the run's duration to
the pre-push pass stamp; without it the local gate cannot be costed.

## Could not measure

- Local hooks: how often commit, commit-msg and pre-push stop someone, and
  how long pre-push takes. Nothing records it. Two tick-log entries are the
  whole evidence.
- Runner queue time across the window. Measured only for seven pushes to
  `dev` on the afternoon of 11-17 (0 to 24 minutes from run created to a
  test leg starting). A tick reported about 50 minutes; that was not
  confirmed.
- What a red run cost a contributor in hours, and whether the 13 outside
  PRs that never went green stalled because of the gate. Five are from
  11-17 and may simply be in progress.
- Deterrence: defects not written because a gate exists.
- CodeQL (not required; not read).
- Token cost of re-queues and re-reviews.
- One failed PR run (41307200242) returned no job list.
- 0.26.2's use of `release.py`: it was cut by the curated-commit recipe.
- Test time per structural family: `.test_durations.gz` did not change in
  the window and has no entry for six of the files.

## How this was measured

- Runs: `gh api --paginate
  'repos/pyrite-wiki/pyrite/actions/workflows/ci.yml/runs?event=<e>&created=>=2026-11-01'`
  for `pull_request`, `merge_group`, `push`. 283, 109 and 68 runs; 448
  completed.
- Jobs and minutes: `.../runs/{id}/jobs?filter=latest` for every completed
  run (444 answered). Minutes are job wall time, summed.
- Causes: the log of every failed job (201 jobs, 82 runs), `FAILED` and
  `##[error]` lines. First attempts of the six re-run runs were read
  separately. 60 red PR attempts were classified by test file; a run can
  have several causes.
- Decided attempts: 243 on pull requests (237 final, 6 first attempts), 61
  red. Outside 38 of 113, loop 20 of 123, Dependabot 3 of 7.
- Pull requests: `gh pr list --state all --search 'updated:>=2026-11-01'`
  (144; 82 merged in the window, 39 from forks). Comments and reviews:
  `gh pr view N --json comments,reviews` for each; counts of gate names are
  by regular expression and are rough.
- Conflicts: a scratch clone, `git merge-tree` of each of 42 open PR heads
  against `dev` at 38d98071. 15 conflict; one on a pin-bearing file.
- Pins: `git log --since=2026-11-01 -G` per constant on `dev` (84 commits).
- verify-red: the `test-evidence` artifact of the last decided run of each
  of 114 PR branches; 79 had a verdict.
- Selection probe: `scripts/test-affected --list --files <one file>` for
  four source files.
- Hook timings: `ruff check --no-cache`, `ruff format --check --no-cache`
  and `scripts/check_import_cycles.py`, once each, on this machine.
- Clock-assert shapes: `grep -rnE 'assert .*\w*(took|elapsed|waited|duration|latency)\w* *(<|<=) *[A-Z_0-9.]+' tests extensions/*/tests`.
- Rulesets: `gh api repos/pyrite-wiki/pyrite/rulesets/{id}`.
- The docs-only skip: the job list of #1627's run 44122817671 (`test`
  skipped, `gate` success) and the `backend` filter in `ci.yml`. The four
  merged PRs: file lists with no backend path and a `.md` whose name appears
  in a test file.
- Runner wait: run `created_at` to each test leg's `started_at`, for the
  seven pushes to `dev` after 13:00Z on 11-17.
- Three data points from the 16:39Z tick were checked against the API
  before use: the #1627 skip (confirmed), the red push on a5f942bd
  (confirmed; #1616 fixed the test at 16:37Z), and #1621's red on the test
  #1617 had already replaced on `dev` (confirmed; counted among the hidden
  first attempts).
- The maintainer's loop log for the week and its two retros. The retro's
  "7 of 42 PR runs failed" is inside this count; its "pin lines changed 17
  times" was corrected there and is recounted here as 7 changes in 5
  commits.

This evaluation is wrong if the clock failures have a product cause (check:
#1617's test, now an ordering assertion, still fails under load); if the
five "regression" PRs were authors' own unfinished work that review would
have caught at a glance; or if a quiet 17 days is a poor sample for the
merge queue, whose value is the rare semantic conflict.
