# AGENTS.md

Entry point for any AI agent working in this repository, whatever the
framework. Pyrite is a knowledge infrastructure platform: Knowledge-as-Code for
humans and AI agents. Process (setup, tests, hooks, branches, pull requests) has
one home, [CONTRIBUTING.md](CONTRIBUTING.md); this file points there instead of
repeating it. `CLAUDE.md` imports this file and adds only what is specific to
Claude Code.

The [documentation index](docs/README.md) groups guides and contracts by reader and task.

## Start here

1. Read `kb/design.md`: Pyrite's design on one page, nine principles and the
   questions to ask of any ticket or change. Judge your work against it.
2. Read [CONTRIBUTING.md](CONTRIBUTING.md), "Branches, hooks and pull
   requests" and "Running the tests". They are the only statement of the
   process.
3. Run `pyrite orient -k <kb-name>` before working in any knowledge
   base — it returns entry types, tags, recent changes, schema, and
   the operational contracts (indexing, error shape, search quoting,
   task-claim semantics) in one call. Don't relearn these from trial
   and error. Caveat: for a plugin-declared type (e.g. this repo's own
   `backlog_item`, `adr`, `component`, `standard`), the schema block is
   rubric prose with no field list — `pyrite kb schema show <kb>` and
   `pyrite schema diff` don't fill the gap either (both verified to
   return the same prose, no fields); today the required fields exist
   only in that type's MCP create-tool schema (e.g. `sw_create_backlog_item`
   requires `title`, `kind`, `kb_name`) — see #232.
4. If integrating over MCP: connect with `pyrite mcp --tier <tier>`
   and call the `kb_orient` tool first for the same reason.

## The three rules agents break most

- **Your own branch.** Never push to `dev`; work on `fix/*`, `feature/*`,
  `docs/*`, `kb/*` or `process/*` and open the PR against `dev`. Use a
  worktree (`scripts/new-worktree.sh <branch>`) whenever more than one
  session or agent works in the clone; see CONTRIBUTING, "Branches, hooks
  and pull requests".
- **Stage explicit paths.** `git add <file>`, never `-A`, `-u` or `.`.
- **One heavy run at a time.** The full suite, `scripts/verify-red.sh` and
  Playwright share one machine-wide lock; run the suite as
  `scripts/heavy_lock.py -- .venv/bin/pytest tests/ extensions/*/tests/ -n 4 -q`,
  or just `scripts/test-affected --run`.

## Using the KB for project context

This project has a knowledge base in `kb/`, indexed by Pyrite's own tools.
The CLI is faster and cheaper than reading files by hand.

```bash
.venv/bin/pyrite search "<topic>" -k pyrite                  # keyword
.venv/bin/pyrite search "<question>" -k pyrite --mode semantic
.venv/bin/pyrite get <entry-id> -k pyrite                    # one entry
.venv/bin/pyrite sw components    # core modules and services
.venv/bin/pyrite sw adrs          # architecture decision records
.venv/bin/pyrite sw backlog       # top 50 backlog items (--limit/--offset, --limit 0 for all)
.venv/bin/pyrite sw standards     # coding standards and conventions
.venv/bin/pyrite backlinks <entry-id> -k pyrite              # what links here
.venv/bin/pyrite tags -k pyrite
```

Before a feature, search for its backlog item, ADRs and component docs. Before
modifying a shared file, search for its path to see what docs reference it.
`pyrite kb list` must show the `pyrite` KB at *this* worktree's `kb/`.

After work that changes architecture, adds a component or completes a backlog
item, update the KB with the CLI (never by hand-editing frontmatter):

```bash
.venv/bin/pyrite create -k pyrite -t component --title "..." -b "..." --tags core
.venv/bin/pyrite create -k pyrite -t backlog_item --title "..." -b "..." --tags enhancement
.venv/bin/pyrite sw new-adr "..." --status proposed
.venv/bin/pyrite index sync
.venv/bin/pyrite index health
```

Use the right `type` frontmatter so plugin tools find entries: `component`
(`kind`, `path`, `owner`, `dependencies`), `adr` (`adr_number`, `status`,
`date`), `backlog_item` (`kind`, `status`, `priority`, `effort`), `standard`.

## Key architecture

- **Core code**: `pyrite/` — CLI, server, storage, services, plugins, models
- **REST API**: `pyrite/server/api.py` (factory + deps) + `pyrite/server/endpoints/` (per-feature modules)
- **MCP server**: `pyrite/server/mcp_server.py` (3-tier tools)
- **Extensions**: `extensions/` — zettelkasten, social, encyclopedia, software-kb, journalism-investigation, cascade
- **Web frontend**: `web/` — SvelteKit + Svelte 5
- **Knowledge base**: `kb/` — ADRs, backlog, components, designs, standards, runbooks

## JSON contracts

See `docs/json-contracts.md` for the canonical error shape, success
envelopes, and body-truncation fields returned by the CLI (`--format
json`), MCP tools, and REST API.

The maintainer runs an orchestration loop outside this repository; every outside
pull request still gets a written review (CONTRIBUTING, "What happens next").
