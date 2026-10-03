---
name: pyrite-dev
description: "This skill should be used by an agent developing Pyrite code — fixing a bug, adding a feature, writing or running tests, debugging, or completing a backlog item — on its own branch in its own worktree. Enforces TDD, root-cause debugging and evidence-before-claims, and ends with a report the conductor can review. For picking work, dispatching agents, reviewing branches, opening PRs, releasing or deploying, use pyrite-conductor."
---

# Pyrite Development Skill (the worker)

**Announce at start:** "I'm using the pyrite-dev skill."

You develop Pyrite on **one branch, in one worktree, on one theme**. Picking the
theme, and reviewing, readying or merging the PR, belong to
[pyrite-conductor](../pyrite-conductor/SKILL.md); you never touch `dev`. If
nobody dispatched you, you are also the conductor: finish here, then load
pyrite-conductor for review and the PR.

A theme produces code and what was learned building it; record both. Why each
rule exists: [history.md](history.md).

## The Iron Laws

```
1. NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST
2. NO FIX ATTEMPTS WITHOUT ROOT CAUSE INVESTIGATION
3. NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE
4. NO BACKLOG CHANGES WITHOUT USING THE CLI (`pyrite update`, `pyrite create`)
```

## Where you are

```bash
git branch --show-current     # feature/*, fix/*, kb/* or process/* -- never dev
pwd                           # a worktree under ../pyrite-wt/, with its own .venv
.venv/bin/pyrite kb list      # the `pyrite` KB path must be THIS worktree's kb/
```

- Any of them wrong: stop, and run `scripts/new-worktree.sh <branch>` from the
  main checkout (ADR-0032).
- Use the worktree's `.venv/bin/...`; the main checkout's venv imports the main
  checkout's code.
- Sub-agents share your branch and worktree: give them disjoint files, never
  `isolation: "worktree"`.
- Stage explicit paths, never `git add -A`.

## Before you build

0. Read `kb/design.md`. A ticket's suggested fix is evidence, not the plan: if
   the work as described runs against a principle there, stop and say so on
   the ticket.
1. Read the ticket and its `## Groom` (`gh issue view N`, `pyrite get <id> -k
   pyrite`), then the contracts and pointers it names. With no groom, find
   them: `pyrite search "<topic>" -k pyrite`, `pyrite sw adrs`,
   [architecture.md](architecture.md).
2. **Test the groom's riskiest assumption first**: its "this groom is wrong
   if" line, then its out-of-scope list. Use one command or one failing test,
   and post the result on the draft PR. If it changes the scope, stop.
3. **A mission brief (a goal state and invariants, not steps) gets a plan
   before code**: one page on the draft PR, led by the result of step 2. It
   states the goal as properties, the surfaces and callers found by grep, what
   must not change, the tests (negative, environment and concurrency cases
   included), what is out of scope, and open questions. Wait for the answer.
4. The groom's open questions are yours to explore; overturn its
   recommendation when the evidence says so.

Bugs and user requests live in GitHub Issues, the roadmap (epics, backlog
items, ADRs) in `kb/`, never both (ADR-0033). A bug you fix here needs no
issue. A bug you find and do not fix:
`gh issue create --label bug --label <area>`, with placeholders for anything
private. A security finding goes to the conductor, not a public issue.

Editing a function on the `tests/test_layer_boundaries.py` allowlist? Remove
its entry: move the storage access behind a service.

## Test-driven development

RED, verify RED (it fails for the right reason), GREEN (the minimal code),
verify GREEN, REFACTOR, commit with a conventional prefix. Code written before
its test is deleted and started over. Patterns: [tdd.md](tdd.md).

## Debugging

Find the root cause before any fix: reproduce, trace the bad value to its
origin, test one hypothesis at a time ([debugging.md](debugging.md)). After
three failed fixes, stop and question the design in your report.

You may pull the Andon cord (pyrite-conductor, "The Andon cord"): when the
work is churning, when the fix you are asked for would patch one instance of
a missing rule, or when it runs against `kb/design.md`, open an `andon` issue
with the evidence and stop. That is a finding, not a failure.

## Verification

Run the command that proves the claim, read its output, then claim. "Should
work" is not evidence.

