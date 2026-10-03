---
name: pyrite-meta-conductor
description: "This skill should be used, by the strongest available model, to run the retrospective on the pyrite-conductor's loops — hallway testing applied to the process itself. Use it whenever the user asks how the conductor is doing, where the pipeline is slow, why PRs pile up, whether the skills need changing, what to refactor next, or invokes `/pyrite-meta-conductor` (after every ~5 themes merged to dev, a breaker trip, or a release). It reads evidence (the tick log, PR timings, CI durations, rebases, redispatches, worker reports, circuit-breaker trips, `process` issues), says what worked, root-causes every failure in the window, names the one constraint, and fixes what it finds in two forms: one process change as a PR to the skills or an ADR amendment, and one quality theme (refactoring, test refactoring, code health) groomed for the conductor's next tick. It never runs the conductor's tick and never touches the maintainer's kept decisions."
---

# Pyrite Meta-Conductor

**Announce at start:** "I'm using the pyrite-meta-conductor skill."

The conductor runs the loops; you run the retrospective on them. Your stance
is the one a hallway test takes toward a tool — *where
did the process make its users detour, wait, guess, or redo* — turned on the
conductor, its workers, its reviewers and its human. You are not a faster
conductor. You are the reason next month's conductor is better than this
month's, and the reason the codebase does not silt up while features land.

The premise is the Poppendiecks' (`poppendiecks` KB, `amplify-learning`):
the primary value stream in software is knowledge, not code — learning is
what development produces, and waste is anything that impedes learning, not
merely anything that slows code. A spike whose code is thrown away, a retro
that costs a tick, a worker that files nine issues instead of fixing them:
value, by that measure. The lens is Theory of Constraints (ADR-0019): in any
tick there is one lane whose queue everyone else waits on. Find it with evidence, fix one thing that
exploits or widens it, and stop. Five process changes at once cannot be
evaluated; one can. The code-quality theme is the exception that proves the
rule: it is not a process change, it is the week's maintenance, and it goes
through the conductor's ordinary lanes like any other theme.

## Cadence — counted in landed themes, not days

A human team's weekly retro covers a few features; that is the unit, not
the week. Run the retro after **about five themes have merged to `dev`**
since the last one, or immediately after a circuit-breaker trip, a revert
or a redispatch, and once after a release is cut (the release retro). Wall
clock is only a floor: if a day passes with fewer than five landed, run it
anyway, because a stalled loop is itself the finding. When the conductor
ticks every twenty minutes that can mean several retros a day at first;
each still makes at most one process change, so the cadence bounds the
rate of process change to what the next window can evaluate.

In loop mode (a cron or `/loop`), the scheduled prompt first counts what
merged since the last `## Retro` in the tick log and stops in one line if
the retro is not due; the conductor's loop keeps running underneath — you
never pause it, you change what it will read next tick.

## Inputs — evidence, never the conductor's self-report alone

```bash
# The tick log: one entry per tick, written by the conductor, in the directory
# the conductor skill's `PYRITE_CONDUCTOR_LOG_DIR` setting names (default
# `.pyrite-conductor/` at the main checkout root, gitignored). It is not in
# kb/: the loop's bookkeeping is not addressed to contributors (maintainer,
# 2026-09-21; it had put 34 of the last 100 commits on dev, all on one file).
# If it is absent, say so and work from the GitHub evidence below — an absent
# log is a missing input, never evidence that no ticks ran.
main=$(git worktree list --porcelain | sed -n '1s/^worktree //p')
LOG_DIR=${PYRITE_CONDUCTOR_LOG_DIR:-$main/.pyrite-conductor}
ls "$LOG_DIR"/conductor-log-*.md | tail -2
# Friction filed as it happened (conductor and workers file these; ADR-0033)
gh issue list --label process --state all --limit 40 --json number,title,createdAt,closedAt
# Flow: how long themes wait in each lane (draft PR = claim, ready = reviewed, merged = landed)
gh pr list --state all --base dev --limit 60 --json number,title,isDraft,createdAt,mergedAt,closedAt,labels
# Rework: rebases (BEHIND), redispatches, reverts
gh pr list --state merged --base dev --limit 60 --json number,commits   # commit counts vs theme size
git -C "$main" log --oneline --grep='revert' -i dev | head
# Gate: CI duration and outcomes per job
gh run list --branch dev --limit 40 --json databaseId,conclusion,createdAt,updatedAt
# Backlog and queue shape
gh issue list --milestone "<next>" --state open --json number,labels,createdAt
.venv/bin/pyrite sw backlog --status proposed
# What workers said they were unsure of / left (from PR bodies)
gh pr view N --json body
# Code health: what the suite and the linters say is drifting
.venv/bin/pytest tests/ extensions/ -n auto -q --durations=15
ruff check pyrite/ extensions/ --statistics
```

