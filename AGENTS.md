# AGENTS.md

Entry point for any AI agent working in this repository, whatever the
framework. Pyrite is a knowledge infrastructure platform: Knowledge-as-Code for
humans and AI agents. Process (setup, tests, hooks, branches, pull requests) has
one home, [CONTRIBUTING.md](CONTRIBUTING.md); this file points there instead of
repeating it. `CLAUDE.md` imports this file and adds only what is specific to
Claude Code.

The [documentation index](docs/README.md) groups guides and contracts by reader and task.

## Start here

1. Read `kb/design.md` by path before indexed lookups: Pyrite's design on one page, nine principles and the
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

Read `kb/design.md` and the [documentation index](docs/README.md) by path
before indexed lookups. Follow the design's topic-map links to binding ADRs.
Run an incremental sync before trusting indexed context, in this worktree as
well as the main checkout. The examples use each command's default output.

### Quick Context Lookups

```bash
# Reconcile the files before indexed lookups; no embeddings needed
.venv/bin/pyrite index sync -k pyrite --no-embed
# Search binding decisions and living module docs separately
.venv/bin/pyrite search "<topic>" -k pyrite --type adr
.venv/bin/pyrite search "<topic>" -k pyrite --type component
.venv/bin/pyrite get design -k pyrite
.venv/bin/pyrite get adr-0042 -k pyrite
.venv/bin/pyrite sw components -k pyrite
.venv/bin/pyrite sw adrs -k pyrite
.venv/bin/pyrite backlinks <entry-id> -k pyrite
```

### When to Use KB Tools

Before a feature, search for its backlog item, ADRs and component docs. Before
modifying a shared file, search for its path to see what docs reference it.

Other useful lookups (semantic search requires configured embeddings):

```bash
.venv/bin/pyrite sw backlog -k pyrite
.venv/bin/pyrite sw standards -k pyrite
.venv/bin/pyrite tags -k pyrite
.venv/bin/pyrite search "<question>" -k pyrite --mode semantic
```

For binding context, search ADRs and component docs separately with their
type filters. An untyped search can also return closed backlog items;
a result is not a current decision just because it matched. Before modifying
a shared file, search for its path to find the docs that reference it.
`pyrite kb list` must show `pyrite` at *this* worktree's `kb/`.

After work that changes architecture, adds a component or completes a backlog
item, update the KB with the CLI (never by hand-editing frontmatter):

```bash
.venv/bin/pyrite create -k pyrite -t component --title "..." -b "..." --tags core \
  -f kind=module -f path=pyrite/x.py -f owner=core
.venv/bin/pyrite create -k pyrite -t backlog_item --title "..." -b "..." --tags enhancement \
  -f kind=bug -f priority=medium -f effort=S
.venv/bin/pyrite sw new-adr "..." -k pyrite --status proposed
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
- **Extensions**: `extensions/` — zettelkasten, social, encyclopedia, software-kb, journalism-investigation
- **Web frontend**: `web/` — SvelteKit + Svelte 5
- **Knowledge base**: `kb/` — ADRs, backlog, components, designs, standards, runbooks

## Cloud briefs

Before a cloud task, read `scripts/known-cloud-failures.json` (or run
`python3 scripts/known_cloud_failures.py`). Include its current entries in
the brief. When empty, add no warning. This is context, never a CI waiver.

## JSON contracts

See `docs/json-contracts.md` for the canonical error shape, success
envelopes, and body-truncation fields returned by the CLI (`--format
json`), MCP tools, and REST API.

The maintainer runs an orchestration loop outside this repository; every outside
pull request still gets a written review (CONTRIBUTING, "What happens next").
