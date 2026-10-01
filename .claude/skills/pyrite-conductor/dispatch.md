# Dispatch: composing themes and launching workers

How the conductor turns two trackers into reviewable units and hands each to
one worker in its own worktree. The old shared-tree wave protocol lives on
here in one form: **file footprints still decide what can run in parallel.**
With a worktree per worker an overlap no longer clobbers files; it surfaces
later, as a rebase conflict at the PR and as the semantic conflict nobody's
tests catch. Same planning, later and more expensive failure, so plan.

## 1. Compose themes

A theme is what a reviewer would recognize as one change. Test each candidate:

- Would the PR title make sense to someone who did not watch it being built?
- Is it complete — could it ship alone — rather than step one of three?
- Does it stay inside one area of the code, or does it cross layers for one
  reason (a fix that touches storage, service and endpoint is one theme; two
  unrelated fixes that happen to touch the same file are not)?

Group GitHub issues and backlog items by that test. Three small CLI bugs in
the entry write path are one theme; a docs correction is not a theme of its
own unless there is nothing else to batch it with — then it is, and the KB
fast path makes it a 30-second PR.

Write each theme down before dispatching, as the worker's spec:

```
Theme:       write-path correctness
Closes:      #15, #14, #16, #17
Groom:       <ticket or item with the `## Groom` section>; its "wrong if" line
Acceptance:  (from the groom: observable properties, and the test that fails today)
Regimes:     the boundaries the tests must enter — an index above the backend's
             hard limit; the retry after a half-finished run; empty, None and
             oversized inputs; the case where every candidate is filtered out
Touches:     pyrite/models/base.py (existing), pyrite/services/kb_service.py (existing),
             pyrite/schema/validators.py (existing), tests/ (new files)
