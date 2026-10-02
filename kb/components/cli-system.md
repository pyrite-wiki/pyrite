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

## Logging and default output

A command's default output is its result (#584). `main()` of `pyrite`, `pyrite-admin` and `pyrite-read` calls `pyrite.logging.configure_entry_point_logging()`: WARNING by default, `-v` INFO, `-vv` DEBUG (stripped from `sys.argv` before Typer, so it works in any position), `PYRITE_LOG_LEVEL` when no flag. `pyrite serve` and `pyrite-server` default to INFO (the operator's log); stdio `pyrite mcp` stays at WARNING and logs to stderr only. Tests that need this must run `main()` in a subprocess; `CliRunner` skips it (`tests/test_default_output_is_the_result.py`). A `-v` right after an option that takes a value (`create -b -v`) is that value: `split_verbosity` asks the real Click command, so boolean flags (`--force -v`) still strip. A test that calls `main()` in-process must restore `sys.argv`, which `main()` rewrites. The search trace's `reason` and the warning take their cause from `semantic_unavailable` (`embedding_service.py`), so they cannot disagree.

## MCP client setup

`pyrite mcp-setup` lives in `mcp_setup_command.py`. It points the `pyrite` server entry at this install in every client it finds (Claude Code through `claude mcp`, Claude Desktop's per-OS config file) or the one named by `--client`; `--project` writes `./.mcp.json` and `--config` any named file. With no client it exits 1 with `CLIENT_NOT_FOUND` and prints the entry to paste. Its rule is "do what was asked, lose nothing, report it": an existing entry has only its command path changed (and its tier when `--tier` is given), an equal entry is not rewritten, and whatever cannot be done safely stops that client with an `error_code` and the file untouched. [[mcp-server]] has the full table, the error codes and what is guaranteed about the rest of the file. It is the first command with the #303 output contract declared locally: `--format` defaults to `json`, `PYRITE_FORMAT` is honoured, there is no `-f`, a usage error exits 2, and the exit code is 1 when a requested effect did not happen (#526). `mcp_tool_counts()` there is also the source of `pyrite mcp --help`'s per-tier counts. `pyrite-admin mcp-setup` is a hidden stub since #582: it exits 1 with `COMMAND_MOVED` and points to `pyrite mcp-setup`.

## Related

- [[plugin-system]] — dynamic command registration
- [[config-system]] — shared configuration
