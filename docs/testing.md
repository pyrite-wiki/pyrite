# Test evidence: verify-red and diff coverage

Two advisory checks run on every pull request that changes backend code. Each
answers a question the other cannot:

| Check | Question | Blind spot |
|---|---|---|
| **Diff coverage** | Is every changed line run by some test? | Code that runs but whose result no test checks |
| **verify-red** | Do the PR's new tests notice the change as a whole? | Which part of the change each test notices (per-hunk revert, planned for 0.27) |

Neither fails `gate` yet. Each PR's numbers go into a `test-evidence` artifact;
after about ten PRs the maintainer decides what, if anything, becomes required.

## verify-red

```bash
scripts/verify-red.sh                 # against origin/dev (else dev)
scripts/verify-red.sh --json e.json   # also write the evidence file
```

The runner (`scripts/verify_red_ci.py`) never modifies your checkout:

1. It checks out the merge base with the base branch into a throwaway
   `git worktree --detach` under `$TMPDIR`.
2. It copies in your test-side files: everything under `tests/`,
   `extensions/*/tests/`, and any `conftest.py`, committed or not.
3. It runs only the tests you **added or edited**. That is the run *without
   the fix*. A test counts as edited when its AST changed, so reformatting a
   test does not count.
4. It copies in the rest of your change, so the throwaway tree now matches
   your working tree, and runs the same tests again. That is the run *with the
   fix*.
5. It removes the throwaway tree.

Nothing is ever restored, because nothing you own was changed. If a run is
killed outright, it can leave a stale directory under `$TMPDIR`. Once that
directory is gone, the next run prunes its worktree registration.

Both runs put the throwaway tree first on `PYTHONPATH`, and the runner refuses
to run (exit 2) if a package in that tree imports from somewhere else, such as
another checkout's editable install (#189). A top-level package or extension
that only the PR adds is stubbed out in the run without the fix, so the head's
editable install cannot serve it there. Both runs also set
`PYTHONDONTWRITEBYTECODE`, so the second run can't import stale bytecode from
the first. Both get a scratch `HOME`, `PYRITE_CONFIG_DIR`, `PYRITE_DATA_DIR`
and `XDG_*` directories, so a new test running against buggy code cannot
write to your real config. `HF_HOME` still points at your model cache.

A PR whose only non-test changes are changelog fragments, `kb/` entries or
Markdown has nothing to verify. Those files are still left out of the run
without the fix, since a test may read them. A pytest run with the fix that
collects no test file at all (a conftest or plugin that breaks before
collection) is the check failing: exit 2, with pytest's last lines in the
summary. A file that collects but runs nothing, such as one that skips itself
when an optional dependency is missing, makes its tests *n/a* instead.

### Verdicts

The output is one line, then a table of every test that is not *red*:

```
verify-red: 3 red · 1 import-only · 0 unexpected pass · 0 n/a (12 pre-existing tests in these files not run)
```

- **red**: the test passes with the fix and fails without it. This is the evidence a review wants.
- **import-only**: the test fails without the fix only because the code it needs
  is missing. Either its file does not collect, or it raises `ImportError`,
  `AttributeError` or `NameError` naming an identifier the PR adds. The runner
  finds those identifiers by diffing the ASTs of the changed code. It reads
  the exception object itself, never the traceback, so the repo's
  `--tb=short` has no effect. This verdict is weak: it shows the test needs
  your code, not that it checks what the code does.
- **unexpected pass**: the test passes without the fix, so it does not test the
  change. In CI it gets a warning annotation. For a test you mean to pass
  either way, such as a "this still works" guard, add
  `@pytest.mark.control(reason="...")`, or the marker plus a docstring that
  says why. It then counts as **control**. A bare marker is rejected and the
  test stays an unexpected pass.
- **n/a**: no claim. The test failed or skipped with the fix, skipped without
  it, was not collected, or its run timed out.

Tests the PR did not add or edit are counted, not listed. Workers run
`scripts/verify-red.sh` once before reporting and paste the summary line. The
conductor reads the CI job's line instead of running it again.

### Known limits

These are not fixed; weigh the line with them in mind.

- **Red through a wrapper is weaker evidence.** A `TypeError` from a keyword
  argument the PR adds, a failure wrapped in an `ExceptionGroup`, or a
  failure that a `CliRunner`, `TestClient` or MCP wrapper turns into a status
  code or an output string all count as *red*, not *import-only*. The runner
  only reads the exception that reaches pytest.
- **A conftest that imports a name the fix adds** stops pytest before any test
  runs without the fix, so every selected test in that run is *n/a*.
- **A SIGKILLed or SIGTERMed driver orphans its pytest child**, which keeps
  running in the throwaway tree until it finishes. Your checkout is still
  untouched.
