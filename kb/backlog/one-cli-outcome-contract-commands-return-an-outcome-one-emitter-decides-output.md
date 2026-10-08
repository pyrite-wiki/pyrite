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
