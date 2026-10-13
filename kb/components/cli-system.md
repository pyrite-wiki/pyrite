---
id: cli-system
type: component
title: "CLI System"
kind: cli
path: "pyrite/cli/"
owner: "markr"
dependencies: ["typer", "rich", "pyrite.plugins"]
tags: [core, cli]
---

Typer-based CLI with a root app and eleven domain-specific sub-apps. Commands share infrastructure through context managers that construct PyriteConfig, PyriteDB, and services with guaranteed cleanup. Plugin-provided CLI commands are dynamically registered at startup.

## Architecture

- Root `typer.Typer` app with `add_typer()` for each sub-app
- `context.py` provides `cli_context()`, `cli_registry_context()`, `cli_db_context()`
- Rich tables and console for human-readable output
- Plugin commands discovered via `PluginRegistry.get_all_cli_commands()`

## Sub-Apps

- `kb` — list, add, remove, discover, validate knowledge bases
- `index` — build, sync, stats, embed, health
- `search` — full-text and semantic search
- `qa` — rubric-based quality evaluation
- `repo` — git repository subscribe, fork, sync
- `collections` — collection management
- `links` — wikilink graph operations
- `schema` — schema inspection and migration
- `task` — task management
- `db` — database migration and inspection
- `export` — export entries to various formats
- `extension` — install, list, enable/disable extensions

## Outcomes: one point renders a result, sets the exit code, and is the only way out

