---
id: tests-q3-a-weak-test-fails-a-lint-assert-free-is-not-none-only-sleep-and-repo-file-reads
title: 'Tests Q3: a weak test fails a lint - assert-free bodies, is-not-None-only checks, bare sleeps, and tests that read the repository'
type: backlog_item
tags:
- quality
- testing
importance: 4
kind: tech_debt
status: planned
priority: high
effort: M
rank: 0
---

> **Decision 2026-10-14 (Mark):** all eight test themes accepted as planned ("I think all the themes make sense"). Q1 and Q2 tentatively in the next release.

Source: `test-architecture-review-2026-10`, findings 3 and the process section. `tests-t8` is the one-time sweep of pre-#360 tests; this is the rule that keeps the tree swept, and it can land before or after T8.

## Problem

Four shapes of weak test recur, and each has been the cause of a real miss:

- Assert-free or type-only bodies: 231 lines whose only check is `assert x is not None`; 159 lines ending in `exit_code == 0` with no check of the output; T8 counted 28 tests with no assertion at all on 2026-09-25. The `a-well-named-test-...` item records the #69 near-miss: a well-named test over an uncovered case defeats review.
- `time.sleep` as synchronisation: 25 calls in 12 files; T7 names the `for _ in range(50): sleep(0.1)` loops whose budget falls through silently.
- Tests that read the repository: `tests/test_orient_is_true_as_the_first_call.py:742-745` and `tests/test_changelog_fragments.py` read `CHANGELOG.md`/`changelog.d`. #844 deleted a fragment by design and the merge queue went red with `FileNotFoundError` (run 38449104863).
- Asserts without a message in parametrised and cross-surface tests: 2,117 of 17,440 asserts carry one. A failure in `test_private_kb_read_scoping.py::test_no_private_content_in_the_body[...]` (104 instances) shows a body and an id, not which principal on which surface was promised what.

The pattern exists: `tests/test_cli_help_assertions_are_plain.py`, `tests/test_experimental_surface.py`, `TestEveryTestIsBounded`.

## Acceptance

One file, `tests/test_tests_are_not_weak.py` (name is the worker's), with one test per rule, each walking `tests/` and `extensions/*/tests` by AST and failing with `file:line: <rule>`:

- a test function whose body has no `assert`, `pytest.raises`, `pytest.fail`, `approx` or a call into a helper from an allowlisted assertion module (`tests/outcome_walk.py`, `tests/characterization/*`, `tests/file_operations_oracle.py`) fails;
- a test whose asserts are all of the form `x is not None`, `isinstance(x, T)` or `result.exit_code == 0` fails;
- a `time.sleep(` outside `tests/e2e` and outside a function whose name contains `wait`/`poll` with a deadline argument fails (T7 fixes the sites; this keeps them fixed; the two may land in either order, the allowlist absorbs the gap);
- an `open(`/`read_text(`/`Path(` on a path that resolves outside `tests/**` and outside `tmp_path` (`"CHANGELOG.md"`, `"changelog.d/..."`, `"README.md"`, `"docs/..."`) fails unless the file is in `_DOCS_AS_TESTS` with a reason (`tests/test_doc_write_as_patch.py`, the tutorial runner);
- a parametrised test (`@pytest.mark.parametrize` or a `for`-loop over surfaces) whose asserts carry no message fails. Advisory first: the worker reports the count and the maintainer decides whether this rule gates (decision below).

Each rule has an allowlist that can only shrink, seeded from the current tree, with the count in the PR body. Fails today: the first four rules on their seeded offenders (the worker names three each in the PR).

## Decision

Whether the no-message rule gates or stays advisory. Recommendation: gate it for parametrised tests only (where the id is not enough to know what failed) and leave single-case tests alone; the sentence-style names (3,001 over 45 characters) already carry the property there.

## Footprint

New: `tests/test_tests_are_not_weak.py` (~300 lines including allowlists). Existing: `tests/test_orient_is_true_as_the_first_call.py` (move the fragment text into `tests/fixtures/`, -15/+5), `kb/standards/testing-standards.md` (+10). Model: sonnet. Sequence: independent of Q1 (disjoint files); after Q2 so the walk's own runtime shows in the durations. Must not break: `tests/test_changelog_fragments.py` is a contract test of the changelog mechanism and belongs in `_DOCS_AS_TESTS`, with a reason.

Checked: the counts by grep; the run-38449104863 traceback. Assumed: AST walking 428 files stays under 2 s (collection of the whole tree is 6.2 s). This groom is wrong if the assert-free count is dominated by Hypothesis `@given` bodies that assert through a strategy (then that decorator joins the allowlisted forms).
