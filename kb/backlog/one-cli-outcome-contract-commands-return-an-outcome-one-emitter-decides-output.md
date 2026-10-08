---
id: one-cli-outcome-contract-commands-return-an-outcome-one-emitter-decides-output
title: 'One CLI outcome contract: commands return an outcome, one emitter decides output and exit code'
type: backlog_item
tags:
- quality
- refactor
- cli
importance: 5
kind: tech_debt
status: proposed
priority: high
effort: L
rank: 0
---

Retro 2026-10-08 quality theme (Mark approved). Groom with Opus after #779 lands, then slice by module.

## Why
Each of the CLI's ~66 `raise typer.Exit(...)` sites decides its own exit code, and results come back as ad-hoc dicts (`{"success": False}`, `{"claimed": False}`). So 'no command exits 0 when an effect it was asked for did not happen' (#526, PR #779) could only be enforced by classifying 195 commands by hand plus two AST scans. The delta cold read showed those scans still can't see a failure emitted as JSON (#787) or swallowed in a service (#788).

## Goal state (properties)
- Every CLI command returns an outcome value: done | partial | nothing | error (with a code).
- One emitter renders it in each output format and maps it to the exit code: 0 done, 3 partial, 1 nothing or error, 2 usage. The rule is the one documented in docs/json-contracts.md.
- No command calls `typer.Exit` with a computed code, and a structural test enforces that.
- The #779 classification tables shrink to what the type system can't express.

## Acceptance (first slice)
- The outcome type and the emitter, with property tests.
- One module migrated end to end (`task_commands.py`: claim, decompose), with no behaviour change against #779's tests.
- A test fails if a migrated module adds a raw `typer.Exit`.

## Pointers
- `pyrite/utils/errors.py` (`exit_unless_whole`, `PARTIAL_EXIT`, `cli_error_from`)
- `tests/test_requested_effect_exit_code.py`
- #786, #787, #788
