---
id: mcp-server
type: component
title: "MCP Server"
kind: service
path: "pyrite/server/mcp_server.py"
owner: "markr"
dependencies: ["pyrite.plugins", "pyrite.storage", "pyrite.config"]
tags: [core, mcp, ai-agents]
---

The MCP (Model Context Protocol) server exposes pyrite tools to AI agents like Claude Code over stdio. Implemented as `PyriteMCPServer`, it uses the official `mcp` SDK to serve tools, prompts, and resources.

## Three-Tier Tool Model

Each server instance runs at a single tier, which determines which tools are available:

| Tier | Access Level | Tools |
|------|-------------|-------|
| **read** | Safe for any agent | kb_list, kb_search, kb_get, kb_timeline, kb_backlinks, kb_tags, kb_stats, kb_schema, kb_orient, kb_batch_read, kb_list_entries, kb_recent, kb_qa_validate, kb_qa_status |
| **write** | Trusted agents | read + kb_create, kb_bulk_create, kb_update, kb_delete, kb_link, kb_qa_assess |
| **admin** | Full control | write + kb_index_sync, kb_manage, kb_commit, kb_push |

Tier is set at construction time via the `tier` parameter and validated against `VALID_TIERS`. Invalid tiers raise `ConfigError`.

## The first call and the KB refusal

`kb_orient` is the first call of a session. With no `kb_name` it answers from `KBService.orient_overview` with the KBs the caller may read in pages of 50 by default (maximum 100); use `offset` when `has_more` is true. The handler applies `readable_kbs` before paging, which also lets a scoped caller past the dispatcher's fail-closed rule for a call that names no KB. With a `kb_name` it answers from `KBService.orient`; `detail="brief"` drops the write-side schema blocks (`ai_instructions`, `evaluation_rubric`, `guidelines`, `goals`, and the top-level `guidelines`) but keeps `relationship_types`, and blocks a plugin adds (`get_orient_supplement`) are returned whole.

A caller with a readable set is refused by `_kb_not_found` for any KB name outside that set, absent or private alike, before a handler runs, so one caller cannot tell the two apart. The code is `KB_NOT_FOUND` (with `legacy_error_code: NOT_FOUND` for 0.25.7 only). Handlers' own not-found answers are only ever reached for a name the caller may read, so they need not match it; several still say `NOT_FOUND` and move with the error-code sweep.

The types orient and `kb_schema` list come from `KBSchema.declared_types()`, the rule the write path's `UNDECLARED_TYPE` refusal uses.

## Plugin Tool Merging

After core tools are registered, `_register_plugin_tools()` calls `registry.get_all_mcp_tools(self.tier)` to collect plugin-provided tools. Plugin tools are merged into the same `self.tools` dict, making them indistinguishable from core tools to MCP clients. Plugins register tools via the `get_mcp_tools(tier)` protocol method. Plugin loading failures are silently caught to avoid breaking the server.

Plugins receive a `PluginContext` with shared `config` and `db` references so they don't need to bootstrap their own connections.

## Prompts

Four built-in prompts available at all tiers:

