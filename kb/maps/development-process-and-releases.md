---
id: map-development-process-and-releases
title: "How does a change reach dev and main, and where do I file it? Topic map: the development process and releases"
type: note
tags:
- map
- design
- process
- git
- ci
- release
- deployment
- branch-flow
- tracking
---

# How does a change reach dev and main, and where do I file it?

Answers "how does a change reach `dev` and `main`?", "where do I file a bug, a
request, a process finding?", "what does a release require?". Layer 2 under
[[design]] (P8, P9). The operating rules are in `CONTRIBUTING.md` (the process) and
`.claude/skills/pyrite-dev/` (the method); this map says which ADR each rests
on. The release runbook is the maintainer's and lives outside this repository.

## How the work is run: the agent loop

Pyrite is built by agents under one maintainer. A contributor's agent loads one
skill from this repository, `pyrite-dev` (`.claude/skills/pyrite-dev/`), and the
process facts in [CONTRIBUTING.md](../../CONTRIBUTING.md); `pyrite search` does
not index the skill, so the table below says where each rule is. The
maintainer's orchestration loop (the conductor that composes themes, dispatches
workers and reviews branches, and its retro) runs outside this repository and
is not part of the contributor surface. What it means for a contributor:

- **The Andon cord.** Anyone can stop the work when it churns instead of
  progressing: two themes fail review the same way, a decision is being
  re-decided, a fix round would repeat the last one, or the work runs against
  [[design]]. A contributor pulls it by commenting on the ticket with the
  evidence and stopping (`pyrite-dev`, "Debugging"); security findings go to
  the maintainer privately. (Named for the Toyota Production System's cord; see
  the `tps` demo KB.)
- **The groom.** A ticket's `## Groom` names the principle at stake, the
  contracts that apply and a predicted footprint. Read it, and test its
  riskiest assumption first (`pyrite-dev`, "Before you build").
- **The cold read.** A reviewer that sees only the diff checks the claimed
  property everywhere it must hold; a worker's report lists each instance it
  covers (`pyrite-dev`, "Finishing: the report").
- **A fix round states the property, not the instances.**

## Find an agent process rule

This map is the searchable index of record. Each rule lives in its linked skill
section; follow the pointer for the current instruction. The table contains
search vocabulary, not a second copy of the rules.