- **Any Markdown counts as not code.** A PR that changes only a skill,
  `CONTRIBUTING.md` or other `.md` file, plus a test that pins its text, has
  nothing to verify.
- **Any docstring satisfies `control`.** The runner can't tell a docstring
  that says why the test passes without the fix from one that only says what
  it tests.
- **Only packages with an `__init__.py` are stubbed.** A top-level module, a
  namespace package or an extension without a `src/` layout that only the PR
  adds can still be imported in the run without the fix. That produces a
  false *unexpected pass*, never a false *red*. A new extension reached
  through the plugin registry is *red*, not *import-only*, since the registry
  wraps the import error.
- **One scratch `HOME` serves both runs**, so the run without the fix can
  leave files the run with the fix sees. It also hides `~/.gitconfig` and the
  Playwright browser cache, so tests that need them are *n/a*.
- **A conftest or plugin hook that crashes after collection reads as *n/a*,
  not exit 2**, and so does a new test deselected by the default `-m` filter
  (`slow`, `e2e`). Both show "not collected", which isn't the real reason.
  Either way the line claims nothing, so a `fix:` PR with it still goes back
  for having no red test (#409).
- **Stale-tree cleanup assumes absolute `gitdir` paths.** With
  `worktree.useRelativePaths` set, a live run's registration could be removed.
  The cleanup also ignores `git worktree lock`.

## Diff coverage

On a pull request, the 3.12 leg of the `test` job runs the suite under
`pytest --cov`, with 3.12's low-overhead `sysmon` tracer. Then
`diff-cover coverage.xml --compare-branch=<base> --fail-under=80` writes the
changed lines that no test runs to that job's step summary. A result below 80%
produces a warning annotation, never a failure. When the PR changes no measured
line, diff-cover reports 100%; the evidence file records that as `null`, and
the summary prints `n/a`.

## The evidence file

The `verify-red` job waits for `test`, whatever its result, and writes
`test-evidence.json` (artifact `test-evidence`, kept 90 days):

```json
{
  "pr": 393,
  "head": "<the PR's head sha>",
  "merge_commit": "<the merge commit CI checked out>",
  "merge_base": "<sha>",
  "fix_commits": 1,
  "verify_red": {"red": 3, "import-only": 1, "unexpected pass": 0, "n/a": 0,
                 "control": 0, "pre-existing": 12},
  "diff_coverage": {"percent": 87.5, "lines": 40, "uncovered": 5}
}
```

`verify_red` is `null` when there was nothing to verify. That happens when the
PR has no code change, or no new or edited test. `diff_coverage` is `null`
when the test job produced no report. To collect the file:
`gh run download <run-id> -n test-evidence`.

## Experimental tests and the ratchet

A third CI job, `experimental`, is not about the PR's own tests: it keeps the
tests of experimental features (#657) running without letting them block a
merge.

- **What is experimental**: the surfaces `kb/designs/alpha-supported-surface.md`
  marks experimental. `tests/experimental_surface.py` maps them to test paths
  and node ids, and the root `conftest.py` applies the `experimental` marker
  from it. Security properties are never experimental; that file lists them
  and `tests/test_experimental_surface.py` checks both lists against a real
  collection.
- **The gate**: the `test` job (and the merge queue's full matrix) runs
  `-m "not slow and not e2e and not experimental"`. `gate` needs `test`, not
  `experimental`.
- **The ratchet**: `experimental` runs `-m "experimental and not slow and not
  e2e"` on Python 3.12 with Postgres, on every backend PR and every push to
  `dev`, and hands the JUnit report to `scripts/experimental_ratchet.py check`.
  The job is red only for news: a failure in neither
  `tests/experimental_known_failures.txt` nor an open `experimental-broken`
  issue (the job reads them with `issues: read`); a PR that adds a line to
  the list, which can only shrink; or a run that did not complete (no report,
  or a pytest exit status other than 0 or 1; the run step times out before
  the job so the ratchet still runs). A listed test that passes or no longer
  exists is named for removal, and a filed test that passes again is named so
  its issue can be closed.
- **Never silently**: on a push to `dev`, whenever `experimental` did not
  succeed (failed or cancelled), the `experimental-issues` job (the only job
  with `issues: write`, using the workflow's token) opens or comments on an
  `experimental-broken` issue: per test file with a new failure, for a grown
  known-failures list, and for a run that did not complete, including one
  that left no result at all. On a PR the news is in the job summary only.
- **Collection errors**: a file that fails to import stops the core run unless
  every test in it is experimental by the mapping; then the core run warns and
  continues, and the experimental job reports it as a failure of that file.

Run either set locally:

```bash
.venv/bin/pytest tests/ extensions/ -n 4 -m "not slow and not e2e and not experimental"
.venv/bin/pytest tests/ extensions/ -n 4 -m "experimental and not slow and not e2e"
```
