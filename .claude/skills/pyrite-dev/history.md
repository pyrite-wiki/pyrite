# Why the rules exist

The incident behind each rule in [SKILL.md](SKILL.md), `pyrite-architect` and
`pyrite-worker`. Read it when a rule looks arbitrary or before proposing to
drop one. The rules themselves live in those files; nothing here is an
instruction.

## Grooming (pyrite-architect)

- **The groom is written into the ticket; the reply is an index.** 2026-09-18:
  a groomed item was reported as created and existed nowhere a later tick
  could find it (#141). Maintainer, same day: "the architect should put
  grooming results directly into the pyrite backlog item or ticket — so that
  there is a clear source of truth."
- **Scope is the invariant across surfaces.** 2026-09-27 to 10-01: one defect
  class (a write changes only what it names, and reads back the same on every
  surface) arrived as nine issues over six days (#455, #549, #554, #555, #557,
  #561, #568, #569, #47), all filed by the maintainer from use, because each
  fix was scoped to its ticket's instance.
- **Ask what the change breaks before naming anything out of scope.** The #555
  groom put `pyrite init` templates and the software-kb preset out of scope.
  Default-on enum enforcement then made shipped schemas refuse their own
  workflows; the worker had to change them. Predicted about 10 files, actual
  26 (PR #570).
- **Contracts, options, open questions, checked versus assumed.** Retro
  2026-10-01: the earlier definition forbade "implementation detail beyond the
  footprint", copied acceptance verbatim and had no place for doubt. The #555
  groom was strongest where it went beyond that ("What the code does today",
  with file and line). Maintainer: "keep the core design contracts in mind,
  investigate the relevant code, talk about what they have learned about the
  code, explore options, encourage full exploration, and provide clear
  pointers to relevant code examples and component documentation."
- **Reproduce first, or it is a spike.** #46, #86 and #87 were three faces of
  one serialization fault nobody had named when a worker was sent to fix #46.
  The theme took three passes, two cold reads and the circuit breaker.
- **Groom edits happen in the `kb/` worktree the conductor names.** Before
  2026-09-21 that was a standing weekly log branch; the tick log now lives
  outside git, so a `kb/` branch exists only when a tick grooms something.

## Where you are

- **`pyrite kb list` must show this worktree's `kb/`.** 2026-09-18: `pyrite
  update` run from a worktree changed three files in the main checkout,
  because `-k pyrite` resolved through `~/.pyrite`. Detail in
  [gotchas.md](gotchas.md).

## Learning (worker)

- **Test the groom's riskiest assumption first.** See the #555 footprint
  above: the assumption that shipped schemas were unaffected was cheap to test
  on day one and expensive to discover mid-build.
- **`Learned`, `Captured in`, `Tokens`.** Retro 2026-10-01: the report had no
  field for what the worker found out, the skill gave learning one line
  (append to gotchas.md; two commits touched it in the window), and no tick or
  PR recorded tokens. Maintainer: "Filing a ticket starts a cascade of
  exploration and learning that results in new code that captures that
  learning, new docs that capture important findings, etc. Each actor in the
  chain helps the whole team learn. Being concise is part of this." and "the
  speed of learning is the metric to watch."
- **Mission briefs get a plan before code.** Maintainer, 2026-09-26. In the
  2026-09-25/26 window almost every cold read changed its PR, mostly for
  properties the brief never named (#501's codes, #515's busy timeout, a
  read-only file, a built `web/dist`, shared test state in #507 and #509).
  Each cost a review round of 30 to 60 minutes; answering a plan costs
  minutes.

## Verification

- **Delete each guard alone.** Retro 9, 2026-09-24: reverting a whole fix
  proves some test goes red, not that each check is pinned. In one window 6 of
  11 cold reads found a guard or a test that stayed green with its target
  removed (a resolved-path check, loop-lifetime guards, refusal tests matching
  any error, a tier test that held under the bug). Each cost a send-back and a
  full push cycle.
- **Regimes.** 2026-09-18: two Opus themes (#140, #145) were redispatched from
  the cold read for regimes their 85 and 44 tests never entered.
- **Draft PR right after the first push; CI is the authority.** #356: full
  local suites from several worktrees at once filled the disk and pushed load
  past 25.
- **Test each tree once.** Maintainer, 2026-09-25. A repeated selection costs
  about 45 minutes under load, and two suites on a loaded machine produce
  failures that are load, not signal.
- **A red pre-push in an untouched test.** Maintainer, 2026-09-25: the local
  run is the first of several gates (PR CI, the merge queue's matrix, `dev`,
  `main`, review). The same day a module reload in one test broke an unrelated
  one only after the batch's change, which is why a CI failure in the same
  test is the worker's.

## Bookkeeping and finishing

- **Changelog fragments, never `CHANGELOG.md`.** #243: every branch appending
  to one file made five PRs conflict in a session.
- **Diff the footprint before pushing.** 2026-09-18: a worker found 18 foreign
  commits on its branch this way, one push from the wrong PR (#119).