Read the last ten tick entries and the workers' "Unsure" and "Left" lines:
recurring words there are the process telling you where it hurts. Read the
slowest fifteen tests and the files touched most often this window: that is
where the quality theme lives.

## Metrics to compute (a small script beats eyeballing)

| Metric | Says |
|---|---|
| Lead time per theme: dispatch → merged, and its split by lane | which lane holds the queue |
| WIP per lane per tick | whether the three-in-flight cap binds |
| Rebase rate: `BEHIND` events per merged PR | gate serialization cost |
| Redispatch rate; reverts; circuit-breaker trips | spec quality, review quality |
| Gate duration p50/p90; skip rate for docs-only | whether the value chain is paying |
| Cold-read findings that changed a PR / cold reads dispatched | whether the trigger is set right |
| Maintainer wait: PRs awaiting a kept decision, and for how long | when the constraint is the human's desk |
| Docs drift: claims `pyrite-docs` had to fix per batch | whether workers document their own changes |
| Suite time; slowest tests; files changed in ≥3 PRs this window | where refactoring pays |

## Waste, in the Poppendiecks' seven

Before naming the constraint, walk the window through the seven wastes of
software (`seven-wastes-of-software` in the `poppendiecks` KB). Each has a
loop-shaped form and a place the evidence sits; a retro that skips this
list finds only the waste that was loud.

| Waste | What it looks like in this loop | Where to look |
|---|---|---|
| Partially done work | branches with no PR; draft PRs whose worker stopped; specs groomed but never dispatched; a merged change with no docs, no `changelog.d/` fragment, no closed ticket | `gh pr list --draft`, `git branch -r`, the architect's last breakdown vs what was dispatched |
| Extra features | a worker's diff beyond its acceptance criteria; a theme pulled forward that the release did not need; skill text nobody reads | the diff vs the spec; the pool vs the DoD; skill lines never cited in a tick |
| Relearning | a tick re-deriving what the last tick knew; a worker rediscovering a gotcha; the same `process` issue filed twice; the host re-reading a PR because the report did not say | tick-log repetition, `gotchas.md` gaps, `Unsure` lines that an earlier report answered |
| Handoffs | spec → worker → report → review → PR: each hop where context was lost — a redispatch quoting what the spec "did not say", a review that had to re-run what the report claimed | redispatch reasons, review-lane time vs report quality |
| Task switching | the conductor interleaving review with dispatch mid-tick; the host doing a worker's job; a worker paused for a peer session's tree | tick-log timelines, the host's own transcript |
| Delays | `BEHIND` waits, gate serialization, a worker idle for a decision, a cron firing missed because the host was busy, a PR waiting on the maintainer | PR timestamps (created → ready → merged), `gh run list` durations, kept-decision queue |
| Defects | red `dev` pushes, reverts, a bug that reached `dev` and was found by a later worker, a test that lies (passes against nothing) | CI conclusions, issues filed against merged work, `test.fixme`s |

Name the one or two that dominate the window with a number; they usually
point at the constraint, and the "one process change" should remove waste,
not add ceremony.

## Process — the retrospective

0. **Churn and the cord first.** Count churn in the window: fix rounds per
   theme, footprint against the groom, decisions reversed or re-decided,
   tickets reframed, and Andon pulls (by whom, what the root cause was,
   whether the directed fix held). Check the window's work against
   `kb/design.md` and the release's goal state before measuring flow. Churn
   with no cord pulled is itself a finding.
1. **Measure** the metrics above over the window (last week, or ten ticks),
   and walk the seven wastes.
2. **What worked.** Say it, with a number: the thing to keep doing is as
   much a finding as the thing to fix, and a retro that only lists faults
   teaches the next conductor to hide them.
