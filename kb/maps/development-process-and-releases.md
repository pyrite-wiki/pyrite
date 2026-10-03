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
[[design]] (P8, P9). The operating rules are in `CLAUDE.md` and
`.claude/skills/pyrite-conductor/release-runbook.md`; this map says which ADR
each rests on.

## How the work is run: the agent loop

Pyrite is built by agents under one maintainer. The rules live in the skills
under `.claude/skills/` and `.claude/agents/`, which `pyrite search` does not
index; these are the ones a contributor or an agent asks about, and where each
is defined.

- **The Andon cord.** Anyone (the maintainer, the conductor, a worker, a
  reviewer, the architect, a contributor) can stop the loop when work churns
  instead of progressing: two themes fail review the same way, a theme passes
  twice its groomed size, a decision is being re-decided, a fix round would
  repeat the last one, or the work runs against [[design]]. Pull it by opening
  an issue labelled `andon` (security findings on the private repository).
  While one is open nothing is dispatched and no fix round is sent; the root
  cause is found and a directed fix made, then the issue is closed and the loop
  resumes. Defined in `.claude/skills/pyrite-conductor/SKILL.md`, "The Andon
  cord". (Named for the Toyota Production System's cord; see the `tps` demo
  KB.)
- **The circuit breaker.** A theme that reaches its third worker fix round, two
  red pushes to `dev`, or a reverted PR stops the loop and goes to the
  maintainer. Same skill, "Stop conditions".
- **The groom.** Before work is dispatched the architect reads [[design]],
  names the principle at stake, challenges the ticket's framing, states the
  user's model and the implementation model, and raises a request that breaks
  the architecture while the user's problem is real to the maintainer. A bug
  report stays a bug report. `.claude/agents/pyrite-architect.md`.
- **The cold read.** A reviewer that sees only the diff checks the claimed
  property everywhere it must hold. `.claude/agents/pyrite-reviewer.md`.
- **A fix round states the property, not the instances.** The conductor skill.
- **Risk spikes.** Throwaway code to learn before building, used when the scope
  or approach is uncertain. `.claude/agents/pyrite-spike.md`.

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
   and 3a.
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
    `CLAUDE.md`.
11. A reviewed-changes flow for users mirrors this one. **proposed** ADR-0044.

## Invariants a test could check

- The required checks (`gate` and the jobs it needs) keep their names;
  a renamed job cannot silently unprotect a branch ([[adr-0032]] migration step
  5; `tests/test_dev_process_config.py`).
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
`scripts/test-affected`, `scripts/check_fix_commit_has_tests.py`,
`changelog.d/`, `.claude/skills/pyrite-conductor/release-runbook.md`.

## Tests that pin it

`tests/test_dev_process_config.py` (hook layout and required-check names),
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
- No ADR covers the conductor, worker and reviewer roles; they are skills under
  `.claude/`.
