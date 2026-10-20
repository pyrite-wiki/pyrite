---
id: docs-drift-pass-after-the-0242-merge-run
title: "Docs drift pass: reconcile README, CLAUDE.md and docs/ with the 0.24.2 merge run"
type: backlog_item
tags:
- docs
- quality
importance: 4
kind: chore
status: proposed
priority: 3
effort: M
project: pyrite
---

The docs lane has not run since `4256263` (the contributor docs pass), and
**176 commits** have landed on `dev` in the two days since, including four
security and correctness PRs in one tick (#180 read scoping, #145 search
filters, #161 git error disclosure, #140 the release script). Every REST
endpoint module, the MCP server, three CLI command modules and `scripts/`
changed in that window. One `pyrite-docs` PR, reviewed like any other.

## Two drifts already confirmed — start here, they are the evidence this is real

**1. `CLAUDE.md:108` undercounts the suite by about a third.** It says
"~3100 in tests/, ~4000 including extensions/*/tests/". Actual, measured on
`43724fd`:

```
$ .venv/bin/pytest tests/ extensions/ --collect-only -q | tail -1
5347/5369 tests collected (22 deselected)
```

Per the standard the docs lane already follows — prefer a number a test can
generate over one a human asserts — this should not be re-asserted with a
fresh hand-count. Either drop the count or have a test derive it.

**2. `README.md:82` documents behaviour the code does not have.** It states
`kb_bulk_create` "handles up to 50 entries per call with best-effort
per-entry semantics". **#95 is open and says the opposite**: one malformed
entry rejects the whole batch. There is no `best_effort` anywhere in
`pyrite/server/mcp_server.py`. This is the dangerous class — a doc promising
an agent a guarantee it will not get — so it is either a README fix or a
reason to prioritise #95, and the docs pass should say which.

## Scope

- `README.md`, `CLAUDE.md`, `docs/getting-started.md`,
  `docs/configuration.md`, and the CHANGELOG's `[Unreleased]` wording.
- The `pyrite-dev` skill's own commands, which drift
  the same way and are read by every agent.
- `kb/` component and standard entries for the modules that changed.
- The release runbook, now that `scripts/release.py` exists (#140) — check
  `533746a` and `7b8358e` actually left it consistent rather than assuming.

## Acceptance

- Every factual claim touched is verified by running it, not by reading the
  code. A claim that cannot be run is dropped or rewritten as an intention.
- Counts are generated or asserted by a test, never hand-typed; see the
  existing `docs-counts-generated-or-asserted-from-code` item, which this
  should close or explicitly defer to.
- The `kb_bulk_create` contradiction is resolved in one direction, with the
  decision stated in the PR.
- No code changes beyond what a doc claim forces; a code fix found on the way
  becomes its own issue.

## Out of scope

Rewriting docs that are merely thin rather than wrong. This is a drift pass,
not a documentation project — the point is that what is written is true.
