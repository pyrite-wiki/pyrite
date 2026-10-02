# Review: from a worker's branch to a merged PR

The conductor's review is the reason the maintainer can review a PR in five
minutes. Everything here happens **before** `gh pr create`.

## The checklist

**Claim the review first.** More than one conductor can be alive at once —
a tick revived by its worker's hand-back, the scheduled tick, the host — and
on 2026-09-18 two of them cold-read PR #69 in parallel and a third rebased
it (#111). Before reading a diff: `gh pr view N --json labels`; if
`in-review` is set, skip the PR this tick. Otherwise `gh pr edit N
--add-label in-review`, and remove the label when you flip it to ready,
redispatch it, or stop reviewing it. A label older than an hour with no
comment from its owner is stale: take it over and say so on the PR.

**A red pre-push is information, not a wall.** The local run is the first of
several gates: PR CI, the merge queue's full matrix on the exact commit, the
push to `dev`, `main` and review all come after it, so it does not have to be
perfect (maintainer, 2026-09-25). A failure in a test the change touches is
real: hand it back to the worker. A failure in a test it does not touch --
common while sibling worktrees run suites (#112) -- is re-run alone; if it
passes alone, push with `--no-verify` and say so on the PR, and let CI decide.
File an issue only if the same test fails that way again. Name the skipped test in the PR, so a reviewer sees it and a repeat is visible. If PR CI or the merge queue then fails the same test, it is a real failure the change caused -- a test the change never touched can still be broken by it (2026-09-25: a module reload in one test broke an unrelated one only after the batch's change) -- and it goes back to the worker. Do not spend a
worker round making a laptop run pristine.

In **your own review worktree** on the pushed head — never the worker's
(`scripts/new-worktree.sh review/<slug> origin/<branch>`; #119). First:
`git rev-parse HEAD` equals the SHA in the worker's report and
`gh pr view N --json headRefOid`; a green check is a claim about a commit,
so identify the commit before believing the check (#91 — a docs-only
classification of an unpushed branch once reported `gate: success` for
code nobody had pushed).

```
- [ ] git log dev..HEAD --oneline        commits are focused, messages say why, Fixes #N present
- [ ] git diff dev...HEAD                 READ IT. Every hunk. The report is not the diff.
- [ ] before trusting a suite number, `python -c 'import pyrite, <ext_pkg>; print(...)'` must point into
      the worktree; make review worktrees with `scripts/new-worktree.sh` (never a symlinked .venv — #189,
      a review worktree whose .venv symlinked the main checkout's resolved every extension package to an
      editable install on `dev`, not the branch under review, and a suite number measured that way is not
      evidence)
- [ ] the full suite: the PR's CI on the pushed SHA (`gh pr checks N`; `test (3.12)` and `gate` green) --
      NOT a local re-run. The worker opened the draft PR after its first push, so CI has usually finished
      by review; locally, `scripts/test-affected --run` on the head is enough (#356: full local suites from
      several worktrees at once filled the disk and pushed load past 25). Run the full suite here only to
      reproduce a CI failure
- [ ] test each tree once: do not re-run a suite on a tree CI or a pass stamp already passed -- read the
      result (`gh pr checks N`; `scripts/test-affected --run` answers `already passed on tree <sha>` and
      exits in a second when the worker's run stamped that tree). Run only targeted tests for a specific
      claim or probe (`pytest <file>::<test>`), never a second copy of a suite that is already going
- [ ] the new tests notice the change: read the `verify-red` job's summary line on the PR's latest push
      (`gh run view <id>` / the Checks tab; `test-evidence.json` in the run's artifacts) and do not re-run it
      locally. The job (`scripts/verify_red_ci.py`) runs the PR's new and edited tests on a throwaway
      checkout of the merge base plus the PR's test-side files, then with the whole change. Every test the PR
      names for the fix should be `red`. `import-only` (it fails only because a name the PR adds is missing),
      `unexpected pass` on a test not marked `@pytest.mark.control`, and `n/a` need a look at that test (PR
      #69: a class named for the exact regression it reintroduced covered only cases that already passed,
      and read as tested). The worker's report pastes the same line from its own run; if the two disagree,
      trust CI and ask why. A PR with a `fix:` commit whose line has **0 red, or only import-only reds, goes
      back** to its author unless the PR states why (an environment-only bug like #373, a fix only a
      wrapper-level test can see); `unexpected pass` or `control` tests do not count as evidence for it.
      A manual check replaces the job only where
      the PR itself touches `scripts/verify*red*` or the `verify-red` job in `.github/workflows/ci.yml`: the
      job runs the PR's own copy, so its table cannot vouch for itself. The manual check runs **dev's** copy,
      never the PR's, with the PR's worktree as the working directory:
      `d=$(mktemp -d); for f in verify_red_ci.py verify_red_record.py; do git show origin/dev:scripts/$f > $d/$f; done; (cd <PR worktree> && .venv/bin/python $d/verify_red_ci.py)`.
      (Before the throwaway-tree runner is on dev, dev's copy reverts in place: run it in a scratch clone
      of the PR branch, not in any worktree.) The job and diff coverage (the test
      job's summary) are advisory; after ~10 PRs of `test-evidence.json` the maintainer decides on gating
- [ ] any number in the report (faster, slower, N% fewer rewrites, a flake rate) was measured against the
      MERGE BASE, under ONE interpreter with the source tree pinned, and the sentence that reports it states
      the condition — two sessions each published a confident wrong number about #69 in one window (one
      across two venvs, one against dev instead of the merge base) — each worktree has its own .venv and they resolve different Pythons; "run it
      here, then there" compares environments (PR #69: a published "not slower" and a cold read's "+71%"
      were both 3.11-vs-3.13 artefacts) — or the number is struck from the PR
- [ ] ruff check . && ruff format --check .
- [ ] theme complete? nothing in "Left:" that belongs to this PR
- [ ] learning is captured: every `Learned` line has a `Captured in` entry, and that test, comment or
      doc is in the diff; where the worker found the groom wrong, the ticket carries a comment saying so
- [ ] footprint: files changed against the groom's prediction, and `Tokens`, go in the tick entry
- [ ] a changelog fragment `changelog.d/<slug>.<section>.md` per user-visible change, and CHANGELOG.md
      NOT edited (a bullet under [Unreleased] is the conflict #243 removed; tests/test_changelog_fragments.py
      fails on one); KB updated via CLI where the theme touched it
- [ ] no private material, no absolute home paths (git grep -n "/Users/" -- the branch's new files)
- [ ] the diff stays inside the theme's footprint: nothing from another theme's out-of-scope list (#119 — 18
      foreign commits were one push from the wrong PR; the worker caught it, the reviewer must too)
- [ ] cold read needed? (below)
```

What you look for in the diff, beyond "does it work": a change the ticket
did not ask for; a test that asserts the implementation rather than the
behaviour; error handling that swallows; a public shape (CLI flag, REST
field, MCP tool argument) that changed without a note; anything the worker
marked "Unsure".

## Outside contributors' PRs

Same checklist, three differences. The cold read is always on (the author
is unknown to the loop and the reviewer's job is to be unimpressed). The
review comment is written for a person who is not on the team: say what
was checked and what was found, thank them for what is good specifically,
and ask for changes as a list they can act on without a second round of
questions. And the outcome is never "merge": it is a recommendation to the
maintainer — merge as is; merge after these changes; wait for <PR>; or,
for competing PRs on one issue, which one, why, and what the other one got
right that should be credited or folded in. Two PRs for #97 arrived twenty
minutes apart on 2026-09-18; that will happen again now that issues carry
reproductions.

**Bodies go through files, always.** `gh pr comment`, `gh pr edit --body`,
`gh issue create`, `pyrite create -b`: write the text with the Write tool and
pass `--body-file` / `"$(cat file)"`. Inline prose in a double-quoted shell
string executes every backtick as a command — on 2026-09-18 a comment
describing a recovery ran `git rebase` on `dev` in the main checkout (#123),
and three of the host's own commands were lost to the same quoting.

**Cleaning up an outside PR: one push.** When the maintainer has said a
contributor's PR is to be landed and the loop prepares it, do everything
locally first — rebase onto current `dev`, the single credited fixup, the
co-author trailers — verify (suite, `verify-red`, lint), and then push
**once**, `--force-with-lease=<branch>:<their current head>` with the SHA
read from `gh pr view N --json headRefOid` at that moment. Every push by a
non-owner to a first-time contributor's fork re-arms GitHub's approval gate,
so three pushes are three clicks on the maintainer's desk (#116 cost three,
#108 three, in one hour). Time the push for when the maintainer is present
to approve the run, and say in the comment that the run needs approval.
The `in-review` label is the lock: while it is set, nobody — including the
maintainer via "Update branch" — pushes to that branch; a fourth head
appeared under a worker mid-amend that way. **Co-author trailers use the
address from the contributor's own commit** (`git log -1 --format='%an <%ae>'
<their sha>`), never `<login>@users.noreply.github.com` — that form links
only for accounts created before mid-2017, and the fixup already on `dev`
for #116 credits nobody because of it.

## Outcomes

- **Fix it yourself** when it is small and you are sure: commit on the
  branch with a message that says what and why.
- **Redispatch** when the theme is incomplete or the approach is wrong: same
  worktree, a prompt that quotes the specific gap. A fragment never becomes a
  PR.
- **Cold read** when the change is risky (below), then triage its findings
  the same way.

## Review the plan, not only the diff

For a theme dispatched as a mission (dispatch.md, "Plan before build"), the
first review is of the worker's plan comment, in the tick it arrives. Check
three things:
- the properties it names against the ticket and the threat model;
- the callers it found against a grep of your own;
- the test list for negative, environment and concurrency cases.

Answer with what is missing, stated as properties. That ten-minute read is
where a cold read's usual findings are cheapest. The cold read of the diff
still follows, and it should then find less.

## The cold read

Dispatch `pyrite-reviewer` when the diff touches **`pyrite/server/`,
`pyrite/storage/`, `pyrite/schema/`, the auth code, or a public shape**, when
it deletes or weakens a test, when the worker's "Unsure" is non-empty, or
whenever your own reading felt too familiar to be critical. Docs and
mechanical fixes do not earn it.

The reviewer gets the diff and nothing else — no ticket, no report, no
conversation. Its value is that it has not been told what to expect.

```
Agent(
  subagent_type="pyrite-reviewer",
  description="cold read: <theme>",
  prompt="Review this change cold. Repository: <main>/../pyrite-wt/<dir> (absolute path),
          branch <branch>, base dev. Run `git diff dev...HEAD`. You have no
          other context on purpose. Report per review.md's finding format."
)
```

Findings come back as: **what breaks** (a concrete input → wrong result),
**what erodes trust** (unclear, untested, surprising), **what is left on the
table** (the ticket's intent the change missed). Each with a file and line.
You decide: fix, redispatch, or note in the PR as a known trade-off. A
finding is evidence to check, not an order to obey — verify it the same way
you verify the worker.

## Open the PR

```bash
git push -u origin <branch>
gh pr create --base dev --title "<type>: <what, in one line a reviewer understands>" --body-file <body>
gh pr merge --auto --rebase
```

Body template:

```
<One paragraph: what a user or operator can now rely on that they could not before.>

<Numbered list, one item per commit or per closed ticket: what and why, one or two lines each. "Fixes #N" on each that closes an issue.>

<Evidence: the full-suite line; anything run beyond the suite (a live server check, an install from tag).>

<Learned: the worker's Learned lines, each with where it is captured.>

<Known trade-offs or "Unsure" items the reviewer should look at, if any.>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

Rebase is the default merge; squash for a branch whose history is noise.

## Shepherd it

- Checks run ~30 s for docs/KB-only, ~3 min for code (one interpreter; the
  full matrix runs on `dev` after the merge).
- `gh pr view N --json mergeStateStatus` → `BEHIND` means another PR merged
  first: `gh pr update-branch N --rebase`. Auto-merge stays armed.
- Merged → `git worktree remove <dir> && git branch -d <branch>` from the main
  checkout, then `git pull --ff-only origin dev` there.
- Red after the merge on the `dev` push (the full matrix found something the
  PR's single interpreter did not) → that is the next theme, before any
  other dispatch.