ADR-0046 (accepted 2026-10-10, with the enforcement-point amendment). A
command on the contract returns an `Outcome` (`pyrite/cli/outcome.py`: `done`,
`partial`, `nothing`, or `Outcome.from_counts(done, failed, data)` for a
batch), carrying the data machine formats print and a `rich(console, data)`
renderer; a refusal is a raised `PyriteError`. `PyriteCLIGroup.invoke`
(`pyrite/utils/errors.py`), the root of `pyrite`, `pyrite-admin` and
`pyrite-read`, prints the result and reads the exit code from `EXIT_CODES`
(0 / 3 / 1; 2 is click's). An `Outcome`'s data is a mapping or `None` and has
no `outcome` key of its own (a `TypeError` where it is built): the root adds
`"outcome"` (`"error"` on a refusal's error shape), so every machine document
is an object that says what happened.

**On the contract** means: declares the shared `--format` option
(`OUTPUT_FORMAT` in `pyrite/cli/output.py`: `json` default, `yaml`, `rich`;
`PYRITE_FORMAT`; plus `LEGACY_FORMAT_SHORT`, a hidden `-f` that warns, for a
module that had it). `declares_shared_format(command)` is the one definition;
the root's guard and the tests both ask it.

**How the root enforces it (two hooks, one decision).** The root cannot tell
click's parsing from the command's body: `--help`, an unknown option and a
body's own `raise typer.Exit(0)` reach `invoke` as the same exception types.
And the shared option's callback is not body entry either: click calls it
while it is still parsing, so a missing required option is found *after* it.
So:

1. `invoke` puts a `_BodyGuard` in `ctx.meta` (one dict for the whole context
   chain) before it calls the group.
2. The shared option's callback records the format and hands the guard the
   leaf context. The guard wraps `ctx.command.callback`. Typer builds a new
   click tree on every call, so that wrap lasts one run.
3. When click calls the body, the wrapper starts `_StdoutWatch` (once per
   root, however often the body is re-entered), reads the format, and keeps
   the object the body returns.
4. Back in `invoke`: if the body never started, nothing is different from a
   legacy command. If it did, `_decide` is an allow-list: the exact `Outcome`
   object the body returned, a `PyriteError`, a `ValueError` (transitional),
   or `KeyboardInterrupt` passing through. Everything else is `NO_OUTCOME`,
   exit 1. The answer is serialised while stdout is still watched; then the
   watch stops, and any byte it held goes to stderr and makes the answer
   `NO_OUTCOME`.

`_StdoutWatch` holds stdout at two levels in one temporary file: it replaces
`sys.stdout`, and points descriptor 1 at the file (`os.write(1)`, a child
process, `sys.__stdout__`, a stream kept from import). It flushes the original
streams and C's stdio before it starts (text written earlier is not the
body's) and before it stops (text still in a buffer would land after the
root's document). With stdout closed only `sys.stdout` is watched.

**What it does not cover** is listed in `PyriteCLIGroup`'s docstring, and is
the list to read before a slice: parsing (a parameter callback; the registry
test allows none on a command on the contract), stderr, a rich renderer's
words, anything after `invoke` returns (`os._exit`, `atexit`, a thread, a
private unflushed buffer), a duplicate of descriptor 1, a forked child, a
command that reaches into the guard itself, and prompts (a prompt writes to
stdout, so a command that asks a question stays legacy until ADR-0046's open
question is decided).

**Found building slice 2 (`entry_commands`, `links`).** (1) Bad input found
in a body is a `ValidationError` (exit 1), not `typer.BadParameter`: after the
body starts, a click usage error is `NO_OUTCOME`; so `links asymmetric`'s
hand-raised `Exit(2)` became a refusal. (2) `-f` means `--field`, `--file` or
`--force` on some commands: those do not declare `LEGACY_FORMAT_SHORT`, and
the walk and the hint scan find the format alias by its callback
(`tests/outcome_walk.py`'s `takes_short_format`), never by `hidden`. (3)
`delete` stays legacy, exactly as before: it asks, a prompt writes stdout, and
the rule for prompts is the maintainer's open question (a local stderr prompt
was tried and withdrawn: it decided that rule in one module). (4) `get` stays
legacy: it is taught with `--format markdown`, which the shared option does not
offer. (5) A hint a command gave (`bulk-create`'s "Run `pyrite kb list`") is
the exception's `suggestion`; set it on the exception, the root prints it.
(6) A test that states what the CLI prints for a refusal must run the root:
`tests/characterization/error_bodies.py` does, through the real `create`.

**Tests.** `tests/test_cli_outcome.py` is the acceptance test: "the walk"
takes every command of the three CLIs that declares the shared option, plants
each way out from `tests/outcome_walk.py` as its body (on the Typer
registration, keeping the signature), runs the real root in `json` and
`rich`, and expects `NO_OUTCOME`. It has no list of commands: **a slice that
migrates a module writes no guard test**, its commands are walked because
they declare the option. A new *class* of way out is one function in
`outcome_walk.py`. Shapes that need the process's own stdout run through
`python -m tests.outcome_walk`.

Still legacy: `tests/legacy_commands/` holds one text file per CLI module
(`pyrite.cli.browse_commands.txt`), naming, one per line, the module's
registered commands that do not declare the shared option. Each file must
match the registry exactly, in both directions, and the failure names the
command. A slice deletes the lines of the commands it migrates, in its own
modules' files only (an empty file is deleted: a module with no legacy
command has no file); a new command is on the contract unless its name is
added to its module's file. `LEGACY_COMMANDS_CEILING` is an upper bound
that may only go down. A command on the contract shows `PYRITE_FORMAT`
on its `--format` in `--help`, but not the converse: `pyrite mcp-setup`
declares its own `--format` honouring it and is still legacy. The files are
the list. The
same test file keeps a source scan named as a **hint** (`os._exit`,
`atexit.register`, a started thread, a command that reads its own format):
it is not the guard and a spelling it misses is not a finding.

## Logging and default output

A command's default output is its result (#584). `main()` of `pyrite`, `pyrite-admin` and `pyrite-read` calls `pyrite.logging.configure_entry_point_logging()`: WARNING by default, `-v` INFO, `-vv` DEBUG (stripped from `sys.argv` before Typer, so it works in any position), `PYRITE_LOG_LEVEL` when no flag. `pyrite serve` and `pyrite-server` default to INFO (the operator's log); stdio `pyrite mcp` stays at WARNING and logs to stderr only. Tests that need this must run `main()` in a subprocess; `CliRunner` skips it (`tests/test_default_output_is_the_result.py`). A `-v` right after an option that takes a value (`create -b -v`) is that value: `split_verbosity` asks the real Click command, so boolean flags (`--force -v`) still strip. A test that calls `main()` in-process must restore `sys.argv`, which `main()` rewrites. The search trace's `reason` and the warning take their cause from `semantic_unavailable` (`embedding_service.py`), so they cannot disagree.

## MCP client setup

`pyrite mcp-setup` lives in `mcp_setup_command.py`. It points the `pyrite` server entry at this install in every client it finds (Claude Code through `claude mcp`, Claude Desktop's per-OS config file) or the one named by `--client`; `--project` writes `./.mcp.json` and `--config` any named file. With no client it exits 1 with `CLIENT_NOT_FOUND` and prints the entry to paste. Its rule is "do what was asked, lose nothing, report it": an existing entry has only its command path changed (and its tier when `--tier` is given), an equal entry is not rewritten, and whatever cannot be done safely stops that client with an `error_code` and the file untouched. [[mcp-server]] has the full table, the error codes and what is guaranteed about the rest of the file. It was the first command with the #303 output contract, declared locally (the shared option in `output.py` replaces that when its module migrates): `--format` defaults to `json`, `PYRITE_FORMAT` is honoured, there is no `-f`, a usage error exits 2, and the exit code is 1 when a requested effect did not happen (#526). `mcp_tool_counts()` there is also the source of `pyrite mcp --help`'s per-tier counts. `pyrite-admin mcp-setup` is a hidden stub since #582: it exits 1 with `COMMAND_MOVED` and points to `pyrite mcp-setup`.

## Related

- [[plugin-system]] — dynamic command registration
- [[config-system]] — shared configuration