| Search question or terms | Rule home |
| --- | --- |
| verify-red regression evidence | [pyrite-dev: Verification](../../.claude/skills/pyrite-dev/SKILL.md#verification) |
| test driven development red green | [pyrite-dev: Test-driven development](../../.claude/skills/pyrite-dev/SKILL.md#test-driven-development) |
| claim scope before building | [pyrite-dev: Before you build](../../.claude/skills/pyrite-dev/SKILL.md#before-you-build) |
| finished worker report testing limits | [pyrite-dev: Finishing: the report](../../.claude/skills/pyrite-dev/SKILL.md#finishing-the-report) |
| bookkeeping feedback | [pyrite-dev: Capture what you learned](../../.claude/skills/pyrite-dev/SKILL.md#capture-what-you-learned) |

## The design today

1. `dev` is the integration branch and default; `main` moves only by
   fast-forward to a commit CI already verified; tags ship. **decided**
   [[adr-0025]], [[adr-0032]].
2. Every batch of work lives on its own `feature/*`, `fix/*` or `kb/*` branch in
   its own worktree. **decided** [[adr-0032]] section 1.
3. A branch reaches `dev` only through a pull request whose required checks pass
   on top of current `dev`; no bypass for the maintainer; merge by rebase or
   squash, never a merge commit. **decided** [[adr-0032]] sections 1 and 2.
4. Layers by cost: commit hooks, local pre-push, PR check (one interpreter),
   push-to-`dev` smoke and matrix, `main` e2e, release install checks. Each layer
   adds information the cheaper one cannot. **decided** [[adr-0032]] sections 3
   and 3a. A layer does not repeat a dearer one's answer: a commit is
   *verified* when `gate` is green on that exact SHA on GitHub, and the
   pre-push hook skips a verified tip. One definition,
   `scripts/verified_commit.py`, read by the hook and by `scripts/release.py`.
5. `main` may move without a release; a tag is a release. **decided**
   [[adr-0032]] section 3a. Snapshot (`.dev0+sha`) versions between releases:
   **proposed** inside the accepted ADR (3b) and not what `scripts/release.py`
   does.
6. GitHub issues hold bugs and requests; the KB holds the roadmap, epics, ADRs
   and designs; one item has one home; security reports are private; process
   findings are KB backlog items tagged `process`. **decided** [[adr-0033]].
7. Governance is BDFL: the maintainer decides what merges and ships; required
   checks are the reviewer of record for the maintainer's own PRs. **decided**
   [[adr-0032]] decisions 2026-09-17.
8. Deployment tiers: development tracks `dev`; stable deployments pin a release
   tag; installs are from source or a git tag, because the PyPI name is not
   reachable (as amended 2026-09-17). **decided** [[adr-0025]].
9. The release line (the roadmap's section of that name) says what each release
   promises and gates on; plugin and API contracts are alpha and multi-user is
   experimental. `kb/roadmap.md`, [[design]].
10. A pull request is a unit a reviewer can hold: complete on one theme, never a
    fragment. Review attention is the constraint. [[adr-0019]], [[adr-0032]];
    `CONTRIBUTING.md`.
11. A reviewed-changes flow for users mirrors this one. **proposed** ADR-0044.

## Invariants a test could check

- The required checks (`gate` and the jobs it needs) keep their names;
  a renamed job cannot silently unprotect a branch ([[adr-0032]] migration step
  5; `tests/test_dev_process_config.py`).
- Whether a commit is verified is decided in one place, and it fails closed:
  a lookup that fails, or answers ambiguously, is "not verified" for the hook
  and the release alike (`tests/test_verified_commit.py` and the two files
  that run its case list through each caller).
- A release dry run that reports ready means `--execute` does not stop on the
  checkout, the changelog date, the branch, the commit's verdict or the
  pre-push hook (`tests/test_release_rehearsal.py`).
- `main` moves only by fast-forward to a commit that passed the push-to-`dev`
  layer; `v*` tags cannot be updated or deleted ([[adr-0032]]).
- A `fix:` commit touches `tests/` (commit-msg hook, [[adr-0032]]).
- A bug is tracked in one place; a fixing PR says `Fixes #N` ([[adr-0033]]).
- The README's install and Quick Start run as written in a clean environment
  ([[adr-0032]] release layer; [[design]] P9).

## ADRs in reading order

[[adr-0032]] first (read its audit standing: detail stale), [[adr-0033]],
[[adr-0025]] (the branch roles and deployment tiers; amended by 0032), then the
roadmap's release line. History only: [[adr-0025]]'s migration list.

## Where the code starts

No component doc covers the process. Paths: `.github/workflows/`,
`.pre-commit-config.yaml`, `scripts/new-worktree.sh`, `scripts/release.py`,
`scripts/test-affected`, `scripts/verified_commit.py`,
`scripts/check_fix_commit_has_tests.py`,
`changelog.d/`.

## Tests that pin it

`tests/test_dev_process_config.py` (hook layout and required-check names),
`tests/test_verified_commit.py`, `tests/test_test_affected_verified.py` and
`tests/test_release_rehearsal.py` (the verified-commit rule at its three
surfaces; the lookup's failure modes are one list, `tests/verified_commit_cases.py`),
`tests/test_task_claim_concurrency.py` (the xdist-safe pattern the pre-push
suite needs), `tests/test_readme_install_tag.py`, `tests/test_readme.py`.

## Known gaps

- [[adr-0032]] decision 3 says rebase is the default merge method; its own
  2026-09-21 note says the queue squashes; section 3b contradicts the release
  script. The live repository settings were not verified ([[adr-audit-2026-10]]).
- [[adr-0025]] still says "no PR reviews" and "all work happens on `dev`" in
  its body, with a banner pointing to [[adr-0032]].
- The standard `git-workflow` still says "All work happens on `dev`" and lists
  `feature/*` as for large multi-day changes: it contradicts [[adr-0032]] and
  is not marked superseded (verified by reading it; not edited here).
- Not decided: whether `main` moves untagged between releases, and who bumps
  the snapshot version ([[adr-0032]] open questions on 3b).
- No ADR covers the worker and reviewer roles; the worker method is the
  `pyrite-dev` skill, and the maintainer's loop is outside this repository.