- `research_topic` — search across all KBs, summarize findings, identify gaps
- `summarize_entry` — fetch entry and generate concise summary
- `find_connections` — analyze relationships between two entries (`kb_a`/`kb_b` name each entry's KB)
- `daily_briefing` — summarize recent timeline events (configurable lookback days)

Each prompt returns MCP `PromptMessage` objects with pre-built user messages.

## Resources

Static resources and URI templates for browsing:

- `pyrite://kbs` — list all knowledge bases
- `pyrite://kbs/{name}/entries` — list entries in a KB (limit 200)
- `pyrite://entries/{id}` — get a specific entry
- `pyrite://kbs/{name}/entries/{id}` — get a specific entry from a named KB

Resources are read via `_read_resource()` which dispatches based on URI prefix matching.

**An id in more than one KB (#528).** Every entry read without a KB (`kb_get`, `kb_read_body`, the task tools, `summarize_entry`, `find_connections`, `pyrite://entries/{id}`) is refused with `AMBIGUOUS` and `candidate_kbs` when the id is in more than one KB the caller can read; a private KB is never a candidate. Tools answer the refusal envelope (`_refusal`) as their result; prompts and resources, which have no result shape for a refusal, send it as a JSON-RPC error whose `data` is that envelope (`_protocol_refusal` in `build_sdk_server`), and each has a way to name the KB: `kb_name`, `kb_a`/`kb_b`, or the KB-qualified resource. `tests/test_ambiguous_id_is_refused.py` drives these over a real MCP session, walks the tool schemas and prompts, and fails on an id-taking surface it has not judged.

## SDK Integration

`build_sdk_server()` creates an `mcp.server.Server` instance and registers async handlers for all MCP protocol methods (list_tools, call_tool, list_prompts, get_prompt, list_resources, list_resource_templates, read_resource). The server runs over stdio via `anyio` in `run_stdio()`.

## Post-Save QA Validation

Write tools (`kb_create`, `kb_update`) support opt-in QA validation via two mechanisms:

1. **Per-request**: Pass `validate: true` to run `QAService.validate_entry()` after save
2. **KB-level**: Set `validation.qa_on_write: true` in `kb.yaml` — all writes auto-validate

When triggered, the `_maybe_validate()` helper runs structural QA and appends `qa_issues` to the response if any issues are found. Clean entries return no `qa_issues` key.

## Configuration and Startup

- CLI: `pyrite mcp --tier <tier>` or `pyrite-admin mcp --tier <tier>`; both default to `write` (ADR-0006) and reject an unknown tier with `INVALID_TIER`. `pyrite-admin mcp` defaulted to `admin` until #582.
- Entry point: `main()` in `mcp_server.py` (`python -m pyrite.server.mcp_server`, no installed script) parses `--tier`, default `write` since #582; so does the separately published `pyrite-mcp serve` (`pyrite-mcp/`). The `PyriteMCPServer(tier="read")` constructor default is library API, and every caller passes a tier.
- The server creates its own `PyriteDB` and `KBService` instances
- `close()` must be called to release the DB connection

## Consumers

`pyrite mcp-setup` (`pyrite/cli/mcp_setup_command.py`, #582) points the `pyrite` entry at this install where each client reads it:

- Claude Code, user scope: `~/.claude.json` (or `$CLAUDE_CONFIG_DIR/.claude.json`). Claude Code rewrites that file continually, so Pyrite reads it to decide and changes it only through `claude mcp add -s user` / `claude mcp remove -s user`. There is no update subcommand: a change is `remove` then `add`.
- Claude Code, project scope (`--project`): `./.mcp.json`.
- Claude Desktop: `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS), `%APPDATA%\Claude\claude_desktop_config.json` (Windows). There is no official Linux build; `$XDG_CONFIG_HOME/Claude/` (default `~/.config/Claude/`) is the path unofficial builds use.
- Any MCP-compatible client over stdio: `--config <file>`, or the snippet the command prints when it finds no client.

**The rule** (maintainer, 2026-10-02; see [[pyrite-is-a-guest-in-state-it-does-not-own]]): do what was asked, lose nothing, report it. What to do is decided from the file as it is. Nothing records what Pyrite wrote, and nothing is inferred from what an entry looks like.

| The client has | `mcp-setup` does |
|---|---|
| no `pyrite` entry | writes the default one: this install's `pyrite` by absolute path (the interpreter's scripts directory, never `PATH`), `mcp --tier <tier>`, default `write` |
| an entry equal in what was asked (the command path; the tier when `--tier` is given) | nothing: no write, no `claude` call |
| an entry that differs in what was asked | changes that field; the report holds old and new |
| an entry whose arguments this install's `pyrite mcp` would not start a server with (`<cmd> <args> --check` does not exit 0 and print its one line: an unknown option, a stray positional, an invalid tier, `--help`) | stops (`ENTRY_WOULD_NOT_START`); nothing written. An existing `-t X` (`pyrite-admin mcp` reads it, `pyrite mcp` does not) is rewritten to `--tier X` when the entry is changed anyway |
| an entry carrying env, extra args, a tier nobody asked to change, unknown keys | keeps them |
| an entry whose args do not begin with `mcp`, or that is not an object with a string `command` and string `args` | stops (`ENTRY_NOT_MCP`, `ENTRY_MALFORMED`) and names `--force` |
| other servers and keys, `pyrite-read`/`pyrite-write`/`pyrite-admin` | never touched; the trio is reported |

`--force` discards the entry and writes the default one; the report names what was discarded. Env values are never printed, only keys.

Env: a new Desktop or `--config` entry pins `PYRITE_CONFIG_DIR`/`PYRITE_DATA_DIR` when the shell sets them explicitly, because a GUI-launched server gets no shell environment and an undefined cwd and would load `~/.pyrite`. A repo-local `.pyrite/` is never pinned (an explicit config dir is trusted). Env is never added to an entry that exists: the report names the mismatch and the line to add. Claude Code entries carry no env; Claude Code inherits the shell.

Files Pyrite writes (`.mcp.json`, Desktop, `--config`): parsed strictly (invalid JSON, comments, NaN, a duplicate key anywhere, a non-object `mcpServers` are `CONFIG_INVALID`), changed in the one entry, and written back in the file's own indent unit, line ending, BOM, surrounding whitespace and ASCII-only-ness. Before the replace the output is parsed again with numbers as `Decimal` and compared with the original outside the entry, in order; a difference is `CONFIG_NOT_PRESERVED` and nothing is written. The file is re-read right before the replace (`CONFIG_CHANGED` if it moved). The write is `pyrite/utils/atomic_write.py`; where that helper would write in place (a hard-linked file, a directory it cannot write, another user's file) or the file is read-only, the run stops with `CONFIG_NOT_WRITABLE`. A symlink is written through. A file a client wrote comes back byte for byte outside the entry. A hand-formatted file keeps its values, but one-line arrays open out, mixed indentation becomes uniform, and escapes and numbers may be respelled (`\/`, `\uXXXX`, `1.10`).

The start check is `pyrite mcp <args> --check`: a hidden option that reads the arguments with the command's own parser, checks the tier, prints `pyrite mcp: arguments ok (tier X)` and exits before any server is built. It replaced `<args> --help` (#690), which exits 0 for a stray positional (click prints help before it rejects leftover arguments) and for an invalid tier (checked in the command body). The probe requires the line as well as exit 0, because `--help` among an entry's own arguments exits 0 and starts nothing.

Claude Code user scope. **The invariant (#690): every status and every `by_hand` command is true of the file as last read.**

- `claude` is never run when `~/.claude.json` exists and is empty or unparseable (`CLIENT_CONFIG_UNSAFE`; the client would replace the file), and no report on such a file names a `claude mcp` command, in `suggestion`, `by_hand` or a note: it says to restore the file and re-run.
- An entry whose replacement `claude mcp add` cannot write whole (it would keep env, unknown keys or a NUL in an argument) and whose path must change stops before any call (`CLIENT_ENTRY_NEEDS_HAND_EDIT`). `by_hand` is `remove` then `add` with `-e KEY=<its value>` for each env key (values are never printed; the suggestion says to fill them in and names keys to put back by hand). With a NUL there is no `by_hand`: no command line can carry it. A NUL only in the command this run replaces is not kept, so that change is made like any other (the old entry then cannot be restored by a printed command, and the report prints none).
- The entry is re-read before the first call (`CONFIG_CHANGED` if it moved).
- A call is judged by reading the file back, whatever its exit code, never by message text. `created` and `changed` are reported only after a read saw the new entry. A non-zero exit, a timeout or a `claude` that cannot be run, with the entry there, is success with a note. An exit 0 with the entry not there is `CLIENT_COMMAND_FAILED`, and `claude` is not called again on the strength of it.
- Once `remove` has been attempted, one function (`settle()` in `_setup_claude_code`) is the only way out, whatever happened: `remove`, `add`, their output (decoded as UTF-8 with errors replaced), a read-back, the restore or its read-back. It returns only when the file holds the new entry. Otherwise it ends `stopped` with the old entry in place (as it was, or restored with `claude mcp add` and seen by a read: the restore is tried only after a failed call left the entry absent; an entry that is a string or null counts as an entry, so `--force` over one whose `remove` failed is `stopped`), or `failed` through `left()`, which builds the report from the last read (if the read after a restore shows the new entry, that is success with a note):

| The last read saw | `failed` report |
|---|---|
| no `pyrite` entry | `removed`; `by_hand` = `add` of the old entry when `add` can reproduce it, otherwise no `by_hand` (after `--force` over env or other keys). The suggestion says that a re-run writes the write tier and no extra arguments, not what the entry held |
| another `pyrite` entry (neither the old one nor this install's) | `removed`, `entry` (what is there); `by_hand` = `remove`, then `add` of the old entry, when it can be reproduced |
| nothing (the file could not be read) | `removed`; no `by_hand`, no `claude` command anywhere |

`by_hand` is always one sequence: every line is a command, they are run in order, each works on the state the report describes, and `suggestion` says where they end (the previous entry back, or the entry pointing at this install). Pointing at this install after a `failed` run is `pyrite mcp-setup` again, which decides from the file as it then is. `removed` and `entry` appear on the `failed` reports above (`entry` also on `created`, `changed`, `unchanged` and on a hand-edit `stopped`); a `failed` report from a bug after a call (`INTERNAL_ERROR`) has neither. Not covered: `KeyboardInterrupt` (Ctrl-C) or a signal between `remove` and `add`.

Other scopes (`_note_rest_of_claude_config`, from the file as last read; none when the last read failed): a `pyrite` entry under `projects[<directory>].mcpServers` (Claude Code's local scope, what a bare `claude mcp add` writes) or in the current directory's `./.mcp.json` (project scope) that differs from the user-scope entry is listed in `other_scopes` and in a note, and never changed. Seen with Claude Code 2.1.296 in a temp `HOME`: the local-scope entry is the one `claude mcp get` reports in that directory, so the note says it takes precedence; an unapproved `./.mcp.json` entry did not displace the user-scope one, so that note says "may".

The exits are enumerated from the source: `tests/test_mcp_setup_reads_back.py::test_every_exit_of_the_two_setup_functions_has_a_case` reads every `raise` and `_record(...)` of `_setup_claude_code` and `_setup_file` with `ast` and fails when one has no case; `test_the_report_is_true_of_the_file_and_every_printed_command_works` runs each case, checks which exit it left by, compares the report with the file as the test reads it, and runs each `by_hand` command against the stub `claude`. `test_a_failure_at_any_step_after_remove_restores_or_reports_what_was_removed` injects a failure at each step after `remove`.

Each client is all or nothing on its own. An exception while a client is set up is that client's report line (`INTERNAL_ERROR`); one in the notes that follow (other scopes), in the GUI-config hint or in the tool counts is a note and changes no status. So one client stopping never hides what happened to another, and no bug after a change ends the command before its report. `tests/test_mcp_setup_reads_back.py::test_a_bug_anywhere_after_a_client_changed_still_prints_every_clients_report` plants a raise at every function `mcp_setup` calls by name after the clients are chosen (read from its source); method calls, subscripts and the printing itself are not planted and are classified by reading. Output is one JSON document by default (`--format rich` or `PYRITE_FORMAT=rich` for text; no `-f`): `new_entry` (what a new entry runs, its tier and tool count) and per client `status` (`created`, `unchanged`, `changed`, `stopped`, `failed`), the `tier` and `tools` its entry serves (`tools` is absent when the counts could not be computed) (a kept entry may serve another tier than `--tier`; `tier_named` is false when the entry names none and the server's default, `write`, applies), `changes`, `kept`, `discarded`, `stale`, `other_scopes`, `notes` and, when stopped or failed, `error_code`, `error`, `suggestion` and, where they apply, `by_hand`, `removed` and `entry`. Exit 1 unless every client ended up pointing at this install; 2 for an unknown `--client` or options that contradict. Tests: `tests/test_mcp_setup_reads_back.py` (hand-written fixtures, byte comparisons, a stub `claude` that can fail, hang and vanish, and one test against the real `claude` in a temp `HOME`).

## Related

- [[rest-api]] — HTTP equivalent of MCP tools
- [[kb-service]] — business logic layer used by all tool handlers
- [[storage-layer]] — database access
- [[schema-validation]] — `kb_schema` tool uses `KBSchema.to_agent_schema()`
- [ADR-0006: MCP Three-Tier Tool Model](../adrs/0006-mcp-three-tier-tool-model.md) — defines the read/write/admin tier architecture