Out of scope: the packaged web UI; anything in web/
Model:       opus   (cross-cutting; touches the base class and the service layer)
```

**`Regimes:` is not optional for anything that touches storage, the server, a
script that mutates the repo, or a loop with a bound.** A test proves the fix
is real; it does not prove the change is safe where it was never run. On
2026-09-18 (retro 5) two of the window's three Opus themes came back from the
cold read for exactly that: #145 pushed search filters into the KNN with 44
tests red against `dev`, and every one of them ran below sqlite-vec's 4096-row
cap — above it the branch raised on every semantic search, including the
maintainer's 18,909-entry index; #140 built a release script with 85 tests
and none entered "the tag already exists from an aborted run", the one path
that moves `main` and then reports nothing happened. Neither spec named the
regime, so neither worker's evidence could. Write the regimes as one line
each, from the ticket's Touches: for every hard limit, retry, empty set or
external state the code depends on, name the case; the worker's report lists
each regime with the test that enters it (pyrite-worker's `Evidence`), and
the review checks the list against the diff before the cold read. A regime
the conductor cannot name is a question for the architect or a spike, not a
line to leave blank.

**A regime names the surface as well as the condition.** The test must run
where the bug can appear, not only where the mechanism is: a web change is
tested on a *rendered component* (`@testing-library/svelte`), never only the
extracted module; a change to request lifetime, sessions, threads, startup or
anything the threadpool touches is tested on a *live server*, never only
`TestClient`, which runs handlers on the test thread and cannot show
interleaving. Retro 7 (2026-09-20): two of two second passes had tests that
exercised the mechanism on a surface where the bug could not appear — #202's
nine module tests never rendered the component that clobbered the title on a
same-route navigation; #203's `TestClient` tests passed at a design the live
server rejected outright. Write the surface into the `Regimes:` line
("same-route navigation — rendered layout"; "40 concurrent reads — live
uvicorn"), and if the repo has no fixture for that surface, that is the first
thing the theme builds.

## 2. Footprints and sequencing

List the files each theme will modify (new files never conflict). Two
themes that modify the same file run **in sequence**: dispatch the first;
dispatch the second when the first's PR has merged, from a fresh
`scripts/new-worktree.sh` (so it branches from the merged `dev`). Do not try
to save time by running both and rebasing later — the rebase is where the
semantic conflict hides.

Cap: **the two budgets in SKILL.md ("WIP limits")** — at most four suite
slots (a worker holds one, the conductor's review suite one, a Playwright run
two; one Playwright-heavy task at a time), and at most two of the loop's PRs
ready with auto-merge armed; no new dispatch while three or more branches
await review. The cap before 2026-09-18 (three workers, six when disjoint,
nothing counted but workers) crashed the maintainer's machine (#168).

**A footprint has two dimensions: files and the machine.** A theme is
*machine-heavy* when its acceptance runs a browser suite, loops the full
suite, starts servers, or loads a model — Playwright packages, "N consecutive
`-n auto` runs", the smoke layer, embedding work. Disjoint files do not make
heavy themes disjoint: on 2026-09-18 three Playwright packages plus one
20-run pytest loop shared a 10-core machine at load average 28, each
Playwright package took 42 min against package B's 20 alone, and every
review's suite re-run paid the same tax. Later the same day up to eight
`-n auto` suites ran at once and the machine went down (#168): the count had
covered dispatched workers, not the conductor's own review suites, not
review agents told to run the suite, not the pre-push hook inside each
worker. **Everything that runs the suite, a browser, a server or a model
holds a suite slot** (SKILL.md, "WIP limits": four slots; a Playwright run
holds two and only one runs at a time) — say `heavy: yes|no` in the spec, so
the next tick can count slots before it dispatches. Every worker prompt says: run
the suite with `-n 4`, once per verification, never in a loop beside another
process; every reviewer and outside-review prompt says: run no suite, the
conductor has run it and here is the result.

## 3. Create the worktree, then dispatch

Once per machine: the conductor's state lives in draft PR bodies and issue
threads, and the permission classifier refuses read-only `gh pr view --json
body` and merged-branch cleanup from inside an agent unless allowed (#72).
`.claude/settings.json` is gitignored here, so add this block to
`.claude/settings.local.json` on a fresh clone (maintainer-approved
2026-09-18):

```json
"Bash(gh pr view:*)", "Bash(gh pr list:*)", "Bash(gh pr checks:*)",
"Bash(gh issue view:*)", "Bash(gh issue list:*)",
"Bash(gh run view:*)", "Bash(gh run list:*)",
"Bash(git worktree list:*)", "Bash(git worktree remove:*)", "Bash(git worktree prune:*)",
"Bash(git branch -d:*)", "Bash(git branch -D:*)"
```

Write PR and issue bodies to a file and pass `--body-file`: a body passed
inline through `"$(cat <<'EOF' … )"` breaks on backticks and apostrophes in
the shell's eval (twice on 2026-09-18), and the failure looks like a
half-run command.

```bash
cd /Users/markr/pyrite && scripts/new-worktree.sh fix/<theme-slug>
#  -> /Users/markr/pyrite-wt/fix-<theme-slug>  with its own .venv and hooks
```

Then launch the worker with the Agent tool. Never use the tool's
`isolation: "worktree"` option (its base commit has been wrong before, and its
merge ceremony is what the script replaces); the worker is *told* its
worktree.

Agent types under `.claude/agents/` register when a session starts, so a
session that created or merged `pyrite-worker.md` will not have it (observed
2026-09-18). If `subagent_type="pyrite-worker"` is refused, dispatch
`subagent_type="general-purpose"` with the same `model` and paste the body of
`.claude/agents/pyrite-worker.md` at the top of the prompt; the next session
has the type.

```
Agent(
  subagent_type="pyrite-worker",
  model="sonnet" | "opus",
  description="theme: write-path correctness",
  prompt=<the spec above, plus:>
    "Work in /Users/markr/pyrite-wt/fix-<slug> on branch fix/<slug>. Use its
     .venv. Load the pyrite-dev skill and follow it. Test the groom's
     riskiest assumption first and post the result on the draft PR. Open a
     draft PR to dev right after your first push so CI starts; do not mark
     it ready. When the theme is complete, reply with the report
     format from pyrite-dev, Learned, Captured in and Tokens included."
)
```

### Model by shape of work

| Shape | Model | Why |
|---|---|---|
| Well-specified, mechanical, clear acceptance (a slug fix, an exit code, a path default, a docs correction) | **sonnet** | the spec carries the judgment; speed and cost win |
| Design-shaped or cross-cutting (touches the base class, the service layer, auth, storage, a public interface; needs a root-cause investigation) | **opus** | the judgment is the work |
| A spike — the architect could not write acceptance criteria because a question is open (root cause unknown, two designs plausible, a dependency unverified) | **opus** (`pyrite-spike`) | the deliverable is a decision-ready ticket, not code; one tick, no PR |
| The conductor itself, and cold reads | the strongest available | reviewing is judgment |

When unsure, the tell is the ticket: if its acceptance criteria could be
handed to a careful junior engineer with no further conversation, sonnet.
**And if the ticket says what hurts but not why — "data loss", "corrupts",
"silently", mechanism unknown — it is a spike first, not a worker.** #46/#86/
#87 were three faces of one serialization fault nobody had named when a
worker was sent to fix #46; the theme took three passes, two cold reads and
the circuit breaker to land what a one-tick spike would have written as
acceptance criteria: "no entry type writes a key its source file did not
have; a no-op load→save is byte-identical."

### Brief by model: mission for Opus and Fable, instructions for Sonnet and Haiku

Match the brief to the model (maintainer, 2026-09-26).

**Opus and Fable get a mission, not tactics.** Write:
- the **goal state** as properties that must hold when the work is done;
- the **invariants**: what must not change, meaning behaviour, public shape, and what may never be published;
- the **hard constraints**: the machine budget, where to push, and security disclosure;
- **done**: what evidence proves it;
- **why it matters**, in a sentence.

Leave out which helper to call, what to monkeypatch, and which line to edit. Those are the worker's judgment.

**Sonnet and Haiku keep the precise, step-by-step brief** further up this file. For them the spec carries the judgment.

**For every model, put nothing in the brief that the skill or the agent definition already says.** In the 2026-09-25/26 window, about fifteen lines of boilerplate in every brief crowded out the one line that mattered. Fix-round briefs follow the same rule: state the property that failed and the evidence; don't dictate the patch.

### Plan before build (Opus-shaped themes)

For a design-shaped or cross-cutting theme, the worker's first deliverable is a plan, not code. The plan is at most one page, posted as a comment on the draft PR, led by the result of testing the groom's riskiest assumption, and covers:
- the goal state restated as properties;
- the surfaces and callers affected;
- what must not change;
- the test list, including negative, environment and concurrency cases;
- what is out of scope;
- open questions.

The conductor, or a cold reader for security and storage themes, answers the plan within the tick. The worker builds only after that answer.

**Why.** In the same window, almost every cold read changed its PR. Most findings were properties the brief never named, not coding slips:
- behaviour that had to stay the same (#501's codes, #515's busy timeout);
- environment edges (a read-only file, a directory fsync that fails, an old SDK, a built `web/dist`);
- shared test state (#507, #509).

Each cost a review round of 30–60 minutes plus a full suite. Answering a plan costs minutes.

Sonnet mechanical themes skip the plan.

### Security confirmation is a failing regression test

When a brief asks an agent to confirm a suspected security defect, it asks
for **one pytest test that asserts the required property**. The test is red
on `dev`, and the fix keeps it. Never ask for a reproduction, a probe of the
endpoint, a request sequence or an exploit. Under the probe framing a safety
classifier stopped 2 of 6 audit runs. Under the test framing none stopped,
and the tests went straight to the fix workers (retro 14; maintainer,
2026-09-26).

### Keep the layer boundary ratcheting down

The boundary test from #380 (`tests/test_layer_boundaries.py`) only lets its allowlist shrink. Two rules keep it shrinking:
- **A theme that edits a function on the allowlist removes that function's entry in the same PR.** The theme leaves the file better than it found it. Put this in the brief's "done".
- **Refactoring runs on a schedule.** The ~fifth-theme `quality` slot (SKILL.md) alternates between the retro's quality theme and a **ratchet-down theme**: remove the next few allowlist entries by moving storage and SQL access behind services, with the boundary test as acceptance.

### Prompt hygiene

A worker's prompt says which files are **new** and which are **existing and
to be patched minimally**. "Add feature X with endpoints, model and UI" leads
to rewrites of files it should have touched in two lines. Name the files.

Sub-agents a worker spawns share the worker's branch and worktree; the worker
gives them disjoint files, the same rule one level down.

## 4. While workers run

Do not poll. Workers report when done; the harness notifies you. Use the time
for the health check, absorbing earlier branches, or composing the next
themes. Never claim to know a worker's result before its report arrives.

## 5. When a worker reports

Go to [review.md](review.md). The report is where review *starts*, not where
it ends.
