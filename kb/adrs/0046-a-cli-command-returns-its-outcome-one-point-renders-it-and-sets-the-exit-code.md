---
id: adr-0046
title: A CLI command returns its outcome; one point renders it and sets the exit code
type: adr
importance: 5
adr_number: 46
status: accepted
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

> **Accepted by the maintainer, 2026-10-10**, with the enforcement-point amendment below (decisions 6 to 8; Andon #822, PR #823).

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

## Enforcement point (amendment 2026-10-10, Andon #822; accepted by the maintainer, 2026-10-10)

Decisions 3 and 4 said the root group renders the outcome and that a scan of
source shapes keeps commands from going around it. Three cold reads found
shapes the scan did not know (#779 r0, #793 r0, #793 r1). The property is
about what a command does, not how it is written, so it is enforced where the
command runs. A spike built this against #793 at 5fca819e (Typer 0.27.3,
Python 3.12, macOS); what follows is what it had to be, not what was
proposed. The numbers are in the item's "Groom 2026-10-10 (Andon #822)".

6. **`PyriteCLIGroup.invoke` is the only way out of a command on the
   contract, checked on every run, as an allow-list.** A command is on the
   contract when it declares the shared `--format` option. From the moment
   its body is entered until the root has decided, the root accepts exactly:

   - a returned value whose type is exactly `Outcome` (not a subclass), which
     the root renders and whose exit code the root reads from the one table;
   - a raised `PyriteError`, rendered as a refusal, exit 1;
   - a raised `ValueError`, while decision 3's transitional rule lasts.

   Everything else is an internal error, code `NO_OUTCOME`, exit 1, printed
   in the format asked for, and the only thing on stdout in a machine format:
   any other return value (`None`, a dict, an `Outcome` subclass); any other
   exception, `BaseException` included (`typer.Exit`, `SystemExit`, `Abort`,
   a `ClickException`, a usage error raised by the body, a crash); and any
   byte written to stdout before the root rendered. Rendering runs inside the
   same guard: a renderer that raises or exits is `NO_OUTCOME`. What the
   command wrote to stdout is not lost; the root writes it to stderr. The
   traceback of an unexpected exception goes to the DEBUG log (`-vv`).

7. **Two hooks, one decision.** The root cannot see where click's parsing
   ends and the command's body begins: `--help`, an unknown option and the
   body's own `raise typer.Exit(0)` all arrive at `invoke` as the same
   exception types. So the shared option, which is the only thing a command
   on the contract declares, also marks the moment the body is entered (it
   has the leaf's context; the root does not). The root starts watching
   stdout at that moment and decides everything. Before it, click is still
   parsing and nothing changes: `--help` exits 0, usage errors exit 2.

8. **Stdout is watched at two levels.** `sys.stdout` is replaced for the
   body (this sees `print`, `typer.echo`, every Rich `Console()` that was not
   given a file, `sys.stdout.write`, `json.dump(..., sys.stdout)`), and file
   descriptor 1 is pointed at a temporary file (this sees `os.write(1, ...)`,
   a child process that inherits stdout, `sys.__stdout__`, `/dev/stdout`, and
   a `Console(file=sys.stdout)` made at import). Replacing `sys.stdout` alone
   missed five of the planted shapes. If descriptor 1 cannot be duplicated
   (stdout closed), the root watches `sys.stdout` only.

### Exceptions to the allow-list, and why

- **`KeyboardInterrupt` passes through** (exit 130). The root cannot tell a
  person's Ctrl-C from a command that raises it, and Ctrl-C must keep
  working.
- **Stderr is open.** Progress, warnings and the `-f` deprecation notice go
  there, and the root cannot tell a warning from a failure by its text. A
  command that writes a failure to stderr and returns `done` is not caught.
  The machine contract is stdout and the exit code; both stay the root's.
- **A rich renderer chooses its words.** `nothing` legitimately renders
  "Failed: lost the claim" in red; the root cannot check that a `done`
  renderer does not. The kind, the exit code and every machine format are
  still the root's. In `--format rich` a renderer that prints and then raises
  leaves its partial text before the `NO_OUTCOME` line.
- **A usage error is click's only while click is parsing.** After the body
  is entered, `ctx.fail()` and `typer.BadParameter` are the command choosing
  exit 2 and are `NO_OUTCOME`; bad input found by the body is a
  `ValidationError`. (The spike ran both rules; no task test needs the
  lenient one.)
- **A command on the contract does not prompt.** `typer.confirm` writes its
  prompt to stdout, and with `err=True` `input()` still writes one space
  there. A command that asks a person a question keeps to the legacy path
  until a rule for prompts is decided (an open question, below).
- **Legacy commands and plugins that return `None` are not watched**
  (decision 5 stands). Their stdout is not buffered, so prompts, progress and
  streaming work as before.

### What no check in the process can see

`os._exit` (exit 0, empty stdout), an `atexit` handler and a thread that
outlives the command write or exit after the root has returned. Nothing in
`invoke` can catch them. The source scan stays for these, as a hint and
named as one; and a caller can tell the first by itself, because every
outcome prints a document in a machine format, so exit 0 with empty stdout
is never a valid answer.

### Consequences of the amendment

- Decision 4's list of calls a migrated module "may not" make is no longer
  the guard and the docs stop saying it is. The registry test still requires
  `-> Outcome` and the shared option; the behavioural test (every command on
  the contract, a planted way out of each class, through the real root)
  carries the property.
- A crash in a command on the contract is one `NO_OUTCOME` line naming the
  exception, not a traceback; `-vv` shows the traceback.
- "A click usage error" means the class of the click Typer runs. Typer
  0.27.3 vendors click as `typer._click`; `click.ClickException` from PyPI
  is an unrelated class there, and `pyproject.toml` allows Typer from 0.12.3.
  The root must not name either module's class by a private path.
- Cost measured: about 0.12 ms per command on the contract (the descriptor
  swap), nothing for a legacy command beyond one unused object.

### Open questions the amendment adds (the maintainer's)

- **Prompts.** Ten `typer.confirm`/`typer.prompt` sites in six CLI modules
  (`entry delete`, `kb remove`, `repo`, `extension uninstall`, the GitHub
  auth commands). Options: they stay legacy; they require `--yes` and refuse
  without it; or the root offers the one way to ask, on stderr.
- **Usage errors from the body**: strict (above, recommended) or allowed.