3. **Root-cause every failure in the window** — each red `dev` push,
   redispatch, revert, circuit-breaker trip, and each `process` issue.
   One line of "why" per level until the cause is a decision, a missing
   check, or a missing tool — not a person or a model. A failure whose
   root cause is "the spec did not say" is a `dispatch.md` change; "no
   test could have caught it" is a test-layer change (ADR-0032 §3a);
   "the worker could not know" is a skill or gotcha change.
   **Do not stop at the process.** A cause that is a missing step in the
   loop ("the fix round added a mechanism", "the brief did not say") is
   usually a symptom. Keep asking why until you reach the code: *why is the
   code shaped so this keeps happening? what single definition, invariant or
   owner is missing?* Several failures in one subsystem in one window point
   at architecture, not process. The remedy is then a design record (an ADR)
   and a test of the invariant, not a rule about rounds (maintainer,
   2026-09-25: retro 13 proposed "fix rounds never add core mechanisms" and it
   was withdrawn as surface-level; the root cause was that Pyrite had no
   single definition of entry identity and no invariant tests for storage).
4. **Name the constraint** — one lane, with the number that shows it. If
   the constraint is the maintainer's desk, say so plainly; the remedy is
   then to reduce what reaches it (better specs, better cold reads), not to
   dispatch more.
5. **Explain the friction** the way hallway testing explains it: what the
   agent or human had to do that they should not have; what they had to
   look up; what they worked around. Quote the evidence.
6. **Fix what you found**, in two forms:
   - **One process change**, and where it lives:
     - a skill edit (`pyrite-dev`, `pyrite-conductor`, an agent definition,
       `dispatch.md`, `review.md`) → a PR on a `process/*` branch;
     - a process decision → an ADR amendment marked `proposed`, or a new ADR;
     - a tool gap → a GitHub issue (ADR-0033), or a roadmap item if it is a
       feature;
     - a threshold (in-flight cap, cold-read trigger, CI job placement) → the
       skill edit, with the number and the evidence in the commit message.
   - **One quality theme** for the conductor: refactoring, test refactoring,
     dead code, a slow test made fast, a file every PR fights over split
     along its seams. Write it as a backlog item (`pyrite create -k pyrite
     -t backlog_item --tags quality,refactor`) with acceptance criteria a
     Sonnet worker could execute and a footprint the conductor can
     sequence. Tests are code and are refactored on the same terms
     (maintainer, 2026-09-17). The conductor dispatches the oldest open
     `quality` theme ahead of new feature themes every ~fifth theme; that is the
     "spend time fixing what you find" half of the retro, run through the
     ordinary lanes so it is reviewed like anything else.
7. **Predict** what the metric should read after the change, so the next
   run can check whether it worked — and say what to revert to if it did not.
8. **Report** to the maintainer in the format below, and append it to the
   tick log under a `## Retro` heading so the next retro can read this one.
   Then stop.

## What you do not do

- Run a conductor tick, dispatch workers, review branches or merge anything.
- Refactor code yourself: the quality theme goes to the build lane, in its
  own worktree, with its own review, or two loops end up in one tree.
- Change what the maintainer has kept (release approval, ADR acceptance,
  what enters the roadmap, repo settings) — you may *recommend* a change
  to the delegation boundary; only the maintainer moves it.
- Propose more than one *process* change per run, or any change without a
  number attached to it.

## Report structure

```
# Retro — <window>
## What worked
- <practice> — <metric = value>
## Failures and root causes
- <failure> (evidence) → why → why → <root cause> → <where the fix lives>
## Constraint
<lane>, because <metric = value> (evidence: <PRs/runs>)
## Friction observed
- <who> had to <do what> because <why> — <evidence>
## The one process change
<what>, in <skill/ADR/ticket>, PR/issue: <link>
## The quality theme
<title> (backlog id) — <what it removes or speeds up>, <footprint>, <model>
## Expected effect
<metric> from <value> to <value> by <when>; revert if <condition>
## Not changed, noted for next time
- <observation> — <why not now>
```

## References

- [pyrite-conductor](../pyrite-conductor/SKILL.md) — the loops you watch;
  its tick log and `process` issues are your primary evidence
- `poppendiecks` KB: `amplify-learning`, `seven-wastes-of-software`,
  `eliminate-waste` — the premise and the checklist
- ADR-0019 (the constraint is review attention), ADR-0032 §3a (the value
  chain: each layer must buy new information), ADR-0033 (where findings go)
