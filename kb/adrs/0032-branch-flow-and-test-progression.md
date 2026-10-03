---
id: adr-0032
type: adr
title: "Branch flow and test progression: feature branches, green-and-current merges to dev, layered gates to release"
adr_number: 32
status: accepted
deciders: ["markr"]
date: "2026-09-17"
tags: [process, ci, git, release, testing, multi-agent]
links:
- target: adr-0025
  relation: refines
  kb: pyrite
- target: adr-0019
  relation: related
  kb: pyrite
- target: fast-commit-hooks-full-suite-at-pre-push-ci-is-the-gate
  relation: related
  kb: pyrite
- target: make-the-task-claim-concurrency-test-xdist-safe-then-run-pre-push-with-n-auto
  relation: depends_on
  kb: pyrite
---

> **Open points (2026-10-03).** Decision 3 says merges are by rebase, while a
> note in the same file says the queue is set to squash; CLAUDE.md says rebase.
> The wording is unresolved here, and the live setting is a repository setting
> not checked. Section 3b (snapshot `.dev0+sha` versions) is marked proposed
> inside this accepted ADR and `scripts/release.py` does the opposite; 3b is
> open, not accepted. Evidence: `kb/designs/adr-audit-2026-10.md`.

# ADR-0032: Branch flow and test progression

Amends ADR-0025. ADR-0025's branch roles stand (`dev` integrates, `main`
releases, tags ship). What changes is **how work reaches `dev`** and **what
each step has to prove**.

## Context

ADR-0025 says "all work happens on `dev`". That was right for one person and one
session. It is no longer the situation:

- **Several agent sessions share one working tree on one branch.** CLAUDE.md's
  "Multi-Session Git Hazard" section is a list of disciplines that exist only
  because of this. On 2026-09-17 alone: two sessions interleaved commits on
  `dev`; a commit hook stashed another session's edits and a crash stranded
  them in `~/.cache/pre-commit/`; a test run under one session's hook replaced
  the shared `.git/index`, so every tracked file showed as a staged deletion
  for every session.
- **There is an outside contributor and a fork.** Their PRs target `dev`, which
  has no protection at all: nothing requires a PR to be green, and nothing stops
  it being merged onto a `dev` that is already broken.
- **`dev` was red for a long stretch** (100 of 100 runs failed before
  `ci-make-green-and-load-bearing`). A permanently red integration branch trains
  people and agents to ignore the signal, and every branch cut from it inherits
  the breakage.
- **The one gate that ran everything ran at the wrong place.** The full suite
  ran on every commit (8 minutes, with the side effects above) and the same
  suite ran again in CI. Cost was highest where iteration should be cheapest.

ADR-0019 names the constraint: human review attention. A process that depends on
every session remembering a list of git disciplines spends that attention on
something a branch rule can enforce for free.

## Decision

### 1. Work happens on feature branches; `dev` only receives green, current work

- Every batch of work — a human's, an agent session's, a contributor's — lives
  on its own branch (`feature/*`, `fix/*`, `kb/*`). Commit there at whatever
  pace the work needs. Nothing on a feature branch has to be green.
