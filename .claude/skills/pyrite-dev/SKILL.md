---
name: pyrite-dev
description: "This skill should be used by an agent developing Pyrite code — fixing a bug, adding a feature, writing or running tests, debugging, or completing a backlog item — on its own branch in its own worktree. Enforces TDD, root-cause debugging and evidence-before-claims, and ends with a report for the PR. Opens a draft PR after the first push; the maintainer reviews and merges it. Process commands (setup, hooks, branches, PRs) live in CONTRIBUTING.md."
---

# Pyrite Development Skill

**Announce at start:** "I'm using the pyrite-dev skill."

You develop Pyrite on **one branch, in one worktree, on one theme**, and you
never push to `dev`. Review and merge are the maintainer's. Every process
fact (setup, hooks, branches, the PR, the heavy-run lock) has one home,
[CONTRIBUTING.md](../../../CONTRIBUTING.md#branches-hooks-and-pull-requests),
"Branches, hooks and pull requests"; this skill gives the method and links there rather than repeating
it.

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
git branch --show-current     # fix/*, feature/*, docs/*, kb/* or process/* -- never dev
pwd                           # your checkout; a worktree under ../pyrite-wt/ if others share the clone
.venv/bin/pyrite kb list      # the `pyrite` KB path must be THIS worktree's kb/
```

- On `dev`, or sharing a clone with another session or agent, or a wrong
  `pyrite` KB path: stop and set up a worktree (CONTRIBUTING,
  [Initial Setup](../../../CONTRIBUTING.md#initial-setup)).
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
private. A security finding goes to the maintainer privately (SECURITY.md), never to a public issue.

Editing a function on the `tests/test_layer_boundaries.py` allowlist? Remove
its entry: move the storage access behind a service.

## Test-driven development

RED, verify RED (it fails for the right reason), GREEN (the minimal code),
verify GREEN, REFACTOR, commit with a conventional prefix. Code written before
its test is deleted and started over. Patterns: [tdd.md](tdd.md).

**A test's expected value comes from the property, never from the code under
test.** Never build an expected value, model or checker by restating the
module's own scan, model or check: a copy shares the code's blind spots, so the
test passes for the wrong reason and verify-red cannot see it. Where the
property is about bytes ("everything not named is unchanged"), compare bytes
directly, before against after. Where it is about behaviour, write the
expected value by hand or take it from a reference tool (YAML, git, the OS).
(#732: an "independent" oracle copied the module's line scan character for
character, and three comment-losing edits passed 2,602 cases.)

## Debugging

Find the root cause before any fix: reproduce, trace the bad value to its
origin, test one hypothesis at a time ([debugging.md](debugging.md)). After
three failed fixes, stop and question the design in your report.

You may stop and say so: when the work is churning, when the fix you are asked
for would patch one instance of a missing rule, or when it runs against
`kb/design.md`, comment on the ticket with the evidence and stop. That is a
finding, not a failure.

## Verification

Run the command that proves the claim, read its output, then claim. "Should
work" is not evidence.

**CI is the verification gate.** Locally, run the tests of the files you
changed; then push to your draft PR and read its CI. Don't hold a push for a
slow local run of the whole affected set (how the hooks, the heavy-run lock
and a verified commit work: CONTRIBUTING, [Running the
tests](../../../CONTRIBUTING.md#running-the-tests)).

| Claim | Run | Look for |
|---|---|---|
| Backend tests pass (local) | `scripts/test-affected --run` | `N passed, 0 failed` |
| Backend tests pass (all) | the draft PR's CI: `gh pr checks <n>` (the whole suite locally: `scripts/heavy_lock.py -- .venv/bin/pytest tests/ extensions/*/tests/ -n 4 -q`) | `gate` passes (it waits for every `test (<py>, <shard>)` leg) |
| The tests guard the change (every branch that changes code, `feat` included) | `scripts/verify-red.sh`, once (CI's `verify-red` job runs the same code) | `verify-red: N red · 0 import-only · 0 unexpected pass · 0 n/a`; paste its summary line into the report |
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
- A branch that changes `pyrite/` or `extensions/` code, `feat:` as much as
  `fix:`, whose line shows **0 red, or only import-only reds, is not done**:
  write a test that fails on the bug's or the feature's behaviour without the
  change, or say why none can. A passing test proves nothing until it has
  been seen to fail without the change (retro 2026-10-03: in 2 of 3 themes a
  cold read found tests that passed with the change removed; #701's 15 parity
  tests passed with alias handling disabled, and its worker had skipped
  verify-red as "a feat branch with no bug").
- Commit each passing step as you reach it, with the reason in the message.
  Uncommitted work and the thinking behind it are lost when a session stops
  (2026-10-02: a worker died with eight files edited and no note of why).
- Open a draft PR right after your first push, with `Fixes #N` in the body;
  its CI is the authority. Leave it a draft until your report is done (steps
  and fork flags: CONTRIBUTING, [Branches, hooks and pull
  requests](../../../CONTRIBUTING.md#branches-hooks-and-pull-requests)).
- Commit each passing step as you reach it, with the reason in the message.
  Uncommitted work and the thinking behind it are lost when a session stops
  (2026-10-02: a worker died with eight files edited and no note of why).
- Test each tree once, and after a load-caused failure re-run only the
  failures (`scripts/test-affected --run -- --lf`). Never start a second heavy
  run while one is running; the lock and its wrapper are in CONTRIBUTING,
  "Running the tests".
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
3. **Push** (CONTRIBUTING, "Branches, hooks and pull requests"). A red
   pre-push in a test your change touches is yours to fix. If you push past an
   unrelated flake as that section allows, name the test in the report.
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
