---
id: adr-0046
title: A CLI command returns its outcome; one point renders it and sets the exit code
type: adr
importance: 5
adr_number: 46
status: proposed
date: '2026-10-08'
tags: [architecture, cli, contract, errors, exit-codes, plugins]
links:
- target: adr-0037
  relation: amends
  kb: pyrite
- target: adr-0040
  relation: amends
  kb: pyrite
- target: adr-0010
  relation: related
  kb: pyrite
- target: one-cli-outcome-contract-commands-return-an-outcome-one-emitter-decides-output
  relation: related
  kb: pyrite
---

# ADR-0046: A CLI command returns its outcome; one point renders it and sets the exit code

## Context

`docs/json-contracts.md` ("Exit codes (CLI)") promises: 0 when every effect
asked for happened, 3 when some did, 1 when none did or the request was
refused, 2 for usage. #526 (PR #779) made that true command by command: on
`dev` at 8a0ad216 there are 111 `raise typer.Exit` sites, 24
`exit_unless_whole` calls and 81 `cli_error`/`cli_error_from` calls across
28 CLI modules, plus five module-local error helpers (`_task_error`,
`_kb_error`, `_cli_err`, and two `_cli_error`), four of which map exception
types to codes by hand instead of reading `exc.error_code` (ADR-0037 §3).
The rule is held in place by hand-kept tables of all 195 registered commands
(`GUARDED`, `SAFE`, `READ_ONLY`) and two AST scans, which cannot see a
failure emitted as JSON with exit 0 (#787) or swallowed in a service (#788).
That is a rule enforced call site by call site (design principle 8).

Every leaf command already runs inside one function: the root group's
`invoke` (`PyriteCLIGroup`, `pyrite/utils/errors.py`), which today catches
`ConfigSaveRefusedError` for every command of `pyrite` and `pyrite-admin`.
Click passes a leaf command's return value up through nested Typer groups to
that `invoke` (checked on Typer 0.24.1). Plugin commands are mounted under
the same root (`pyrite/cli/__init__.py`, `get_all_cli_commands`).

## Decision

1. **A command returns an `Outcome`; it does not choose an exit code.** An
   `Outcome` is one of `done`, `partial` or `nothing`, and carries the
   result data (what a machine format serialises) and a renderer for
   `--format rich`. A batch outcome is built from counts
   (`Outcome.from_counts(done, failed, data)`), with the rule of
   `exit_unless_whole`: none failed is `done`, some of each is `partial`,
   none done is `nothing`.
2. **A refusal is raised, not returned.** A command that refuses raises a
   `PyriteError`; the code lives on the class (ADR-0037 §3). There is no
   `error` variant of `Outcome`, so there is one representation of a refusal,
   not two.
3. **One point renders both.** `PyriteCLIGroup.invoke` takes the leaf's
   return value or exception and is the only place that prints a result in
   the requested format, renders a refusal in the canonical error shape
   (`cli_error_from`), and sets the exit code: `done` 0, `partial` 3,
   `nothing` 1, refusal 1, usage 2 (click). `pyrite-read` adopts the same
   group. A command that returns `None` has rendered itself; that is the
   legacy path, allowed only for commands on a list that may only shrink.
4. **The registry is the enumeration.** A test walks every registered command
   of the three CLIs and requires its callback to be annotated `-> Outcome`,
   or to be on the legacy list. A module with no legacy command may not call
   `typer.Exit`, `sys.exit`, `cli_error` or `exit_unless_whole`.
5. **The plugin CLI contract gains `Outcome`.** ADR-0040's "CLI helpers" row
   becomes `cli_error` and `Outcome`. A plugin command may keep returning
   `None` (it then owns its output and exit code, as today); one that returns
   an `Outcome` gets the same rendering and exit codes as core.

## Questions this ADR leaves open

- Does a machine-format payload of a `partial` or `nothing` outcome carry an
  `outcome` key (`"partial"`), so a caller that cannot see the exit code can
  tell? Recommended: yes, additive, for dict payloads; decided before the
  first slice that changes a payload, not in the first slice.
- Is a lost `task claim` a `nothing` (today: payload `{"claimed": false}`,
  exit 1) or a refusal with a code? Kept as `nothing` until decided.
- Where the format comes from: the leaf's own `--format` parameter until #303
  lands its shared option (`PYRITE_FORMAT`, JSON default, no `-f`); then that
  option, declared once.

## Consequences

- The #779 tables and scans shrink as modules migrate; at the end the
  registry test and the outcome type carry the rule, and the tables are
  deleted.
- A failure the command never learns of (a service that logs and continues)
  is still invisible. Services that loop over items report per-item failure
  in their result; that is a separate rule for services, not this one.
- MCP and REST keep their own result dicts; this ADR does not change them.
- Corrects ADR-0037 §3's "maps code to exit code": the CLI exits 1 for every
  refusal, and the code is in the output.
