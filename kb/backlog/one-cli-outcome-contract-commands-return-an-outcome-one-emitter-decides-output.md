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

## Groom 2026-10-08

Groomed by the architect on `dev` at 8a0ad216 (includes #779). Read-only: no
suite, server or browser was run. ADR drafted alongside: **ADR-0046
(proposed)**, `kb/adrs/0046-a-cli-command-returns-its-outcome-one-point-renders-it-and-sets-the-exit-code.md`.

**Principles.** 8 (one rule, one point: the exit-code rule is enforced at
111 `raise typer.Exit` sites today), 9 (`docs/json-contracts.md` must be true
of every command), 3 ("reports it").

**The user's model.** "Exit 0 means it did what I asked. If it did part, the
output says which part, in the format I asked for. The format changes how
the answer looks, never what the command does."

**The implementation model.** A command computes what happened and returns
it; it never prints a result or picks an exit code. One function, the root
group's `invoke`, renders the outcome in the requested format and maps it to
0/3/1; a refusal is a raised `PyriteError`, rendered at the same point from
the class's code. The registry of commands is the list the rule is checked
against.

**Where the two differ today** (the finding). Each command has its own
`if formatted is not None: typer.echo(...); return` branch, so the format
decides which code runs. That one shape produces four families of bug, all
open: the exit code follows the rich branch only (#787); the effect lives in
the rich branch only (#660: `kb discover --add` in JSON registers nothing,
exits 0, `kb_commands.py:207-209` returns before `if add:` at 227; #779's
table calls it SAFE); a format is ignored or crashes (#547, #553, #666's
csv); a machine format is printed through Rich (#611 part 1). The default
format differs by command (#303, decided 2026-10-02, not built). The
backlog item names only the first family. Its four-variant outcome also
duplicates the refusal path ADR-0037 already put on the exception class;
see options.

### 1. Contracts that apply

- `docs/json-contracts.md`, "Exit codes (CLI)" (l.546): 0 every effect
  happened, 3 part, 1 refused or none, 2 usage; batch rule is
  `exit_unless_whole`. The emitter implements exactly this table.
- `docs/json-contracts.md`, "Error shape": the CLI refusal is
  `{error, error_code, suggestion?, retryable}` in machine formats, one
  `ERROR [CODE]` line in rich. Unchanged.
- `pyrite/utils/errors.py:104` `cli_error_from`: the one CLI mapping from a
  `PyriteError` to that shape, reading `exc.error_code`. The emitter's
  refusal path calls it; it does not grow a second one.
- `pyrite/utils/errors.py:52` `exit_unless_whole(failed, done)`: the batch
  rule. Becomes `Outcome.from_counts`; same truth table.
- ADR-0037 §3: codes live on exception classes. Binding here: no
  `isinstance` chain maps exceptions to codes (four module helpers do today).
  §3's "maps code to exit code" is wrong (every refusal exits 1); ADR-0046
  corrects it.
- ADR-0040, "CLI helpers" row and §3 "CLI": plugin commands are
  `(name, typer.Typer)` with `cli_error` as their contract helper. An
  `Outcome` a plugin may return is a new contract symbol: ADR-0046 amends
  this row.
- ADR-0010 + #303's decision (2026-10-02): JSON is the default of every
  command, `PYRITE_FORMAT` sets a person's default, `-f` stops meaning
  `--format`. The ADR-0010 amendment #303 called for is not drafted.
- `kb/maps/surfaces-and-output-contracts.md` items 5, 6 (format is an output
  layer; one refusal contract).
- Written nowhere: what a *check* command's exit means (see section 2,
  "check commands"). Finding and docs task; ADR-0046 leaves it open.

### 2. What the code does today

Surprises first.

- **There is already one point every command passes through.**
  `PyriteCLIGroup.invoke` (`pyrite/utils/errors.py:135`) wraps every leaf of
  `pyrite` and `pyrite-admin`. Checked with a throwaway Typer app (Typer
  0.24.1): a leaf's return value, under a nested `add_typer` sub-app,
  reaches the root group's `invoke`. Click's standalone `main` then discards
  it and exits 0, so the root must act on it before returning. Plugin
  commands are mounted under the same root (`pyrite/cli/__init__.py:188`).
  `pyrite-read` does not use the group (`pyrite/read_cli.py:23`).
- **Exit sites.** 111 `raise typer.Exit`, 24 `exit_unless_whole(` calls and
  81 `cli_error`/`cli_error_from` calls across 28 modules (74 / 37 core /
  plugin for `Exit`; the item's "~66" undercounts). Per module (`Exit`,
  `exit_unless_whole`, `cli_error*`): software-kb `cli.py` 17/2/2,
  `task_commands` 12/2/2, `admin_cli` 11/2/7, journalism `cli.py` 10/4/1,
  `export_commands` 9/2/0, `cli/__init__` 7/2/11, `read_cli` 6/0/0,
  `kb_commands` 5/0/11, `index_commands` 4/2/6, `schema_commands` 4/1/3,
  `qa_commands` 4/0/0, cascade 4/0/0, `protocol_commands` 3/0/1,
  encyclopedia 3/0/0, social 3/0/0, `link_commands` 2/1/8,
  `entry_commands` 2/0/28, `ids_commands` 2/0/2, `init_command` 2/0/1,
  `search_commands` 1/2/4, `extension_commands` 1/0/4, `db_commands` 1/0/3,
  `mcp_setup_command` 1/0/1, `browse_commands` 0/3/6, `repo_commands`
  0/1/5, zettelkasten 0/0/2, `collection_commands` 0/0/1.
- **Five private exit helpers**, each listed in #779's `EXIT_CALLS` so the
  scan treats them as exits: `_task_error` (`task_commands.py:25`),
  `_kb_error` (`kb_commands.py:38`), `_cli_err` (`cli/__init__.py:129`),
  `_cli_error` (`entry_commands.py:42`, `browse_commands.py:28`),
  `_refusal_exit` (`entry_commands.py:78`); plus `stop`/`settle` inside
  `mcp_setup_command.py:892,1116`. `_task_error` maps
  `EntryNotFoundError` to `NOT_FOUND` and anything else to `ERROR` by
  `isinstance` (ADR-0037 §3 says read the class).
- **How output is rendered.** `pyrite/cli/output.py:24` `format_output(data,
  fmt)` returns `None` for rich, else the registry's serialiser
  (`pyrite/formats/__init__.py`: json, markdown, csv, yaml). Each command
  then branches; about 170 format branches or `format_output` calls across the modules (`task_commands` 20,
  `kb_commands` 20, journalism 18, `browse` 17, `read_cli` 14, `link` 14,
  `extension` 12, ...). Format options, by the registry: 87 commands have
  none (rich always), 56 `--format` defaulting to `json` (20 of them with
  `-f`), 28 defaulting to `rich` (10 with `-f`, all of `task`), 19 a
  `--json` flag (journalism, cascade), 1 honours `PYRITE_FORMAT`
  (`mcp-setup`, `mcp_setup_command.py:1080`, "declared here until the
  shared option of #303 lands"). Three `--format` options mean a file
  format, not output (`export collection`, `export site`, `import`).
- **Usage errors exit 1, not 2**, where a command checks its own arguments:
  `task create` with both title forms or a malformed `--field`
  (`task_commands.py:139-176, 215, 222`). `link_commands.py:786,793` raise
  `Exit(code=2)` by hand.
- **Check commands disagree.** A command whose answer is a verdict picks its
  own code: `kb validate` exits 2 on drift (`kb_commands.py:333,390`), the
  same code as a usage error; `ids missing` exits 3 when files are left
  (`ids_commands.py:96`); `index health`, `qa validate`, `protocol check`
  exit 1 when the check fails. No contract says which is right.
- **Services report failure two ways.** By raising a `PyriteError`
  subclass, or by returning a dict with a flag. Count of
  `raise ...Error` / failure-dict literals per service: `repo_service` 0/29,
  `kb_service` 44/17, `git_service` 3/13, `export_service` 6/5,
  `llm_service` 2/5, `task_service` 10/1; 18 more services only raise.
  `KBService.claim_entry` (`kb_service.py:2423`) answers "no such entry" as
  `{"claimed": False, "error": ...}`, not `EntryNotFoundError`; REST passes
  that through with HTTP 200 (`server/endpoints/tasks.py:72`) and MCP as a
  plain result (`mcp_server.py:1709`). `bulk_create_entries` returns
  per-item `{"created": False, "error", "error_code"}`.
- **`task decompose --format json`** prints `"decomposed": true` even when
  every child failed (`task_commands.py:582`); the exit (1 or 3) is the
  only signal.
- **Reproduced by citation.** The behaviour #779 fixed for `task` is pinned
  by `tests/test_requested_effect_exit_code.py::TestTasks` (l.576): a lost
  claim exits 1 in json and rich, and its payload's `claimed` is false;
  decompose with a refused child exits 3. #787 and #660 carry one-command
  reproductions that fail today. No new reproduction was run.

### 3. Invariant and surfaces

**Invariant.** For every registered command: the exit code is a function of
what happened (done 0, part 3, none or refused 1, usage 2); the requested
format changes only how the outcome is printed; every machine format is one
parseable document; an unsupported format is a usage error. Enumeration
source: the Typer registry of the three CLIs, as #779's `_commands()` walks
it (`tests/test_requested_effect_exit_code.py:1010`): **195 commands in 28
modules**, each classified by #779 as GUARDED (30), SAFE (82) or READ_ONLY
(83).

Every member, by module (commands: G/S/R), and the slice that takes it:

| Module | Cmds | G/S/R | Slice |
|---|---|---|---|
| `pyrite/cli/task_commands.py` | 10 | 2/5/3 | 1 |
| `pyrite/cli/entry_commands.py` | 7 | 2/4/1 | 2 |
| `pyrite/cli/link_commands.py` | 7 | 1/0/6 | 2 |
| `pyrite/cli/browse_commands.py` | 7 | 2/0/5 | 3 |
| `pyrite/read_cli.py` | 6 | 1/0/5 | 3 |
| `pyrite/cli/search_commands.py` | 2 | 2/0/0 | 3 |
| `pyrite/cli/collection_commands.py` | 2 | 0/0/2 | 3 |
| `pyrite/cli/kb_commands.py` | 15 | 1/11/3 | 4 |
| `pyrite/cli/repo_commands.py` | 6 | 1/3/2 | 4 |
| `pyrite/cli/ids_commands.py` | 2 | 0/2/0 | 4 |
| `pyrite/cli/init_command.py` | 1 | 1/0/0 | 4 |
| `pyrite/cli/index_commands.py` | 7 | 2/3/2 | 5 |
| `pyrite/cli/extension_commands.py` | 4 | 1/2/1 | 5 |
| `pyrite/cli/schema_commands.py` | 3 | 1/0/2 | 5 |
| `pyrite/cli/export_commands.py` | 2 | 1/1/0 | 5 |
| `pyrite/cli/db_commands.py` | 2 | 0/2/0 | 5 |
| `pyrite/cli/protocol_commands.py` | 2 | 0/1/1 | 5 |
| `pyrite/cli/__init__.py` | 14 | 1/9/4 | 6 |
| `pyrite/cli/qa_commands.py` | 10 | 0/3/7 | 6 |
| `pyrite/cli/mcp_setup_command.py` | 1 | 0/1/0 | 6 |
| `pyrite/admin_cli.py` | 27 | 4/17/6 | 7 |
| software-kb `cli.py` | 23 | 2/8/13 | 8 |
| journalism-investigation `cli.py` | 18 | 4/6/8 | 9 |
| cascade `cli.py` | 5 | 0/1/4 | 10 |
| social `cli.py` | 4 | 1/1/2 | 10 |
| encyclopedia `cli.py` | 4 | 0/1/3 | 10 |
| zettelkasten `cli.py` | 4 | 0/1/3 | 10 |

Totals: 195 = 10+14+17+24+20+25+27+23+18+17 by slice. #788 says four
READ_ONLY rows are wrong (`cascade extract-actors`, `cascade export`,
`cascade suggest-aliases -o`, `qa assess`); slices 6 and 10 classify them by
what they write, not by the table.

Surfaces:

- **CLI, core** (`pyrite`, `pyrite-admin`): root group is
  `PyriteCLIGroup`. In scope.
- **CLI, `pyrite-read`**: plain `typer.Typer` (`read_cli.py:23`); must adopt
  the group in slice 1 or its six commands cannot migrate.
- **Plugin CLIs**: mounted under the root (`cli/__init__.py:188`), so a
  returned `Outcome` is rendered for out-of-tree plugins too, with no lint
  of their source. In-tree plugins are slices 8-10; returning `None` stays
  legal for a plugin (ADR-0046 decision 5).
- **MCP**: tools return dicts, failures as `{"error": ...}` (ADR-0040
  "Errors" row). No exit code. Out of scope; checked: `_task_claim` returns
  the service dict unchanged.
- **REST**: a lost claim is HTTP 200 `{"claimed": false}`. Out of scope;
  belongs to the service-result rule (option b), not this one.
- **Docs**: `docs/json-contracts.md` "Exit codes (CLI)" lists commands by
  name (l.563-588) and "`--format` defaults" has a per-command table; both
  become one rule at the end. `kb/components/cli-system.md` describes
  `mcp-setup` as "the first command with the #303 output contract declared
  locally".
- **Tests**: `tests/test_requested_effect_exit_code.py` (GUARDED, SAFE,
  READ_ONLY, HANDLERS, PRINTS, EXIT_CALLS) shrinks to the registry test;
  `tests/test_doc_agent_contracts.py` runs the documented commands and
  checks the `--format` table against Typer signatures.
- **Files on disk, shipped schemas and templates**: none. No state Pyrite
  does not own is written.

### 4. Options

**(a) An `Outcome` type, rendered at the root group** (recommended). A
command returns `Outcome.done|partial|nothing(data, rich=renderer)` or
`Outcome.from_counts(done, failed, data, ...)`; a refusal is a raised
`PyriteError`. `PyriteCLIGroup.invoke` renders both and exits. The
item proposed a per-command emitter decorator; prefer the root because
forgetting a decorator is going around the rule (principle 8), and the root
already covers plugin commands. Enforcement: the registry test requires
each callback to be annotated `-> Outcome` unless its module is on a frozen
legacy list; a migrated module may not call `typer.Exit`, `sys.exit`,
`cli_error*`, `exit_unless_whole` or `format_output`. Closes the #787,
#660 and #611 families by construction: there is no format branch left in
the command. Cost: every command is touched once; rich renderers move into
small functions. Does not see a failure a service never reports (#788).

**(b) A typed result protocol on services**, rendered by every surface. A
service that does several things returns `BatchResult(done, failed)` or
raises; the CLI, MCP and REST map it. Fixes #788's class and REST's 200 on a
lost claim, and gives MCP the same truth. Cost: changes return shapes MCP
and REST callers and plugins (`claim_entry`, `bulk_create_entries` are
ADR-0040 contract calls) depend on; 6 services hold ~70 failure dicts. It
does not by itself remove the CLI's format branches, so #660/#787 remain.
Recommended as a **follow-on rule for batch services**, its own backlog item
after slice 1, with `Outcome.from_batch` as the CLI's one adapter.

**(c) A structural lint only.** Extend #779's scans to `typer.echo(...);
return` after a failure-shaped result. Cheapest, no runtime change. It is
what #779 did, and the cold read showed the scans miss emitted JSON and
services; each new shape needs a new scan. Not a rule (principle 8). Keep
#779's scans only until the last legacy module migrates.

**Recommendation: (a) now, (b) as the next rule, (c) never extended.** Fold
#303's output decision into (a): the shared `--format` option (#553, #547)
is declared once in slice 1 and read by the root group, so the format rule
and the exit rule have one home. That is a decision (below), because it
puts a behaviour change (task's default `rich` to `json`, `-f` deprecated)
into a refactor slice.

### 5. Open questions

For the worker to explore, and to overturn with evidence:

- Is the root group the right point, or does it fight Typer? Check
  `CliRunner` (tests call `invoke(app, ...)`), `standalone_mode=False`
  callers, and any command that calls another command's function directly
  (`task status` -> `_task_get_impl`; `pyrite-admin` commands that reuse a
  `pyrite` callback). If the root cannot see the value in one of these, say
  where and fall back to a decorator applied at registration, not by hand.
- Where does the emitter get the format? Recommended: the shared option's
  parameter name in `ctx.params`; until #303 is applied, a short list of
  today's names (`fmt`, `output_format`, `output_json`). The three file-format
  `--format` options (`export`, `import`) must not be read as output format.
- Should the root render an uncaught `PyriteError` for every command (today:
  a traceback, exit 1) or only for `Outcome` commands? Recommended: only
  migrated ones in slice 1 (no behaviour change for the rest).
- `_task_error` emits `NOT_FOUND`; the class says `ENTRY_NOT_FOUND`. Keep
  `NOT_FOUND` in slice 1 via `cli_error_from` plus the one-line override
  `docs/json-contracts.md` already documents for CLI reads, or move to the
  class code now? #610 owns the spelling; recommended: keep, and let #610
  change it once at the root.
- A rich renderer: a function `(console, data) -> None` on the outcome, or a
  default key/value renderer when none is given? Try both on
  `task get`, the widest rich output in the module.
- `mcp-setup` already reports per-client and exits 1 unless every client is
  ok; by the batch rule a mixed result is 3. Which is right is a json-contracts
  question for slice 6.

### 6. Pointers

- `pyrite/utils/errors.py` (`PyriteCLIGroup`, `cli_error_from`,
  `exit_unless_whole`): where the rule lives today and where it moves.
- `pyrite/cli/mcp_setup_command.py:1078-1090`: the shared `--format` option
  as #303 wants it (JSON default, `PYRITE_FORMAT`, no `-f`,
  `validate_output_format`).
- `pyrite/cli/output.py` (`validate_output_format`, `format_output`).
- `tests/test_requested_effect_exit_code.py:1000-1060`: the registry walk to
  reuse; `TestTasks` (l.576) is slice 1's behaviour bar.
- `tests/test_storage_invariants.py`: a Hypothesis property test to model
  the emitter's mapping tests on.
- `kb/components/cli-system.md`, `kb/components/format-system.md`.
- `tests/test_cli_errors.py`, `tests/test_task_cli_get.py`,
  `tests/test_task_cli_create_fields.py`,
  `tests/test_task_cli_create_title.py`,
  `tests/test_task_commands_registered_kbs.py`: what pins `task` today.

### 7. Checked versus assumed

Checked: the counts above (grep over the 28 modules and 27 service files);
the registry (195 commands, the per-module classification and format
options, by importing #779's `_commands()` and walking each command's
params); that a leaf's return value reaches `PyriteCLIGroup.invoke` through
a nested sub-app (a 15-line Typer probe, not a test); #660 still present at
`kb_commands.py:207-227`; `read_cli` does not use the group; the open issues
named; #303's decision and that no PR has built it.

Assumed: that no command depends on Click discarding its return value (none
returns a value today that I saw, but I did not read all 195); that the
rich renderers can move out without changing their text; the per-slice
line counts.

**This groom is wrong if** a command's effect or exit cannot be expressed
as done/partial/nothing plus a raised refusal (the check commands are the
candidates), or the root group does not see a leaf's return value under
`CliRunner` or the `pyrite-admin` reuse paths.

Footprint (predicted, scored after merge): see each block. In total about
195 commands across 28 modules, ~4,000 lines changed, roughly half of it
deleted format branches and exit calls, in 11 slices. Not a spike: the one
unverified tool behaviour was probed, no storage format or guest state is
touched.

**Folding in the reports.**

- #786 (`extension install --verify`): separate. The exit code follows the
  verdict; the verdict is wrong (in-process discovery). Fix first on its
  own; slice 5 sequences after it (same file).
- #787 (`promote-claim`, `start` echo an error dict and exit 0): fix first
  on its own (two sites, journalism `cli.py:528,1031`); slice 9 makes the
  class impossible for that module.
- #788 (services swallow; four misclassified commands): the service half is
  option (b), not this rule; fix the two named services first on their own.
  The four classifications are made true by slices 6 and 10, by what each
  command writes.
- #660 (`kb discover --add` in JSON adds nothing): same family; a bug with a
  one-line fix (do the effect before the format branch). Fix first on its
  own; slice 4 removes the shape.
- #547, #553 (every `--format` validated, declared once): folded into slice 1
  (the shared option) and applied per module by slices 2-10.
- #611 part 1 (JSON through `console.print`): its own fix is groomed after
  #574; slice 6 removes the shape.
- #303 (one default format): decision below.

---

### Slice 1: the outcome type, the root renders it, `task` migrated

```
Acceptance:   A task_commands command returns an Outcome and never calls typer.Exit, cli_error*,
              exit_unless_whole or format_output; PyriteCLIGroup.invoke renders it and exits
              0/3/1; a raised PyriteError from an Outcome command is rendered via cli_error_from in
              the requested format; pyrite-read uses PyriteCLIGroup. Property test (Hypothesis):
              Outcome.from_counts(done, failed) agrees with exit_unless_whole for all counts, and
              the exit for each kind is the json-contracts table. TestTasks and every task test
              pass unchanged. Registry test: every registered command is annotated -> Outcome or
              its module is in a frozen LEGACY_MODULES set (27 today); a test that fails today:
              "pyrite task claim's callback returns an Outcome".
Regimes:      batch with zero items (decompose with no --child is a usage error, 2); every child
              refused (nothing, 1); some refused (partial, 3); lost claim (nothing, 1, payload as
              today); KB missing (KB_NOT_FOUND, 1, in json and rich); usage errors stay 1 in this
              slice and are listed for the #303 pass.
Touches:      existing: pyrite/utils/errors.py, pyrite/read_cli.py, pyrite/cli/task_commands.py,
              pyrite/cli/output.py, tests/test_requested_effect_exit_code.py,
              docs/json-contracts.md (exit-code section names the one point)
              new: pyrite/cli/outcome.py, tests/test_cli_outcome.py
              ~650 lines: outcome.py ~120, errors.py +50, task_commands ~-150/+120,
              test_cli_outcome ~220, registry test +50, docs ~15
Sequence:     first; after ADR-0046 is read (it may stay proposed for core-only use)
Model:        opus
heavy:        yes (TestTasks runs subprocesses)
Cold read:    yes (public CLI shape, plugin-facing type)
Out of scope: changing task's default format or -f (the #303 decision); MCP/REST claim shapes
              (checked: they pass the service dict through); NOT_FOUND vs ENTRY_NOT_FOUND (#610).
Decision:     #303 folded here or not (below).
```

LEGACY is a set of module paths, one per line, that a slice never needs to
edit: a migrated module is detected by its annotations, and a stale entry is
harmless until slice 11 deletes the set. That keeps slices 2-10 from all
editing the same lines of one test file.

### Slice 2: the write path (`entry_commands`, `link_commands`)

```
Acceptance:   14 commands return Outcomes; _cli_error and _refusal_exit in entry_commands are
              deleted; link's hand-raised Exit(code=2) become BadParameter; every row of the
              json-contracts write-refusal table still reproduces (tests/test_doc_agent_contracts.py).
              create --link with a dangling link is partial (3) with its finish-up command printed,
              as TestCreateWithFailedLink pins.
Regimes:      create/add/update/delete/link/rename refusals by class code; links bulk-create batch.
Touches:      existing: pyrite/cli/entry_commands.py, pyrite/cli/link_commands.py   ~450 lines
Sequence:     after slice 1
Model:        sonnet
heavy:        no
Cold read:    yes (write refusals are the agent-facing contract)
Out of scope: the write pipeline itself (KBService), unchanged.
```

### Slice 3: reads (`browse_commands`, `search_commands`, `read_cli`, `collection_commands`)

```
Acceptance:   17 commands return Outcomes; browse's _cli_error deleted; backlinks on an absent
              subject is a raised not-found in both CLIs (TestBacklinks); batch-read and
              search --files keep 3/1.
Regimes:      empty result is done (0), absent subject is a refusal (1); index lag (principle 5).
Touches:      existing: pyrite/cli/browse_commands.py, pyrite/cli/search_commands.py,
              pyrite/read_cli.py, pyrite/cli/collection_commands.py   ~400 lines
Sequence:     after slice 1; independent of 2
Model:        sonnet
heavy:        no
Cold read:    no
Out of scope: #667 (--fields in csv), a search-shape question.
```

### Slice 4: KBs and registry (`kb_commands`, `repo_commands`, `ids_commands`, `init_command`)

```
Acceptance:   24 commands return Outcomes; _kb_error deleted; kb discover --add registers in every
              format (the #660 shape gone; a failed registration is counted, not passed);
              repo sync and init keep 3/1; ids missing/pin and kb validate exit per the check-command
              decision below.
Regimes:      registry rows vs kb.yaml (the operator's file, ADR-0039): unchanged writes.
Touches:      existing: pyrite/cli/kb_commands.py, pyrite/cli/repo_commands.py,
              pyrite/cli/ids_commands.py, pyrite/cli/init_command.py   ~500 lines
Sequence:     after slice 1 and after #660's own fix (same function)
Model:        sonnet if the check-command decision is made first, else opus
heavy:        no
Cold read:    no
Decision:     check commands' exit codes (below).
```

### Slice 5: index, schema, extension, export, db, protocol

```
Acceptance:   20 commands return Outcomes; index embed and schema migrate keep 3/1; export's nine
              hand exits go; extension install --verify keeps 3 for a plugin that will not load.
Regimes:      embedding owed vs failed (ADR-0035): owed is done.
Touches:      existing: pyrite/cli/index_commands.py, pyrite/cli/extension_commands.py,
              pyrite/cli/schema_commands.py, pyrite/cli/export_commands.py,
              pyrite/cli/db_commands.py, pyrite/cli/protocol_commands.py   ~450 lines
Sequence:     after slice 1 and after #786 (extension_commands.py)
Model:        sonnet
heavy:        no
Cold read:    no
Out of scope: export's file --format (an input/output file format, not the output option).
```

### Slice 6: the root module, `qa`, `mcp-setup`

```
Acceptance:   25 commands return Outcomes; _cli_err deleted; qa stale/compact print one JSON document;
              qa assess and the other qa writers classified by what they write (#788); mcp-setup's
              stop/settle report becomes an Outcome with the same per-client body.
Regimes:      mcp-setup: a client that ends stopped while another changed (1 or 3: decide in-slice
              against json-contracts, with the maintainer if it changes documented behaviour).
Touches:      existing: pyrite/cli/__init__.py, pyrite/cli/qa_commands.py,
              pyrite/cli/mcp_setup_command.py   ~500 lines
Sequence:     after slice 1, #788's service fixes and #611
Model:        opus (mcp-setup's never-neither recovery must survive the move)
heavy:        no
Cold read:    yes (mcp-setup writes another program's config: the guest standard applies)
```

### Slice 7: `pyrite-admin`

```
Acceptance:   27 commands return Outcomes; index health exits 1 when unhealthy, repo
              sync/unsubscribe keep 3/1; the hidden mcp-setup stub keeps COMMAND_MOVED.
Touches:      existing: pyrite/admin_cli.py   ~400 lines
Sequence:     after slice 1
Model:        sonnet
heavy:        no
Cold read:    no
```

### Slices 8-10: plugin CLIs

```
Acceptance:   each plugin's commands return Outcomes, imported from the contract module ADR-0046
              names; journalism's and cascade's --json flags read by the root as the output format
              (or replaced by the shared option under #303); promote-claim and start (#787) cannot
              print an error and exit 0.
Touches:      8: extensions/software-kb/src/pyrite_software_kb/cli.py (23 cmds, ~400 lines)
              9: extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py
                 (18 cmds, ~400 lines)
              10: cascade, social, encyclopedia, zettelkasten cli.py (17 cmds, ~300 lines)
Sequence:     after ADR-0046 is accepted (it amends the plugin contract); 9 after #787;
              10 corrects #788's three cascade classifications
Model:        sonnet
heavy:        no
Cold read:    8: no; 9: no; 10: no (one cold read of slice 8 for the plugin-facing pattern)
```

### Slice 11: delete the scaffolding

```
Acceptance:   LEGACY_MODULES empty and deleted; GUARDED, SAFE, READ_ONLY, HANDLERS, PRINTS and
              EXIT_CALLS deleted, the behaviour classes kept; exit_unless_whole either gone or a
              thin alias of Outcome.from_counts for plugins for one minor; docs/json-contracts.md
              "Exit codes (CLI)" states the rule once, without the per-command list; the
              "--format defaults" table becomes one line if #303 was folded.
Touches:      existing: tests/test_requested_effect_exit_code.py, pyrite/utils/errors.py,
              docs/json-contracts.md, kb/components/cli-system.md   ~-900/+80 lines
Sequence:     after slices 2-10
Model:        sonnet
heavy:        yes (the subprocess behaviour classes run)
Cold read:    no
```

### Needs a decision first (the maintainer's)

1. **Accept ADR-0046** (draft, this branch). Slice 1 can be built while it is
   proposed, since it is core-only; slices 8-10 change the plugin contract and
   wait for acceptance. Recommendation: accept after slice 1's cold read, so
   the decision is made on running code. Counter-case: accepting first stops
   slice 1 from drifting from the ADR.
2. **#303 folded in or kept separate.** Recommendation: fold. Slice 1 declares
   the shared `--format` option once (JSON default, `PYRITE_FORMAT`, no `-f`,
   validated), and each migration slice applies it to its module; #303,
   #547 and #553 close with slice 11. Otherwise every CLI module is swept
   twice, and the second sweep re-touches every format branch the first
   removed. Counter-case: a refactor slice with a behaviour change is harder
   to review, and `task`'s rich default changing under agents that use it is
   visible; the alternative is #303 first, as its own sweep, then this.
3. **What a check command's exit means.** Today `kb validate` drift is 2
   (usage's code), `ids missing` is 3, `index health`/`qa validate` are 1.
   Recommendation: a check is clean 0 or failed 1; warnings never change the
   exit; `kb validate` drift becomes 0 with warnings, and `ids missing`
   moves to 1 (`docs/pinning-entry-ids.md` runs it with `expect-exit: 3`,
   so the doc and its test change too). Counter-case: 2 and 3 already ship and scripts may branch on
   them; keep them and document check commands as an exception.
4. **The JSON `outcome` key** (ADR-0046's first open question). Recommended
   yes, additive, so `decompose`'s `"decomposed": true` with every child
   refused is no longer the only thing a JSON reader sees. Not needed for
   slice 1.

## Groom 2026-10-10 (Andon #822)

Spike, time-boxed to one run, against #793 at 5fca819e (Typer 0.27.3,
Python 3.12, macOS). **This replaces slice 1's acceptance for the guard**
("Registry test ... a module not on the list may not call ..."). The rest of
slice 1's acceptance stands. ADR-0046 has the model ("Enforcement point",
decisions 6 to 8, proposed).

**Answer: buildable.** A guard in `PyriteCLIGroup.invoke`, armed by the
shared `--format` option when the command's body is entered, answered
`NO_OUTCOME` (exit 1) for 16 of the 18 shapes the delta read named, through
the real `pyrite` root, with every existing task test green. The code was
thrown away; nothing below is a patch.

### The design that survived

The root decides; the shared option tells it when the body starts. The root
alone cannot do it: `--help` (`Exit(0)`), an unknown option (`UsageError`)
and a body's own `raise typer.Exit(0)` reach `invoke` as the same exception
types, and a legacy command that prompts or streams must not have its stdout
held back. The shared option's callback is the one thing every command on the
contract already runs, and it has the leaf's context, so it marks body entry
there; the root starts watching stdout at that moment and makes every
decision. Alternatives tried or weighed:

- **`sys.stdout` replacement only** (`contextlib.redirect_stdout`): 0.9
  microseconds, but misses five planted shapes (`os.write(1, ...)`, a child
  process, `sys.__stdout__`, `/dev/stdout`, `Console(file=sys.stdout)` made
  at import). Lost to replacing `sys.stdout` and pointing descriptor 1 at a
  temporary file: 119 microseconds per command, all five caught.
- **The command never receives a console**: already true on the branch (the
  renderer gets the root's), and it stops nothing, since `print` needs none.
- **A command class per call site** (`cls=`): a rule kept call site by call
  site (design principle 8). Not built.
- **Run the body in a child process**: the only thing that would see
  `os._exit`. Not built; the cost is a process per command.

### Shapes, through the real root in a real process (`--format json`)

| Shape planted in a migrated command | Result |
|---|---|
| `raise _Done(0)` with `from typer import Exit as _Done` | caught |
| `sys.exit` through an alias, and through `getattr` | caught |
| `ctx.abort()`, `ctx.exit(0)`, `raise SystemExit(0)` | caught |
| a `ClickException` subclass with `exit_code = 7` (Typer's click and PyPI's) | caught |
| `ctx.fail()`, `typer.BadParameter` from the body | caught under the strict rule; exit 2 as a usage error under the lenient one (both run) |
| `KeyboardInterrupt` | allowed: exit 130; a person's Ctrl-C looks the same |
| `print`, `typer.echo`, `sys.stdout.write`, `json.dump(..., sys.stdout)` | caught |
| `console.log` / `.out` / `.rule` / `.print` on a module-level `Console()` | caught (Rich resolves `sys.stdout` at write time) |
| `os.write(1, ...)`, `subprocess.run(['echo', ...])` | caught, by the descriptor swap only |
| a renderer called from the body (alone, or followed by `raise _Done(0)`) | caught |
| a `_render_*` helper printing a failure, called from the body | caught |
| the same helper passed as `rich=` on a `done` outcome | **not caught**: the root cannot judge a renderer's words; kind, exit code and machine formats are still the root's |
| a red failure on `Console(stderr=True)`, then a normal return | **not caught**: stderr is open (warnings, progress) |
| the same, then a `ClickException` with its own code | caught (by the exception) |
| an `Outcome` subclass overriding `exit_code` | caught (exact type) |
| a renderer that raises, or calls `sys.exit(0)` (`--format rich`) | caught; text it printed first stays above the `NO_OUTCOME` line |
| a return of `None`, a dict, `0`; a `KeyError` | caught |

Of the 18 named: 16 caught, 1 not (stderr failure then a normal return), 1
allowed (`KeyboardInterrupt`). Found beyond the list, **not caught**:
`os._exit(0)` (exit 0, empty stdout), an `atexit` handler that prints, a
non-daemon thread that prints after the command returns. All three act after
`invoke` has returned. Not run: a write to `/dev/tty`.

### The behavioural acceptance test (replaces the scan as the guard)

- It walks the registry: every leaf of `pyrite`, `pyrite-admin` and
  `pyrite-read` that declares the shared option (10 of 161 leaves of `pyrite`
  today; 83 have some `--format`). A slice adds no case by hand; a migrated
  command is in the walk because it declares the option.
- For each such command and each class of way out, it replaces the command's
  body with the planted shape, runs the **real root**, and expects exit 1 and
  `NO_OUTCOME` (the only document on stdout in JSON). Classes, at least: an
  exit (`Exit`, `SystemExit`, `Abort`, a `ClickException` with a code), a
  crash, a write to `sys.stdout`, a write to descriptor 1, a child process, a
  wrong return (`None`, a dict, an `Outcome` subclass), a renderer that
  raises, a renderer that exits. Controls: a returned `Outcome` renders and
  exits by its kind; a `PyriteError` is a refusal; `--help` exits 0; an
  unknown option exits 2; `KeyboardInterrupt` exits 130.
- The prototype: 10 commands x 14 classes x 2 formats plus 30 renderer cases,
  271 cases, 5.1 s in-process under `CliRunner`. 251 passed. The 20 that did
  not are one class, a `Console(file=sys.stdout)` made at import: under
  pytest the stream captured at import is pytest's own, not descriptor 1.
  That class is caught in a real process, so it gets one subprocess test.
- A real command that goes around the root now fails its own functional
  tests with `NO_OUTCOME`, and fails the same way for a user on a path no
  test covers. That is the difference from the scan: it fails closed.

Three things the prototype had to learn, which the worker will meet:

1. **Typer builds a new click tree on every call.** A body planted on the
   click command the test walked is not the body that runs. Plant on the
   Typer registration (`registered_commands[i].callback`), keeping the
   signature (`functools.wraps`), since Typer reads the parameters from it.
2. **Typer wraps option callbacks.** `param.callback is _record_format` is
   false for every command; the original is at `__wrapped__`. "Declares the
   shared option" needs one helper, used by the root and by the test.
3. **`typer.testing.CliRunner` takes the Typer app, not a click command.**

### What the scan is still for

A hint, named as one, for what no check in the process can see: `os._exit`,
`atexit.register`, a thread started by a command, a failure written to
stderr before a normal return. Its tests stop asserting "a migrated module
cannot ..."; a shape it misses is no longer a finding against the property.
It may also go: the registry test (`-> Outcome`, the shared option, the
legacy mark) and the behavioural walk carry the rule without it. Slice 11
deletes it with the rest of the scaffolding either way.

### Measured footprint of the guard

| | |
|---|---|
| Spike diff | 2 files, +129 / -20: `pyrite/utils/errors.py` +109 / -20, `pyrite/cli/output.py` +20 |
| Of which | stdout watch about 45 lines, `invoke` 28 lines to about 50, the `NO_OUTCOME` helper 10, arming in the shared option 14 |
| Cost per command on the contract | 119 microseconds (0.9 without the descriptor swap) |
| Legacy commands | untouched: one unused object per run; `tests/test_requested_effect_exit_code.py` 279 passed with the guard in |
| Existing tests with the guard in | `tests/test_task_cli_outcome.py` + `tests/test_cli_outcome.py` 34 passed; `TestTasks` 7 passed (real processes); the same under the strict usage rule |
| Real process, checked by hand | `task --help` and `task list --help` exit 0; `--format xml`, an unknown option, a missing argument exit 2; `-f json` warns on stderr and prints JSON; `--version`; stdout closed (`>&-`) exits 0 as on the branch; a pipe |

A worker's footprint for round 2: `pyrite/utils/errors.py`,
`pyrite/cli/output.py`, `pyrite/cli/outcome.py` (if rich rendering is made
all-or-nothing), `tests/test_cli_outcome.py` (the walk),
`tests/test_requested_effect_exit_code.py` (the scan demoted),
`docs/json-contracts.md`, `kb/components/cli-system.md`, and ADR-0046 rebased
onto the amendment (the PR edits decisions 3 and 4; the amendment is appended
after "Consequences", so the two do not overlap). Model: opus (the last round
before the breaker, and the option callback and the root share one
invariant). Cold read: yes.

### Surprises

- **Typer 0.27.3 vendors click** (`typer._click`). `click.ClickException`
  from the PyPI package is an unrelated class: Typer's `main` does not treat
  it as a click error, and `typer.Exit` is not `click.exceptions.Exit`.
  `pyproject.toml` allows `typer>=0.12.3`, where they are the same classes.
  The allow-list's "usage error" has to be the class Typer runs, found
  without a private import path. ADR-0046's context says "checked on Typer
  0.24.1".
- **Prompts cannot pass.** `typer.confirm("...")` writes to stdout, and with
  `err=True` `input()` still writes one space there. No task command
  prompts; ten sites in six other CLI modules do.
- **A crash loses its traceback** on a migrated command: one `NO_OUTCOME`
  line names the exception, the traceback is at DEBUG.
- **Everything the body's children write is the body's.** A child process
  that inherits stdout (git, pip) is caught. Nine `subprocess` calls in four
  CLI modules; a grep suggests each passes `capture_output` or `stdout=`,
  not checked one by one.
- **The body can re-enter.** `ctx.invoke(other_command)` from a body passes
  through the same hook; starting the watch twice in the throwaway code
  would have lost the saved stream. The watch must start once.
- Close callbacks (`ctx.call_on_close`) run inside the root's `invoke`, so a
  print there is caught; `atexit` is not.

### What round 2 of #793 must do (properties, not a patch)

1. For a command that declares the shared option, from body entry until the
   root has decided, the only outcomes are: the root renders a value of
   exact type `Outcome`; the root renders a refusal for `PyriteError` (and
   `ValueError`, transitional); `KeyboardInterrupt` passes; everything else
   is `NO_OUTCOME`, exit 1, in the format asked for. No `except` clause in
   `invoke` lists what is forbidden; it lists what is allowed.
2. Nothing the body wrote to stdout reaches stdout. It reaches stderr, and
   the answer is `NO_OUTCOME`. Holds for `sys.stdout` and for descriptor 1,
   under `CliRunner` and in a real process, and when descriptor 1 cannot be
   duplicated the `sys.stdout` half still holds.
3. Rendering is inside the guard. In a machine format stdout holds exactly
   one document whatever the renderer or the payload does.
4. Before body entry nothing changes: `--help`, `--version`, usage errors,
   an unknown format. For a command that does not declare the shared option
   nothing changes at all, and its stdout is never held back.
5. The exit code of a rendered outcome comes from the one table, read by the
   root, never from an attribute a subclass or an instance can replace.
6. The acceptance test is the registry walk above, with the controls. It is
   green with no per-command list in it.
7. Every sentence in `PyriteCLIGroup`'s docstring, `docs/json-contracts.md`,
   `kb/components/cli-system.md` and `pyrite/cli/outcome.py` about what a
   command cannot do is one the walk proves, or it names its exception
   (stderr, a renderer's words, `os._exit`, `atexit`, threads, prompts).
8. The scan's tests are marked as a hint or removed; none is the evidence
   for properties 1 to 5.

The delta read's other findings (the `repr` under `--format markdown|csv`,
`Outcome.partial` overwriting a payload's own `outcome` key, the filtered
input in `tests/test_cli_outcome.py:69-80`, the legacy mark) are not part of
the guard and still stand for round 2.

### What changes for slices 2 to 11

- **No slice writes a guard test.** Declaring the shared option puts a
  command in the walk. A slice's "no raw `typer.Exit`" acceptance line is
  replaced by: the module's commands are in the walk and the walk is green.
  102 `typer.Exit` sites in 22 CLI modules remain to be removed by moving
  the result into an `Outcome`, not by hiding them from a scan.
- **Prompts** (slices 2, 4, 5, 6, 7: `entry delete`, `kb remove`, `repo`,
  `extension uninstall`, the auth commands): a command that prompts cannot
  declare the shared option until the maintainer picks a rule (ADR-0046,
  open questions). Until then those commands stay on the legacy list and the
  slice says so.
- **Progress** (slice 5: `index_commands.py`, two Rich `Progress`/`status`
  uses): on a stderr console, or the command stays legacy.
- **Module-level `console = Console()`** (23 CLI modules): caught without
  change, since Rich resolves `sys.stdout` when it writes.
- **Commands that stream or never return** (a server, a watcher): not
  surveyed here. They cannot return one `Outcome`; they stay off the shared
  option, and the slice that meets one lists it.
- **Slices 8 to 10 (plugins):** a plugin command that declares the shared
  option gets the guard; one that returns `None` without it is untouched.
  The plugin contract (ADR-0040) should say that declaring the option is
  what opts in.
- **Slice 11:** deletes the scan with the tables, or keeps it as the named
  hint.

### Checked versus assumed

Checked by running: every row of the shapes table (real process, JSON; the
renderer rows also in rich), the walk under `CliRunner`, the three test
files, the timings, the help and usage paths. Assumed, not run: Windows
(`os.dup2` on descriptor 1); Python 3.11 and 3.13; Typer below 0.27; a real
terminal (colour and width of the rich rendering were not compared, though
rendering happens after the watch ends, on the real stdout); making rich
rendering all-or-nothing by rendering to a buffer; `pyrite-admin` and
`pyrite-read` roots (no command of theirs is on the contract yet); the
strict usage rule against commands outside `task`.

## Build notes, round 2 of #793 (2026-10-10)

What building the guard found that the groom above does not say. Slices 2 to
11 should read this with `kb/components/cli-system.md` ("Outcomes") and
`PyriteCLIGroup`'s docstring, which lists what the guard does not cover.

- **The shared option's callback is not body entry.** Click calls it while it
  is still parsing: with `--format json` given and a required option missing,
  the callback runs and `MissingParameter` is raised after it. A watch
  started there answers `NO_OUTCOME` for a usage error. The callback hands
  the root's guard the leaf context, and the guard wraps `ctx.command.callback`
  (`_BodyGuard.watch_body_of`, `pyrite/utils/errors.py`); the wrapper is body
  entry. Typer builds a new click tree per call, so the wrap lasts one run.
- **Buffers are the other half of descriptor 1.** Text written before the
  body is still in the stream's buffer when the watch starts (into a pipe),
  and would be taken for the body's: a false `NO_OUTCOME`. Text the body
  wrote through a stream kept from import, `sys.__stdout__` or C's `printf`
  is still in a buffer when the body returns, and would land after the
  root's document. `_StdoutWatch` flushes the Python streams and C stdio
  before it starts and before it stops. The spike's table has
  `sys.__stdout__` as "caught by the descriptor swap"; unflushed, it was not.
- **A group's `result_callback` sits between the command and the root** and
  can swap `nothing` for `done`. The root renders only the object the body
  returned (`guard.returned`).
- **The format is read when the body starts.** A body that rewrote
  `ctx.meta` could otherwise be rendered by its own renderer when JSON was
  asked for.
- **A parameter callback runs unwatched** (it is how `--help` works). The
  registry test allows a command on the contract no callback but the shared
  option's. A custom parameter type or a default factory is the same class
  and is not checked.
- **An Outcome's data is a mapping or `None`, with no `outcome` key.** A
  slice whose command returns a list puts it under a key
  (`{"entries": [...]}`), as the task commands already do.
- **The shared option takes `json`, `yaml`, `rich`.** `markdown` and `csv`
  printed a Python `repr` for a task payload. Slice 3 (`search`, the browse
  commands) has data those formats were written for; it needs a way for a
  command to offer them (the Outcome naming a table, or the option taking a
  per-command set). Not designed here.
- **Not caught, found by probing beyond the groom's list:** a child the
  command forks (it returns through the root too: two documents), a write to
  the root's own saved duplicate of descriptor 1 or one taken at import, a
  buffer of the command's own over descriptor 1 flushed at exit, and a
  command that reaches into the guard (`ctx.meta`, `EXIT_CODES`). All named
  in `PyriteCLIGroup`'s docstring.
- **Numbers.** 10 commands on the contract and 47 ways out: 43 under
  `CliRunner` in `json` and `rich` (810 cases), 4 that need the process's
  own stdout in a real process. With the controls `tests/test_cli_outcome.py`
  is about 1,000 tests in about 20 s at `-n 4`. Against the round-1 root,
  660 of the walk's first 720 cases were red.

## Groom 2026-10-13: formats a migrated read command offers

Groomed by the architect on `dev` at 90360bff. Read-only for code; no suite,
server or browser. Every behaviour below was run through the real `pyrite`
and `pyrite-read` against a throwaway KB (`PYRITE_CONFIG_DIR` in a scratch
directory), or is cited by file:line. **This replaces the "### Slice 3:
reads" box above.** Slice 3 as written is not dispatchable: it removes two
formats from commands whose docs teach them. Slice 3a below is dispatchable
now; 3b and 3c wait for decision 1 (and 3c for decision 2).

**Principles.** 8: which formats a command prints, and with which columns,
is decided today in three places that do not know each other (the option's
help string, the command's branch, a serialiser that guesses). 9: 48
commands advertise `markdown, csv`; the serialisers know six payload keys.
3 ("reports it"): a format that prints part of the answer has not reported
it.

**The user's model.** "`--format` changes how the answer looks, never what
the answer is. If `--help` offers csv, I get this command's rows, all of
them, with the columns its JSON has. If the command cannot print a format,
it says so before it does anything."

**The implementation model.** The command declares the shape of its answer
(a payload; a table of rows under a named key with named columns; one
entry). The shared option offers exactly the formats the root can print for
that shape, so the offer is true by construction and an unoffered format is
click's usage error. The root writes every byte of csv and markdown from the
declared shape. No serialiser guesses a shape, and no command reads its
format.

**Where the two differ today** (the finding). `csv_serialize` and
`markdown_serialize` pick a shape by which key the payload happens to hold
(`pyrite/formats/csv_fmt.py:14-23`, `markdown_fmt.py:13-25`) and print fixed
columns for it. So: `get --format csv` prints the line `tag,count` and
nothing else (an entry has a `tags` key); `backlinks --format csv`, the one
csv command the tutorial runs, drops `relation` and `forward_relation`;
`--fields` is ignored (#667); every other payload prints a Python `repr`.
Slice 1 answered this for `task` by refusing both formats, which was right
there (nothing real was lost). For reads it removes the cases that work. The
question is not "may a command offer more formats" but "who knows the
columns": today the serialiser guesses; it must be the command that says and
the root that prints.

**One of several.** Same root cause: #667 (`--fields` ignored in csv; open
PR #721), #666 (`sw adrs --format csv` prints a rich table), #547 (formats
not validated), #553. Option R below gives #666 its csv in slice 8 with no
csv code in the plugin, and closes #547 for each migrated command.

### 1. Contracts that apply

- ADR-0046 decision 1: an Outcome "carries the result data and a renderer
  for `--format rich`". A table on the Outcome extends a plugin-facing type:
  an amendment, the maintainer's (decision 1 below).
- ADR-0046 decision 4: a legacy command is "counted, per module, in a table
  that must match the registry exactly". Binding on the fix for the shared
  test lines: the count stays hand-kept and exact; only where it is kept may
  change.
- ADR-0046 decision 6 and `docs/json-contracts.md:565-589`: in a machine
  format stdout is exactly one document; an unknown format is exit 2 before
  the body.
- #303 (maintainer, 2026-10-02) and ADR-0046 "Questions decided": JSON is
  the default of every command; `PYRITE_FORMAT` is how a person changes it.
  No per-command default.
- ADR-0010 §6: `search --format csv` and `get --format markdown` are its two
  CLI examples. §1: csv is "for list/search endpoints only".
- `docs/json-contracts.md:650-653`: "`markdown` and `csv` are not offered by
  the shared option". True of `task`; the sentence changes under any option
  but (c).
- ADR-0040 "CLI helpers" row: what a plugin command may import. Gains
  whatever the amendment names.
- Written nowhere: what a refusal looks like under `--format csv`; what
  `PYRITE_FORMAT` does when it names a format the command does not offer;
  which columns a command's csv has. Findings; see open questions.

### 2. What the code does today

Reproduction of the reported problem, one command each: `pyrite task list -k
kbx --format csv` exits 2 (the shared option); `pyrite backlinks beta-note
-k kbx --format csv` exits 0 with rows (legacy). Migrating the second as
slice 3 was written makes it the first.

Surprises first.

- **`pyrite-read` does not default to rich everywhere.** Five of its seven
  commands do (`get`, `list`, `timeline`, `tags`, `backlinks`:
  `pyrite/read_cli.py:79,132,187,242,288`). `pyrite-read search` is
  `search_commands.py`'s function registered a second time
  (`read_cli.py:15`, `register_search_command`) and defaults to `json`.
  `config` has no `--format`.
- **No test pins a CLI csv or markdown shape.** `tests/test_formats.py:404-
  466` (`TestApiContentNegotiation`) pins REST `Accept` negotiation, not the
  CLI. The only CLI tests naming csv are `tests/test_cli_outcome.py:261,668`,
  which pin that the shared option refuses it.
- **One executable doc uses csv, three prose lines use markdown.**
  `docs/tutorials/pyrite-in-20-minutes.md:311` runs `pyrite backlinks
  wip-constraints -k reinertsen --format csv` with `expect-text
  wip-research` (CI: `.github/workflows/ci.yml:654`). Lines 133, 374, 375
  teach `pyrite get <id> -k pyrite --format markdown` in prose (not run).
  `docs/getting-started.md` and `docs/json-contracts.md` teach neither.
- **`get --format markdown` is not the file.** It prints `id`, `title`,
  `kb_name`, `date`, `importance`, `tags` and the body
  (`markdown_fmt.py:28-41`). It reads the key `type`; the payload's key is
  `entry_type`, so the type line never prints. Fields under `metadata` are
  dropped. Run: an entry with `outcome: dismissed` and `custom_thing: 7`
  printed neither.
- **The empty set prints rich text in every format.** `timeline` (both
  CLIs) and `collections list` print `No timeline events found.` / `No
  collections found.` under `--format json`, exit 0, before the format is
  looked at (`browse_commands.py:408-410`, `read_cli.py:201-203`,
  `collection_commands.py:27-29`). With `--format xml` they also exit 0.
- **`search --files` ignores `--format`**: a Rich table under `csv` and
  `json` (`_search_files`, `search_commands.py:330`, takes no format).
- **An unknown format has five answers.** Exit 2 on `search`, `tags`,
  `backlinks`, `get` (the four with `validate_output_format`); a traceback
  and exit 1 on `list-entries`, `batch-read`, `orient`, `recent`,
  `collections query`; `{"error": "Unknown format: xml"}` exit 1 on
  `pyrite-read get|list|tags|backlinks`; exit 0 on the empty-set paths
  above; ignored by `update` (prints JSON whatever was asked).
- **The two `backlinks` refusals differ.** `pyrite backlinks nope` prints
  `{error, error_code: ENTRY_NOT_FOUND, retryable}`; `pyrite-read backlinks
  nope` omits `retryable` (`read_cli.py:38-50`, its own `_emit_error`).
  Both exit 1, and both print that JSON under `--format csv`.
- **Already right on the legacy path:** `backlinks` on an absent subject (1,
  `ENTRY_NOT_FOUND`), `batch-read` with a missing id (3, rows printed).
  Nothing in slice 3's old acceptance needs a fix that can go first.
- **`PYRITE_FORMAT`**: `PYRITE_FORMAT=csv pyrite task list` exits 2 (the
  env value is validated like a flag, `output.py:59-60`). A legacy command
  ignores the variable (`PYRITE_FORMAT=rich pyrite tags` prints JSON).

The 18 read commands (17 of slice 3, plus `pyrite get`, which is in slice
2's module), their defaults and what each format prints today. "ok" = rows
or the entry, with the loss noted; "repr" = a Python `repr` or a one-row
dump with `repr` cells; all 17 that have `--format` advertise `json, rich,
markdown, csv, yaml` in help.

| Command | file:line | default | csv | markdown |
|---|---|---|---|---|
| `pyrite get` | `entry_commands.py:179` | json | `tag,count`, no rows | entry, 6 keys + body |
| `pyrite list-entries` | `browse_commands.py:39` | json | ok, 6 fixed columns | titles, no ids |
| `pyrite batch-read` | `:109` | json | ok; `not_found` absent | titles, no ids |
| `pyrite orient` | `:176` | json | repr | repr |
| `pyrite recent` | `:325` | json | ok, 6 fixed columns | titles, no ids |
| `pyrite timeline` | `:383` | json | ok; empty set is rich text | ok; same |
| `pyrite tags` | `:433` | json | ok (`tag,count`) | ok |
| `pyrite backlinks` | `:472` | json | ok; relation columns dropped, `importance` empty | titles, no ids |
| `pyrite search` | `search_commands.py:58` | json | ok, 7 fixed columns; `--fields` ignored; `--files` rich | title + snippet, no ids |
| `pyrite collections list` | `collection_commands.py:14` | json | repr | repr |
| `pyrite collections query` | `:64` | json | ok, 6 fixed columns | titles, no ids |
| `pyrite-read get` | `read_cli.py:75` | rich | `tag,count`, no rows | entry, 6 keys + body |
| `pyrite-read list` | `:130` | rich | ok (`name,type,path,entries`) | ok |
| `pyrite-read timeline` | `:181` | rich | as `pyrite timeline` | same |
| `pyrite-read tags` | `:237` | rich | ok | ok |
| `pyrite-read backlinks` | `:284` | rich | as `pyrite backlinks` | same |
| `pyrite-read search` | `search_commands.py:58` | json | as `pyrite search` | same |
| `pyrite-read config` | `read_cli.py:329` | no `--format` | - | - |

An empty `entries` or `results` list in csv is the empty string, no header
(`csv_fmt.py:38-39,53-54`).

**Slice 2's 14 commands** (asked by the conductor; a worker is on it now).
`create`, `add`, `delete`, `link`, `links bulk-create`: no `--format`.
`rename`: `json or rich`, any other value prints JSON. `update`: advertises
five, prints JSON for all five. `links check|suggest|discover|batch-suggest|
orphans|asymmetric` (`link_commands.py:35,304,432,570,673,765`, default
`rich`): advertise five; markdown is a `repr`, csv is one row with `repr`
cells (run for check, suggest, orphans, asymmetric; discover and
batch-suggest share the code path and were not run to output). **Slice 2
loses nothing real on 13 commands and one real format on one: `pyrite get
--format markdown`**, the form the tutorial teaches three times. Until
decision 1: slice 2 leaves `get` on the legacy path beside `delete` (which
prompts) and says so; 3c migrates `get` with `pyrite-read get`. Not a
format matter but the same slice: `create`, `add`, `link` go from text to
JSON by default, and `docs/json-contracts.md:293,665-668` says "Rich
text, always" (and "Never JSON" for `create`) of them.

Wider than these slices: 48 of the 89 `--format` options in the three CLIs
advertise csv or markdown (walked from the registry; `link` 6, `kb` 5, `qa`
5, `extension` 4, `cli/__init__` 4, `repo` 2, `index` 2, `init` 1,
journalism 1, plus the reads). Outside the reads they are the `repr` case:
slices 4 to 9 lose nothing, as `task` did.

### 3. Invariant and surfaces

**Invariant.** For every command and every format its `--help` offers:
stdout is one document of that format holding the command's whole answer
(every row the JSON holds under the declared key, with declared columns),
and a format it does not offer is a usage error (2) before the body runs.
Which formats a command offers follows from the shape it declares, in one
place.

- **CLI, `pyrite`**: 11 read commands above; 9 work in csv with losses, 2
  print a `repr`. Not held.
- **CLI, `pyrite-read`**: 7; the same serialisers, a second copy of five
  commands (`read_cli.py` 348 lines), its own error shape. Not held.
- **CLI, `pyrite-admin`**: no read command in these slices; its `repr`
  cases go in slice 7.
- **Plugin CLIs**: software-kb has 15 `--format` options, none advertising
  csv; #666 asks for one. A plugin gets csv only from something it can
  declare; it cannot teach the registry's key sniffing a new shape. In
  scope for the amendment's wording, built in slices 8 to 10.
- **REST**: `negotiate_response` (`pyrite/server/api.py:1028`) uses the
  same serialisers by `Accept`, pinned by `tests/test_formats.py`. Out of
  scope to edit and safe to leave: the root writing CLI tables itself
  removes no serialiser. Read, not ignored: #721 threads `fields=` through
  `negotiate_response` for REST search, and that stays.
- **MCP**: tools return dicts; no format. Not affected (grep: `mcp_server.py`
  does not import `pyrite.formats`' csv or markdown).
- **Docs**: the tutorial line 311 (run in CI) and its three prose lines;
  `docs/json-contracts.md:645-675`; ADR-0010 §6;
  `kb/components/format-system.md`, `cli-system.md:105`.
- **Tests**: `tests/test_cli_outcome.py:261,668` pin the three-format set by
  value; they must come to read the set from the option.
- **Files on disk, shipped schemas, templates**: none. No state Pyrite does
  not own is written.

### 4. Options

**(a) The shared option takes a per-command format set and default.**
`output.py`: `OUTPUT_FORMAT` becomes a factory; `declares_shared_format`
stops comparing by identity (`output.py:124`). `outcome.py`: nothing. The
root still calls `format_response`, so the guessing stays: `get --format
csv` still prints `tag,count`, `backlinks` still drops its relation, and the
`outcome` key the root adds lands in the one-row fallback. #721: no route.
The command no longer calls `format_output`, so `fields=` has nowhere to go
unless the Outcome carries it, which is (b). Plugins: a plugin may offer csv
and gets a `repr`. The per-command default reopens #303. About 40 lines;
smallest change that keeps the formats, and it moves today's bugs onto the
contract.

**(b) The Outcome names a table; the root renders csv and markdown for any
Outcome that has one.** `outcome.py`: a `Table` (the key holding the rows,
the columns). Root: a table writer. Fixes the guessing. Alone it breaks
"unknown format is a usage error before the body": whether an Outcome has a
table is known only after the command ran, so `links bulk-create --format
csv` would write its links and then fail on the format. It needs a static
declaration too.

**(c) Remove markdown and csv from reads.** Nothing in `output.py` or
`outcome.py`. Breaks: the tutorial's line 311 (a CI failure until the page
changes) and its three `get --format markdown` lines; ADR-0010 §6's two
examples; #667, filed because two of three first-hour agents reached for
`search --fields ... --format csv` unprompted; #666; and #721, an outside
contributor's 417-line PR, which becomes REST-only. Smallest by far, and
the only option that shrinks the supported surface (principle 9).

**(R) Recommended: (a) and (b) joined, with the set derived from the shape
instead of chosen per command.**

- `output.py` declares a closed set of option constants, one per shape:
  `OUTPUT_FORMAT` (json, yaml, rich; unchanged), one for a table (adds csv,
  markdown), one for an entry (adds markdown). One default, json. Each has
  its own thin callback so `declares_shared_format` can also answer "which
  shape". No per-command set, no per-command default.
- `outcome.py`: `Outcome` gains an optional table: the data key holding a
  list of mappings, and the column names. `_check` refuses a table whose key
  is absent or not a list of mappings.
- Root (`_outcome_answer`, `pyrite/utils/errors.py:384`): csv is a header
  plus one row per item, written by one writer; markdown is a pipe table
  for a table, the entry document for an entry. A command that declared the
  table option and returned an Outcome without a table is `NO_OUTCOME` in
  every format, so any test of the command finds it.
- The writer is #721's `pyrite/formats/tabular.py` (`projected_columns`,
  `cell_text`, `projected_markdown`) generalised from "`--fields` given" to
  "columns named". `--fields` becomes the table's columns.
- The registry serialisers stay for REST, untouched.

Effect on #721: its `tabular.py` is the writer this needs, and its 272-line
test file is acceptance for 3b. Its call-site edits in `browse_commands.py`
and `search_commands.py` (three more `if fields_list: ... return` branches,
`**kwargs` through `format_output`) are code 3b deletes. Merged first, 3b
builds on it. Merged second, every one of its CLI hunks conflicts with code
that no longer exists. So #721 goes first.

Effect on plugins: a plugin command declares the table option, returns
rows and columns, and has csv and markdown with no format code (#666's csv
in slice 8). `Table` and the option constants join ADR-0040's "CLI helpers"
row. A plugin that returns `None` is untouched.

Size: about 300 lines before any command moves (`output.py` +40,
`outcome.py` +45, `errors.py` +30, `tabular.py` +40 on #721's 58, tests
+150).

**Counter-case, for (c).** The contract is three days old and has three
formats that work for any payload. csv is `jq -r '.entries[] | [.id,
.title] | @csv'` away, and each format offered is one more thing every
future command's author must keep true. Alpha is when removing is cheap.
What decides it for me: agents chose csv unprompted in the first-hour tests
(#667, #666), a table is a fraction of the tokens of 25 JSON lines per hit
(#54), and (R) makes the offer true by construction, which is the cost (c)
is trying to avoid.

**A visible consequence of (R), for the same decision.** CLI list markdown
changes from headings (`# Entries (2 total)` then `## <title>`, which has
no ids, so nothing in it can be acted on) to a pipe table with the declared
columns. Nothing documents or tests the CLI form; REST keeps the headings.
`backlinks` csv gains `relation`, `forward_relation`, `inverse_relation`.
`get --format csv` and `orient|collections list --format csv|markdown`
become exit 2.

### 5. Open questions

- A refusal under `--format csv` or `markdown`. Today: the JSON error shape
  on stdout, exit 1 (run on both `backlinks`). Recommended: keep that, and
  say it in `docs/json-contracts.md`, whose "printed in the format asked
  for" is otherwise false for csv. A one-row csv of `error,error_code` is
  data to a spreadsheet.
- `PYRITE_FORMAT` naming a format this command does not offer. Today exit 2
  (`PYRITE_FORMAT=csv pyrite task list`), so a person who sets
  `PYRITE_FORMAT=markdown` for reading breaks every non-table command.
  Recommended: an environment default the command does not offer falls back
  to json, as `_default_format` already does for errors (`errors.py:210`);
  an explicit `--format` it does not offer stays exit 2. This is a public
  rule; if the worker's evidence says otherwise it goes to the maintainer.
- A partial outcome in csv has nowhere to put `outcome` or `not_found`
  (`batch-read`). Recommended: rows on stdout, exit 3, the rest only in
  json and yaml, documented. Overturn it if a caller needs the misses in
  csv.
- Should `pyrite-read` keep five copies? `search` is already one function
  registered twice. Registering `browse_commands`' functions on
  `pyrite-read` would delete about 250 lines and the second error shape,
  and would add `orient`, `list-entries`, `batch-read`, `recent` to
  `pyrite-read` (additive). `pyrite-read list` (KBs) has no twin in
  `browse_commands`. Explore in 3c; adding commands to a public CLI is a
  note for the maintainer, not a blocker.
- `get --format markdown`: keep today's bytes (root calls the existing
  entry serialiser) or print the file. Recommended: today's bytes in 3c,
  and the loss (no type, no custom fields) filed as its own report against
  design.md's "a single-entry read comes from the index, not the file".
- Default columns per command: today's fixed lists, or the JSON row's keys.
  Recommended: today's lists, plus the relation columns on `backlinks`.

### 6. Pointers

- `pyrite/cli/task_commands.py`: the pattern a migrated command follows.
- `pyrite/cli/output.py:59-126`, `pyrite/utils/errors.py:372-396`: where the
  format is recorded and where a document is built.
- `gh pr diff 721`: `pyrite/formats/tabular.py`,
  `tests/test_fields_every_format.py`; its review of 2026-10-10 lists what
  the PR still owes (rebase, an 80-column case, `get --fields`).
- `tests/outcome_walk.py`, `tests/test_cli_outcome.py:261,668`.
- `scripts/run_tutorial.py` (`expect-text`), the tutorial line 311.
- `tests/test_formats.py:404-466`: the REST shapes that must not move.
- `kb/components/format-system.md`, `kb/components/cli-system.md:105`.

### 7. The shared test lines (point 3 of the brief)

"On the contract" is already derived: `declares_shared_format`
(`output.py:114`), used by the root and by `_on_the_contract`
(`tests/test_requested_effect_exit_code.py:1246`). The walk's `CONTRACT` is
derived from it. Two hand-kept things remain:

- `test_the_task_commands_are_on_the_contract` (`:1341-1345`) asserts
  exactly ten commands, all `pyrite task`, in one module. Every slice must
  rewrite the same two lines. It adds nothing the exact table does not
  already hold.
- `LEGACY_COMMANDS` (`:1213-1240`), one line per module. ADR-0046 decision 4
  requires this count to be hand-kept and exact; that is its point. Slices
  conflict where their modules sit on adjacent lines: 2 with 4 (`kb`,
  `link`), 2 with 5 (`db`, `entry`, `export`), 3 with 5 (`collection`,
  `db`; `schema`, `search`), 3 with 6, 4 with 5, 5 with 6, 6 with 7, among
  others; 2 and 3 do not touch in the table, only in the task test. Checked
  in a scratch repo: two branches editing adjacent lines conflict on
  rebase; one unchanged line between them does not.
- Also shared: `docs/json-contracts.md:566` ("the `task` commands are
  today") and the same phrase at `:657`; `kb/components/cli-system.md:105`.

Fix, inside decision 4: the table becomes one file per module in a
directory beside the test, each holding the names of that module's commands
that are not on the contract, one per line. The registry test compares each
file with the registry, exactly, in both directions. A slice deletes or
shortens only its own modules' files. Names instead of counts also show
which command a slice left behind (`entry_commands: 1` does not say it is
`delete`). `LEGACY_COMMANDS_AT_SLICE_1 = 185` stays and is never edited.
`test_the_task_commands_are_on_the_contract` is deleted. The docs stop
naming migrated modules and give the observable test instead: a command on
the contract shows `PYRITE_FORMAT` on its `--format` in `--help`.

Not recommended: a frozen baseline no slice edits (legacy must be a subset
of the 185 names). No shared lines at all, but a command that drops the
shared option goes back to legacy unseen, and it drops decision 4's "a
slice lowers it", so it would need the ADR changed.

Test that fails today: "for every module, the commands that do not declare
the shared option are exactly the names in that module's legacy file" (no
such files exist). It is a test of scaffolding; the property it protects is
decision 4's.

### 8. Checked versus assumed

Checked by running: every cell of the table in section 2 (16 commands x
csv, markdown, xml, on a KB with two linked notes; `get`, `update`, `rename`
and four `links` commands in csv, markdown, yaml, xml); the empty-set and
`--files` outputs; both `backlinks` refusals; `batch-read` exit 3;
`PYRITE_FORMAT=csv` on `task list`; the registry walk (defaults, callbacks,
which options advertise csv); the adjacent-line rebase. Checked by reading:
ADR-0046 with its amendment, the item, `output.py`, `outcome.py`, the
serialisers, `gh pr diff 721` and both its review comments, #303, #547,
#666, #667, `tests/test_formats.py:404-466`, the tutorial and its runner
directive.

Assumed: `collections list` with rows prints a `repr` (called the
serialiser with a made-up row; the fixture KB had no collection); `links
discover` and `batch-suggest` print as their four siblings do; a pipe table
is acceptable CLI markdown; three option constants can share one recording
function without Typer objecting (not probed; `task`'s two callbacks are the
evidence); the line sizes; that #721's author will rebase.

Not found filed, for the conductor: the empty-set rich text under `--format
json` (`timeline` in both CLIs, `collections list`); `search --files`
ignoring `--format`; `get --format csv` printing `tag,count`; `get --format
markdown` dropping the type and custom fields. 3b and 3c remove the first
three by construction.

**This groom is wrong if** a read command's answer cannot be stated as a
payload, a table or an entry (candidates: `orient`, whose answer is nested;
`search` with a trace), if a caller depends on the heading-style list
markdown, or if the three-constant option cannot be recognised by
`declares_shared_format` without a per-command callback.

Not a spike: no storage or index format, no state Pyrite does not own, no
unverified tool behaviour beyond the last assumption, which 3b meets in its
first hour. It is a public shape, so 3b is opus with a cold read.

---

### Slice 3a: the legacy list is one file per module

small: sections 2 and 7 only.

```
Acceptance:   The legacy commands of each module are named in that module's own file; the
              registry test fails when a file and the registry differ in either direction, and
              names the command. No test names the commands or modules that are on the contract.
              Migrating a command changes no line outside its module's file (show it: plant the
              shared option on one browse command in a test, and only that module's comparison
              goes red). docs/json-contracts.md and cli-system.md no longer list migrated
              modules. Fails today: the per-module comparison (no files).
Regimes:      a module with no legacy command has no file; a file naming a command that does not
              exist is red; a third-party plugin's commands are not counted (as today, :1268);
              the 185 ceiling still holds.
Touches:      existing: tests/test_requested_effect_exit_code.py (:1213-1345, about -45/+40),
              docs/json-contracts.md (:565-568, :656-658), kb/components/cli-system.md (:105)
              new: one directory of 26 small text files (185 names)
              about 250 lines, 185 of them names
Sequence:     independent of #721 (no file under pyrite/). Conflicts once with slice 2 on
              :1225, :1232, :1341-1345: land whichever is nearer done first; the other rebases.
Model:        sonnet
heavy:        no
Cold read:    no
Out of scope: the hint scan (:1348 on) and the behavioural walk (already derived; read
              tests/outcome_walk.py to confirm it holds no module list).
```

### Slice 3b: a command names its table (`browse_commands`, `search_commands`)

```
Acceptance:   The 7 browse commands and search (in pyrite and pyrite-read) declare a shared
              option and return Outcomes; browse's _cli_error and both modules' format branches
              are gone; the walk is green with no list edited. For each of these commands and
              each format its --help offers, stdout is one document of that format holding every
              row its JSON holds, in the declared columns; a format it does not offer exits 2
              and names the ones it does. csv of an empty result is the header line, exit 0.
              --fields sets the columns in csv, markdown and rich (identity fields first, then
              the requested order: #721's tests pass). backlinks csv carries the relation
              columns. The tutorial runs green. A command that declares the table option and
              returns no table is NO_OUTCOME in every format. backlinks on an absent subject is
              a raised not-found; batch-read and search --files keep 3/1, and search --files
              prints the format asked for. Fails today: "pyrite backlinks --format csv has a
              relation column" and "pyrite timeline --format json on an empty KB prints JSON".
Regimes:      empty result (done, 0); absent subject (refusal, 1, JSON error shape under csv
              pending the open question); partial batch-read in csv (rows, exit 3); a --fields
              name no row has (empty column); cells with comma, quote, newline, a list or dict
              (JSON in the cell, as #721); PYRITE_FORMAT naming an unoffered format (open
              question); index lag (a search or list may trail a write; unchanged).
Touches:      existing: pyrite/cli/output.py, pyrite/cli/outcome.py, pyrite/utils/errors.py,
              pyrite/formats/tabular.py (#721's), pyrite/cli/browse_commands.py,
              pyrite/cli/search_commands.py, tests/test_cli_outcome.py (:261, :668 read the
              offered set from the option), docs/json-contracts.md ("--format defaults" and
              #721's section), kb/components/cli-system.md, kb/components/format-system.md,
              the ADR-0046 amendment text, two legacy files deleted
              new: one test file for the invariant (registry-driven: each table command x each
              offered format on a fixture KB), a changelog fragment
              about 850 lines: design 300, browse and search -350/+300, tests 200, docs 50
Sequence:     after decision 1; after #721 merges (its review of 10-10 asks for a rebase and two
              changes; if it is not updated by the time decision 1 is made, carry it as #821
              carried #649, with credit); after 3a. Independent of slice 2 (no shared file once
              3a has landed).
Model:        opus
heavy:        no
Cold read:    yes (plugin-facing type, a public output shape)
Out of scope: REST negotiation and the registry serialisers (pinned by tests/test_formats.py,
              untouched); #54's lean default; the csv formula-cell note in #721's review (the
              same on the old path; its own report); orient stays json, yaml, rich.
Decision:     1 below.
```

### Slice 3c: `pyrite-read`, `collections`, `get`

```
Acceptance:   pyrite-read's 6 commands, collections list|query and pyrite get return Outcomes
              with the shape each declares (get: an entry; lists: a table; config and
              collections list: a payload unless the worker finds a table fits). get --format
              markdown prints what it prints today, in both CLIs; get --format csv exits 2.
              pyrite-read backlinks and pyrite backlinks answer an absent subject with the same
              keys. pyrite-read's defaults are as decision 2 says, stated in
              docs/json-contracts.md and the changelog. read_cli's _emit_error is gone. Fails
              today: "pyrite-read backlinks nope prints the same error keys as pyrite
              backlinks nope".
Regimes:      as 3b; plus a KB named with -k that is not registered (KB_NOT_FOUND, 1).
Touches:      existing: pyrite/read_cli.py, pyrite/cli/collection_commands.py,
              pyrite/cli/entry_commands.py (get only, :179-229), pyrite/cli/outcome.py and
              pyrite/utils/errors.py (the entry shape, if 3b did not land it),
              tests/test_read_cli.py, docs/json-contracts.md, the tutorial only if a line
              becomes false; three legacy files shortened or deleted
              about 450 lines
Sequence:     after 3b (same outcome.py, errors.py) and after slice 2 (entry_commands.py);
              after decision 2
Model:        sonnet if 3b landed the entry shape, else opus
heavy:        no
Cold read:    no, unless decision 2 keeps rich (a default that differs by entry point)
Out of scope: making get --format markdown the file (reported separately); registering
              browse's functions on pyrite-read (open question; do it only if the maintainer
              is told what it adds).
Decision:     2 below.
```

Predicted footprint for the three, scored after merge: 3a about 250 lines
in 3 files plus the directory; 3b about 850 in 12; 3c about 450 in 7.

### Needs a decision first (the maintainer's)

**Decided by the maintainer, 2026-10-13: 1 yes (accept the amendment as
recommended, option R); 2 json.** Recorded in ADR-0046, "Formats beyond
json, yaml and rich". Slices 3b and 3c are no longer waiting on a decision;
their sequence conditions (3a, #721, slice 2) still hold.

1. **Amend ADR-0046: a format beyond json, yaml and rich is offered only by
   a command that declares a shape the root can print** (a table: csv and
   markdown; an entry: markdown), and `Table` with the option constants
   joins the plugin CLI contract (ADR-0040). Recommendation: accept (option
   R). Counter-case: option (c), three formats and nothing else, with the
   tutorial, ADR-0010 §6 and #721 changed to match. It carries the visible
   changes listed at the end of section 4 (pipe-table markdown, `backlinks`
   csv columns, three exit-2 cases).
2. **`pyrite-read`'s default output.** Today five of its commands print
   rich text by default, `search` prints JSON, `config` has no option.
   Recommendation: json, as #303 decided for "every command" and slice 1
   did for `task`, with `PYRITE_FORMAT=rich` for a person and a changelog
   line; `tests/test_read_cli.py` passes either way (it checks exit codes
   and substrings), and no doc teaches `pyrite-read` beyond one phrase at
   `docs/json-contracts.md:484`. Counter-case: `pyrite-read` is not on the
   supported-surface list and may be the entry point a person types; a
   default per entry point is one line at the root and breaks no script.
   Against that: it is the CLI whose help says "safe for AI agents", its
   `search` is already JSON, and a per-entry-point default is the
   per-command default #303 removed, one level up.