- A branch reaches `dev` **only through a pull request whose required checks
  pass on top of current `dev`** (GitHub: required status checks + "require
  branches to be up to date"). GitHub has no "base must be green" rule; this is
  the mechanism that delivers it. If `dev` is broken, the PR's checks fail on
  top of it and nothing merges until `dev` is fixed. Work cannot land on a
  broken base.
- **No bypass for the maintainer.** A rule that exempts the account that makes
  most of the pushes protects against the rarest source of breakage. Break-glass
  is disabling the ruleset deliberately, which leaves an audit entry.
- Merge by **squash or rebase only**, so `dev` stays linear and `main` can always
  fast-forward (§3). Merge commits are disabled.
- One session, one branch, **one checkout**. Two sessions cannot hold different
  branches in one working tree, so concurrent sessions each get their own
  `git worktree` (or clone). This retires the shared-tree hazards rather than
  managing them. It does *not* reverse the CLAUDE.md rule against
  `isolation: "worktree"` for parallel sub-agents inside one wave: those share a
  feature branch and a file-footprint plan, and their merge cost was the
  problem. The unit of isolation is the branch, not the agent.

### 2. Yes — force up to date, and pay for it by making CI fast

"Up to date" is what makes the guarantee real: two branches that are each green
can be red together, and agent sessions working in parallel on one codebase are
exactly where that happens. Without it, `dev` goes red with nobody's PR to blame.

The cost is serialization: every merge to `dev` makes every other open PR stale,
and each must update and re-run. GitHub's merge queue would absorb this, but it
is not available to repositories owned by a user account. So the cost is
`open branches × CI duration`, and **CI duration is the variable we control**:

- Python CI takes ~22 minutes today (serial pytest with coverage). The same
  suite takes 2m19s locally under `pytest -n auto`. At 22 minutes, strict mode is
  unworkable with more than two branches in flight. At ~5 it is a non-issue.
- Therefore **parallel CI is a prerequisite of this ADR, not a follow-up**:
  `make-the-task-claim-concurrency-test-xdist-safe-...` lands first.
- KB-only and docs-only changes must not wait for the Python suite. A required
  check cannot simply be path-filtered away (GitHub leaves it "pending" forever),
  so the workflow always triggers, a first job classifies the change, and the
  heavy jobs *skip* — a skipped job satisfies a required check. A ticket-filing
  PR goes green in about a minute, and auto-merge lands it unattended.

### 3. A progression: cheap and fast first, more layers toward release

Each layer runs only what is worth its cost at that distance from a user, and no
layer repeats work a cheaper layer already did.

| # | Where | Runs | Budget | Blocks |
|---|-------|------|--------|--------|
| 0 | every commit (local hook) | ruff, format, file hygiene, import cycles, KB schema | seconds | the commit |
| 1 | feature-branch iteration (local, on demand) | the tests for what you touched; `pytest -n auto` when you want the whole picture | seconds – 2 min | nothing; this is for the author |
| 2 | **PR → `dev`** (CI, required) | full Python suite incl. extensions on 3.11 / 3.12 / 3.13 with Postgres; frontend type-check, unit tests, build; lint; on top of current `dev` | ≤ ~5 min wall-clock once parallel | **the merge** |
| 3 | `dev` after merge (CI, not a merge gate) | layer 2 again on the merged result, plus Playwright e2e, coverage, mypy ratchet | ≤ ~10 min | the *release*; a red `dev` also blocks every PR via §1 |
| 4 | **`dev` → `main`** (release) | everything in 2–3 green on the exact SHA; e2e required (once deterministic); live-server integration flows; the getting-started tutorial run; install-from-tag in a clean venv | minutes, once per release | **the tag** |

- **Python tests are required checks on both `dev` and `main`** — all three
  interpreter versions, since they run in parallel and cost no extra wall-clock —
  plus `frontend`. `e2e` joins the required set on `main` when
  `playwright-e2e-suite-non-deterministic-failures-...` lands; requiring a
  non-deterministic check would only teach everyone to re-run until green.
- `main` moves **only by fast-forward to a SHA that passed layer 3**, and the tag
  points at that SHA (release runbook, rewritten 2026-09-17). Required linear
  history and `enforce_admins` on `main` make that a rule rather than a habit.
  (**Amended 2026-09-22** — `main` still moves only by fast-forward to a
  layer-3 SHA, but it moves *more often* than a release; see 3b.)
- `v*` tags are protected from update and deletion.
- The local pre-push hook stays on and runs the full suite (**amended in
  migration, 2026-09-17:** it was written as opt-in when the suite took 8 min;
  at ~1 min it is cheaper than a failed CI run, so it remains the default).
  Pushing to `dev` directly is no longer possible. Commit hooks stay.

### 3a. The value chain (amended 2026-09-18)

Sharper rule for the table above, from running it for a day: **each step may
cost more than the one before it only if it adds breadth or depth the previous
step could not**, and the value being bought at every step is the same thing —
not breaking for users. A step that repeats what a cheaper step already proved
is waste; a step that could tell us something new and does not is a gap.

| Step | Costs | Buys (new information) |
|---|---|---|
| commit hook | seconds | the change is well-formed (lint, format, schema) |
| local pre-push | ~1 min | the change passes the suite on *my* interpreter |
| **PR → `dev`** (required check: `gate`, which needs `test (3.12)`, `frontend`, `kb`; skipped = pass) | ~3 min | the change passes on top of *current `dev`*, on the primary interpreter |
| **push to `dev`** (after merge) | ~5 min, not a merge gate | **breadth**: every supported Python, Postgres conformance; the assembled thing works — a real server process driven over REST, MCP/SSE and MCP/stdio, and the getting-started tutorial run as written (`smoke` job); a red `dev` blocks every PR |
| **push to `main`** | minutes | **depth in a browser**: Playwright e2e against a seeded world (deterministic since package A) |
| **release** (tag) | minutes, once per release | **what a user gets**: install from the tag in a clean venv, the Docker image, the published artifacts |
| pre-release, by hand or script | minutes | the release notes are true; the UI works in a browser; the runbook's clean-venv check |

Consequences: the Python matrix runs one interpreter on pull requests and all
three on pushes (`ci.yml`). The single required check on `dev` and `main` is
`gate`, a job that needs every gating job and fails only on a failure or
cancellation — a skipped job (the classifier's "nothing to test here") passes.
Requiring matrix legs by name hung docs-only PRs, because a skipped matrix
reports as `test`, not `test (3.12)`. Coverage and e2e are
manual until each has something to say.

The **smoke** row was empty until 2026-09-18: ~4000 tests proved the code on
three interpreters and nothing proved the assembled artifact worked at all.
All three bugs the outside contributor found (PRs #3, #4, #5) lived in that
gap, and each is structurally invisible to an in-process test — each is a
property of one long-lived process serving several requests, or of a console
script started as a subprocess. `tests/e2e` (marker `e2e`, excluded from the
default run) and `scripts/run_tutorial.sh` fill it, in a `smoke` job gated on
the push to `dev`. It is deliberately **not** in `gate`'s needs: a minutes-long
job must never block a pull request, and a break there blocks the release while
a red `dev` already blocks every PR through the up-to-date rule.

**Not every merge to `main` is a release** (maintainer's decision, 2026-09-18).
`main` moving and a tag being cut are separate events, so the row that says
what a *user gets* — install from the tag, the Docker image — belongs to the
release, not to the push that preceded it.

### 3b. Snapshot versions on `main` between releases (amended 2026-09-22)

The row above already says not every merge to `main` is a release. This makes
that useful: **`main` moves forward as work lands, and between releases it
carries a PEP 440 development version naming the commit it was built from.**

Immediately after a release is cut, the release script bumps `dev` to the next
version's snapshot form:

```
0.25.0                      the release, a real semver version
0.26.0.dev0+a34ab48         what dev and main carry until 0.26.0 is cut
0.26.0                      the next release
```

**PEP 440, not semver-with-a-suffix.** The first spelling tried was
`0.26.0-pre-release-snapshot-<sha>`, which `packaging.version.Version()`
rejects outright — `pip install` fails and no wheel builds. `.dev0+<sha>` is
the valid form with the same meaning, and its ordering is what the scheme
needs:

```
0.25.0 < 0.26.0.dev0+a34ab48 < 0.26.0.dev0+f1e2d3c < 0.26.0
```

The `+<sha>` is a *local version identifier*. It identifies the build, and it
does participate in ordering — PEP 440 compares local segments, so two
snapshots of the same target version sort by their SHA rather than by which
was built first. That ordering is meaningless and should not be relied on:
the only comparisons that matter here are `release < snapshot < next release`,
and those hold. Released versions remain plain semver, so anyone reading a tag
sees an ordinary version number.

**`pip install pyrite` still resolves to the last release.** Development
versions are pre-releases under PEP 440, and pip skips those unless asked, so
a user has to opt in with `--pre` or by installing from `main` directly. The
snapshot is available to whoever wants it and invisible to everyone else.

Three things this buys:

1. **Users can run from `main` and get a version string that identifies the
   commit**, rather than a stale release number that lies about what is
   installed.
2. **Merging back to `main` becomes continuous** rather than a release-day
   event, so `main` never sits far behind `dev` and the fast-forward in step
   (d) is never a surprise.
3. **The release machinery gets exercised on every merge to `main`, not once
   a release.** This is the real reason. A release path used weekly is a path
   whose breakage is discovered at the worst moment — the 0.24.2 cut found
   `scripts/release.py` hardcoding `markramm/pyrite` (#259) only because
   someone tried to release from the new org. Running the same code on every
   merge means the next such defect surfaces on an ordinary Tuesday.

What `main` means therefore changes from *"the last release"* to *"the last
commit that passed layer 3"*. The fast-forward-only rule, the required checks
and the tag protections are unchanged; only the frequency changes.

### 4. What does not change

`dev` is still the default branch, the integration branch, and what
demo.pyrite.wiki tracks. Tagged releases still drive the production sites.
Version numbers still follow roadmap milestones.

(**Amended 2026-09-22:** "`main` is still releases only" no longer holds —
see 3b. Everything else in this section stands.)

## Consequences

**Easier**
- The multi-session hazards in CLAUDE.md stop being possible instead of being
  remembered: no shared index, no stash of someone else's edits, no interleaved
  commits.
- `dev` being green becomes an invariant CI enforces. A branch cut from `dev`
  starts from something that works.
- Contributors and the maintainer follow the same path, so CONTRIBUTING describes
  what actually happens.
- A release is a fast-forward and a tag of something already proven.
- Every batch of agent work arrives as a PR: a diff, a CI verdict and a
  description in one reviewable unit — which is the review surface ADR-0019 and
  the ADR-0031 response argue for, provided by GitHub for free in the meantime.

**Harder**
- Small changes cost a PR. Mitigation: the docs/KB fast path and auto-merge; an
  agent opens the PR and sets auto-merge in one step.
- Parallel branches serialize at the merge. Bounded by CI duration (§2); if it
  becomes the constraint, batch related branches or move the repo to an
  organization to get a merge queue.
- Each concurrent session needs its own worktree and its own `.venv` (or a shared
  venv with the package installed per worktree). Setup must be scripted.
- KB edits made through a running Pyrite instance (the web UI, MCP writes) commit
  to whatever branch that checkout is on; the instance used for daily KB work
  should sit on its own `kb/*` branch and merge like anything else.
- The backlog becomes slightly less live: a ticket filed on a branch is not on
  `dev` until its PR merges. The fast path keeps that to about a minute.

## Migration

Status 2026-09-17: steps 1–4 done (parallel CI, classifier, rulesets on `dev`,
`main` and `v*` tags with no bypass, auto-merge and branch auto-delete on,
`scripts/new-worktree.sh`, CLAUDE.md/skill/CONTRIBUTING rewritten). Step 5
amended: pre-push stays on.

1. Make CI fast: xdist-safe concurrency test, `-n auto` in CI, the change
   classifier with skip-on-docs. **Gate for everything below.**
2. Apply protection: `dev` ruleset (required checks, up to date, no force-push,
   no deletion, squash/rebase only, no bypass); tighten `main` (all Python jobs +
   `frontend`, linear history, enforce for admins); protect `v*` tags; enable
   auto-merge and delete-branch-on-merge.
3. Script the session setup: `scripts/new-worktree.sh <branch>` creating the
   worktree, venv and hooks.
4. Rewrite CLAUDE.md "Git Workflow", "Parallel Agents" and "Multi-Session Git
   Hazard", the pyrite-dev skill, CONTRIBUTING and ADR-0025's status line.
5. Make pre-push opt-in; update `tests/test_dev_process_config.py` to pin the new
   layout, including the required-check names so a renamed CI job cannot silently
   unprotect a branch.

## Decisions on the open questions (2026-09-17, markr)

1. **Release first, then migrate.** v0.24.1 is cut from current `dev` using the
   fast-forward runbook (tag a SHA CI already passed; `main` only fast-forwards)
   with as much of layer 4 as can be run by hand. Branch protection and the
   feature-branch flow switch on afterwards, once CI is parallel.
2. **Governance: BDFL.** Mark Ramm is the sole maintainer and has final say on
   what merges and what ships. The required checks are the reviewer of record for
   the maintainer's own PRs; PRs from anyone else need the maintainer's approval.
   Co-maintainers are welcome, by invitation, once there is someone who wants the
   responsibility and has earned the trust; this ADR's rules apply to them
   unchanged, which is part of the point of having no maintainer bypass.
3. **Rebase is the default merge method.** It keeps each commit's message and
   authorship (and lets the `fix:`-needs-a-test rule keep checking commits), and
   keeps `dev` linear without merge commits. Squash stays available for a branch
   whose history is noise. Merge commits are disabled.

---

## Open questions on 3b (proposed 2026-09-22, not yet decided)

The snapshot-version amendment above is `proposed`. Three things need the
maintainer's answer before it is implemented:

1. **Who bumps to the snapshot, and when.** The natural place is step (e) of
   `scripts/release.py`, which already commits to `dev` after a release to
   consume the fragments and reopen `[Unreleased]`. Adding the version bump
   there makes it one commit and one decision. The alternative — a separate
   manual step — is more visible and more forgettable.

2. **Does the SHA update on every merge, or once per release?** Updating it on
   every merge to `main` makes the version string always true, at the cost of a
   commit per merge that exists only to change a version. Setting it once after
   the release means the string names the commit the *cycle* started from, not
   the one installed. The first is more useful and noisier; the second is
   cheaper and slightly dishonest.

3. **Does `main` still need a tag per move?** Today `v*` tags and `main` moves
   coincide. Under 3b they do not, so either `main` moves untagged between
   releases (simplest, and the tags keep meaning "release"), or every move gets
   a snapshot tag (more traceable, many more tags).

A fourth thing, noticed while writing this: **item 3 under "Decisions on the
open questions" above says rebase is the default merge method. As of
2026-09-21 the merge queue is set to squash**, because outside PRs kept
arriving with a merge commit from `origin/dev` and a rebase-merge queue
ejects those without running anything. Three contributor PRs needed a
maintainer-side rebase in one day; after the change, zero. That decision
should be written into this ADR properly rather than left as a repo setting
nobody reading here would know about.