| Claim | Run | Look for |
|---|---|---|
| Backend tests pass (local) | `scripts/test-affected --run` | `N passed, 0 failed` |
| Backend tests pass (all) | the draft PR's CI: `gh pr checks <n>` | `test (3.12)` and `gate` pass |
| The fix is real | `scripts/verify-red.sh`, once (CI's `verify-red` job runs the same code) | `verify-red: N red · 0 import-only · 0 unexpected pass · 0 n/a`; paste its summary line into the report |
| Each guard is tested | delete that guard alone, run the tests | a test fails, for every guard you added |
| Frontend passes | `cd web && npm run check && npm run test:unit && npm run build` | all green |
| Lint passes | `.venv/bin/ruff check . && .venv/bin/ruff format --check .` | clean |

- A test meant to pass without the fix gets `@pytest.mark.control(reason="...")`;
  a bare marker is rejected.
- `experimental` marks tests of experimental surfaces (#657). It is applied
  from `tests/experimental_surface.py`, never by hand. Those tests do not block
  a merge or a push; CI's `experimental` job runs them against
  `tests/experimental_known_failures.txt`, which can only shrink. Run them with
  `scripts/test-affected --run --experimental`. A security test is never
  experimental: list a new one in that file's `NEVER_EXPERIMENTAL` if its path
  is mapped experimental. `test_experimental_surface.py` fails on an
  experimental test that touches security vocabulary until you list it there or
  in `REVIEWED_EXPERIMENTAL` with the reason it is not one.
- A `fix:` branch whose line shows **0 red, or only import-only reds, is not
  done**: write a test that fails on the bug's behaviour, or say why none can.
- Commit each passing step as you reach it, with the reason in the message.
  Uncommitted work and the thinking behind it are lost when a session stops
  (2026-10-02: a worker died with eight files edited and no note of why).
- Open a draft PR right after your first push (`gh pr create --draft --base dev
  --fill`, `Fixes #N` in the body). Its CI is the authority.
- Test each tree once: run `scripts/test-affected --run` in the foreground on a
  committed tree, and the pre-push hook reuses the pass.
- After a failure that looks load-caused, re-run only the failures
  (`scripts/test-affected --run -- --lf`); never start a second suite while
  one is running.
- A test that passes alone and fails in parallel is a bug in that test.

## Capture what you learned

Put each finding where the next person will meet it, the first that fits:

- behaviour the code must keep: a test named for the property;
- code that looks wrong and is right, or the reverse: a comment at that line;
- how a component works, where its doc is silent or wrong: `kb/components/`;
- the groom was wrong: a comment on the ticket, with file:line;
- a decision that could have gone another way: `pyrite sw new-adr "Title" -k
  pyrite --status proposed` (accepting it is the maintainer's);
- a trap with no closer home: [gotchas.md](gotchas.md).

## KB bookkeeping

Use the CLI on your branch; never hand-edit frontmatter.

- Closed a backlog item: `pyrite update <id> -k pyrite -f status=done && git mv
  kb/backlog/<id>.md kb/backlog/done/` (`done`, never `completed`).
- New component: `pyrite create -k pyrite -t component ...`.
- Then `.venv/bin/pyrite index sync`, and check `pyrite search "<feature>" -k
  pyrite` finds it.
- One user-visible change, one fragment `changelog.d/<slug>.<section>.md`
  (`added changed deprecated removed fixed security`). Never edit
  `CHANGELOG.md`; a test asserts `[Unreleased]` is empty.

## Finishing: the report

Done means the whole theme, with evidence for every claim. Then, in order:

1. **Diff your footprint**: `git diff --name-only origin/dev...HEAD`. A file on
   the out-of-scope list is not yours: find how it got in.
2. **Clean the history.** You committed at every passing step; the reviewer
   reads commits, not checkpoints. Rewrite the branch into the few commits the
   idea needs, each one coherent with a message that says why: `git fetch
   origin dev`, `git rebase origin/dev`, note that head, then `git reset
   --soft origin/dev` and recommit by explicit paths (or `git commit --fixup`
   as you go and `GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash
   origin/dev`). `git diff <noted-head> HEAD` must be empty: the rewrite
   changes history, never content. Then `git push --force-with-lease`. Only your own branch, and only before the PR
   is marked ready.
3. **Push**: `git push -u origin <branch>`. A red pre-push in a test your change
   touches: fix it. In a test it does not touch: re-run that test alone, and if
   it passes, push with `--no-verify` and name the test in the report. If CI
   then fails the same test, it is yours to fix.
4. **Report with the pushed SHA.** The PR stays a draft.

```
Branch:      fix/what-it-fixes      Worktree: ../pyrite-wt/fix-what-it-fixes
Pushed:      <sha> == origin/<branch>
Commits:     <n>, one line each
Closes:      #N, #M  (or backlog item ids)
Evidence:    the test-affected line; the draft PR's CI on the pushed SHA; the
             `verify-red: ...` line, with a reason for each non-red test; lint;
             Regimes, one line each
Guards:      each check you added -> the test that fails when it alone is removed
Changed:     files, new vs existing; the count against the groom's prediction
Covers:      for a property that says "every" or "all": each instance found by
             search, marked guarded (test) | already safe (why) | out of scope (why)
Learned:     the riskiest-assumption result, then what the groom got wrong,
             what the code turned out to do, contracts you found; one line
             each, with file:line
Captured in: per Learned line, the test, doc path or commented line holding it
Tokens:      as the harness reports them, or "not reported"
Unsure:      what a reviewer should look at twice
Left:        anything in the theme you did not finish, and why
```

A `Learned` line with nothing in `Captured in` is not captured yet.

## References

[testing.md](testing.md); [data-pipelines.md](data-pipelines.md) (the entry
lifecycle); [extensions.md](extensions.md) (building a plugin);
[gotchas.md](gotchas.md) (read before touching hooks, DB access or entry ids).
