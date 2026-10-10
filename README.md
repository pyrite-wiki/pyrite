<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo/pyrite-mark.svg">
    <img src="docs/assets/logo/pyrite-mark-light.svg" alt="Pyrite logo: an isometric cube inside a hexagonal frame, drawn as a graph of nodes and edges" width="120">
  </picture>
</p>

# Pyrite

**A second brain for you, your agents, and your teams.**

Knowledge-as-Code — structured knowledge in markdown files with YAML frontmatter, schema-validated, versioned in git, searchable by any AI through MCP.

Your AI agents have no memory. Your knowledge is trapped in platform silos. Every new chat starts from zero. Pyrite gives you structured, validated, git-versioned knowledge bases that any AI can read and write through a built-in MCP server. One brain, every AI, persistent memory that compounds over time.

**Why Pyrite instead of vectors-in-Postgres or Notion+AI?**

- **Typed entries with schema validation** — not flat vector blobs. Define `person`, `decision`, `event`, `component` types with validated fields. Query structurally, not just by vibes.
- **Git-native** — every change is a versioned commit, not a database mutation. Branch, diff, review, rollback. Your knowledge has a full audit trail.
- **MCP server with three-tier access control** — read/write/admin tiers. Give untrusted agents read-only access. Give your own agents write access. Keep admin for yourself.
- **Semantic + structured search** — find by meaning (vector embeddings) AND by type, tag, date range, or relationship. Both at once in hybrid mode.
- **Plugin system with 19 extension points** — custom entry types, MCP tools, CLI commands, validators, lifecycle hooks, relationship semantics, schema migrations.
- **Zero running cost locally** — markdown files + SQLite index on your disk. No cloud dependency, no subscription, no vendor lock-in. Your data is plain text files you can read in any editor.

> **Multi-user is experimental. Here be dragons.** Pyrite is solid for one person, their agents, and a team that trusts each other. Accounts, per-KB permissions and the public `/site` work, and each release closes more gaps, but they have not had the review a system holding other people's private data needs. Don't put knowledge on a shared or internet-facing instance that you couldn't live with every user of that instance reading. See [SECURITY.md](SECURITY.md#multi-user-is-experimental).

## Quick Start

```bash
# Install (no PyPI wheel yet -- from source)
git clone https://github.com/pyrite-wiki/pyrite.git && cd pyrite
pip install -e ".[all]"

# Initialize a knowledge base
pyrite init --template research --path my-kb

# Create some entries
pyrite create -k my-kb --type person --title "Sarah Chen" \
  --body "Engineering lead. Considering move to consulting." --tags "team,engineering"

pyrite create -k my-kb --type note --title "Switch to async standups" \
  --body "Decided 2026-03-01. Reduces meeting load by 3hrs/week." --tags "process"

# Search (keyword, semantic, or hybrid)
pyrite search "consulting" -k my-kb
pyrite search "career transition" -k my-kb --mode=semantic  # keyword mode finds exact words only
# The first semantic search downloads the embedding model (~90 MB, one time).
# Writes never wait on that download: an entry is keyword-searchable the
# moment it is written, and gets its embedding on the next `pyrite index
# embed` / `index sync` (or, on a server, at startup). Run `pyrite index
# embed` to fetch the model and catch up on demand.

# Connect to Claude Code and Claude Desktop (every client it finds; restart it after)
pyrite mcp-setup                  # write tier; --tier read|admin, --client, --project
```

`pyrite mcp-setup` points a `pyrite` server entry at this install where each
client reads it: `claude mcp add` for Claude Code (or `./.mcp.json` with
`--project`), and Claude Desktop's own `claude_desktop_config.json`. Run it
again any time. An entry that already points here is not rewritten; one that
points elsewhere gets its command path changed (and its tier, if you pass
`--tier`), and keeps its `env`, extra arguments and other keys. Other servers
are never touched. Each client is all or nothing: one it cannot change safely
is left as it was with the reason, while another may still be changed, and
the exit code is 1 unless every client ended up pointing here. The JSON report
names each client and what changed.
[Getting started](docs/getting-started.md#connect-an-ai-via-mcp) lists the
cases.

Or add it by hand to any MCP client's config:

```json
{
  "mcpServers": {
    "pyrite": {
      "command": "/absolute/path/to/.venv/bin/pyrite",
      "args": ["mcp", "--tier", "write"]
    }
  }
}
```

Use the absolute path (`which pyrite`): Claude Desktop does not see your shell's
PATH or an activated venv.

Now any AI that speaks MCP can search, read, and write your knowledge base.

## How It Works

Markdown files with YAML frontmatter in git are the source of truth. Pyrite builds a SQLite FTS5 index (with optional vector embeddings) on top of the files for fast search. The MCP server, CLI, and REST API all read from and write to the same files. Rebuild the index from files at any time with `pyrite index build`.

You define domain-specific entry types and field schemas in a `kb.yaml` file. Pyrite validates entries on every write, indexes them, and exposes them through all interfaces. A plugin protocol lets you extend entry types, add MCP tools, define relationship semantics, and hook into lifecycle events.

```
Your files (git)  →  SQLite index (derived)  →  MCP server / CLI / REST API / Web UI
                                               ↑
                                          Any AI connects here
```

## MCP Server

Three permission tiers. Each tier includes the tools from lower tiers. Counts below are the core `kb_*`/`task_*` tools only; installed plugins add more per tier (e.g. software-kb adds `sw_adrs`, `sw_backlog`, `sw_new_adr`) — run `pyrite mcp --help` for the live total including plugin tools, generated from the tool registry so it can't drift from what a running server exposes.

| Tier | Core tools |
|------|-------|
| **read** (29) | `kb_list`, `kb_search`, `kb_get`, `kb_timeline`, `kb_tags`, `kb_backlinks`, `kb_stats`, `kb_schema`, `kb_orient`, `kb_batch_read`, `kb_batch_suggest`, `kb_discover_neighbors`, `kb_list_entries`, `kb_recent`, `kb_qa_validate`, `kb_qa_status`, `kb_read_body`, `kb_find_by_status`, `kb_find_by_assignee`, `kb_find_by_location`, `kb_find_overdue`, `kb_index_job_status`, `list_edge_types`, `task_list`, `task_status`, `task_ancestors`, `task_blocked_by`, `task_critical_path`, `task_subtree` |
| **write** (+11) | read + `kb_create`, `kb_bulk_create`, `kb_update`, `kb_delete`, `kb_link`, `kb_qa_assess`, `task_create`, `task_update`, `task_claim`, `task_checkpoint`, `task_decompose` |
| **admin** (+8) | write + `kb_index_sync`, `kb_manage`, `kb_commit`, `kb_push`, `kb_registry_add`, `kb_registry_remove`, `kb_registry_reindex`, `kb_registry_health` |

Pagination and totals differ by tool and by transport (CLI/MCP/REST) — see `docs/json-contracts.md` for the measured shape of each, rather than assuming a uniform `has_more`/`total` contract. `kb_bulk_create` handles up to 50 entries per call. After parameter validation, a missing or empty title is reported per entry without rejecting valid siblings, and results preserve input order. Invalid parameter types, an unavailable KB, or an invalid batch size still reject the whole call. `kb_orient` provides a one-shot KB summary for agent onboarding. `kb_batch_read` fetches multiple entries in one call. Search results return snippets by default (use `include_body` for full text, `fields` for projection).

Plugins add their own tools per tier (e.g., software-kb adds `sw_adrs`, `sw_backlog`, `sw_new_adr`).

Also exposes: 4 prompts (`research_topic`, `summarize_entry`, `find_connections`, `daily_briefing`), resources (`pyrite://kbs`, `pyrite://kbs/{name}/entries`, `pyrite://entries/{id}`).

Use `pyrite mcp --tier read` for a read-only server.

## CLI

```bash
# Search (keyword, semantic, or hybrid)
pyrite search "immigration policy"
pyrite search "immigration" --kb=timeline --type=event --mode=hybrid

# Read
pyrite get stephen-miller
pyrite backlinks stephen-miller --kb=research
pyrite timeline --from=2025-01-01 --to=2025-06-30
pyrite collections list --kb=research

# Write
pyrite create --kb=research --type=person --title="Jane Doe" \
  --body="Senior policy advisor." --tags="policy,doj"

# Admin
pyrite index sync          # Incremental re-index after file edits
pyrite index health        # Check for stale/missing entries
pyrite kb discover         # Auto-find KBs by kb.yaml presence

# Schema versioning
pyrite schema diff --kb=research      # Show type versions and field annotations
pyrite schema migrate --kb=research   # Migrate entries to current schema version
```

All commands support `--format json` for agent consumption.

## Custom Types

Define types in `kb.yaml`:

```yaml
name: legal-research
kb_type: generic
types:
  case:
    description: "Legal case or proceeding"
    fields:
      jurisdiction:
        type: select
        options: [federal, state, international]
      status:
        type: select
        options: [active, decided, appealed, settled]
      filing_date:
        type: date
      parties:
        type: list
        items:
          type: text
```

Types support versioning for safe schema evolution:

```yaml
types:
  case:
    version: 2
    fields:
      methodology:
        type: text
        required: true
        since_version: 2  # required for new entries, warning-only for legacy
```

Entries track their schema version in `_schema_version` frontmatter. `pyrite schema migrate` applies registered migrations and produces a reviewable git diff.

Field types: `text`, `number`, `date`, `datetime`, `checkbox`, `select`, `multi-select`, `object-ref`, `list`, `tags`.

Declared enums are enforced: `options:` on a `select`, `multi-select` or `list` field (`values:` is an accepted alias; `items: {options: [...]}` constrains a list's elements) and `enum:` in `validation.rules`. A write with an off-list value is refused by default; `validation.enforce_enums: false` makes it a warning instead, separately from `validation.enforce`, which governs every other check. An update that leaves a value already off-list unchanged still succeeds and reports it, and `pyrite index health` lists every off-list value under `off_list_values`.

Eleven built-in entry types: `note`, `person`, `organization`, `event`, `document`, `topic`, `relationship`, `timeline`, `collection`, `qa_assessment`, `task`. Entries support `aliases` for alternate names that resolve in wikilinks and autocomplete.

## Plugin Protocol

Extensions implement a Python protocol class with up to 19 methods:

- `get_entry_types()` — custom entry types with serialization
- `get_type_metadata()` — field definitions, AI instructions, presets
- `get_collection_types()` — custom collection types
- `get_mcp_tools(tier)` — per-tier MCP tools
- `get_cli_commands()` — Typer sub-commands
- `get_validators()` — entry validation rules
- `get_migrations()` — schema migration functions for entry type upgrades
- `get_relationship_types()` — semantic relationship definitions
- `get_hooks()` — lifecycle hooks: `before_save`, `after_save`, `before_delete`, `after_delete`, `before_index`

Six extensions ship:

| Extension | Purpose | Key Types |
|-----------|---------|-----------|
| **software-kb** | Software project management | ADRs, components, backlog items, standards, runbooks |
| **zettelkasten** *(example plugin)* | CEQRC maturity workflow | Notes with maturity progression |
| **encyclopedia** *(example plugin)* | Articles with review workflow | Articles, reviews, voting |
| **social** *(example plugin)* | Engagement tracking | Social interactions |
| **journalism-investigation** *(experimental; unsupported)* | Investigative research | Sources, claims, actors, evidence chains |
| **cascade** | Timeline research | Timeline events, actors, capture lanes |

`zettelkasten`, `encyclopedia` and `social` are example plugins: reference
code showing how a Pyrite plugin adds entry types, CLI commands, MCP tools
and a preset, not supported products. See each extension's `README.md` for
what it demonstrates.

**EXPERIMENTAL: Unsupported until the 0.28 alpha plugin/API contract.**
The journalism-investigation MCP tools are not a supported product surface.

Tracked issues: [#92](https://github.com/pyrite-wiki/pyrite/issues/92), [#93](https://github.com/pyrite-wiki/pyrite/issues/93), [#94](https://github.com/pyrite-wiki/pyrite/issues/94), and [#98](https://github.com/pyrite-wiki/pyrite/issues/98).

## Web UI

Optional SvelteKit 2 + Svelte 5 frontend for browsing, visualization, and oversight:

- WYSIWYG + markdown editor (Tiptap + CodeMirror dual mode)
- `[[wikilinks]]` with autocomplete, alias resolution, and pill decorations
- `![[transclusion]]` embedded content cards
- Block references: `[[entry#heading]]` and `[[entry^block-id]]`
- Backlinks panel, outline/TOC, split panes
- Interactive knowledge graph (Cytoscape.js)
- Collections with list, table, kanban, and gallery views
- Virtual collections via query DSL
- AI chat sidebar (RAG), summarize, auto-tag, suggest links
- Quick switcher (Cmd+O), command palette (Cmd+K)
- Daily notes with calendar
- Timeline visualization
- Version history with diff viewer
- Web clipper for URL content capture
- WebSocket multi-tab sync
- Slash commands in editor

## Architecture

```
pyrite/
├── models/          # Entry types (base, core_types, factory, generic, collection)
├── schema/          # YAML-driven type definitions, field validation, core types
├── migrations.py    # Schema migration registry (on-load entry transforms)
├── config.py        # Multi-KB and repo configuration
├── server/
│   ├── api.py       # FastAPI REST API factory (role-based tier enforcement)
│   ├── mcp_server.py # MCP server (mcp SDK, 3-tier, paginated)
│   ├── websocket.py # WebSocket multi-tab sync
│   └── endpoints/   # Per-feature REST routes (entries, search, kbs, collections, graph, daily, clipper, ...)
├── storage/
│   ├── database.py  # SQLite + FTS5 + sqlite-vec (SQLAlchemy ORM + raw SQL)
│   ├── index.py     # Incremental indexing with wikilink/transclusion extraction
│   └── repository.py # Markdown file I/O
├── services/        # Business logic, ~40 services (kb, search, embedding, llm, git, task, qa, auth, worktree, export, ...)
├── plugins/         # Plugin discovery and protocol
└── formats/         # Content negotiation (JSON, Markdown, CSV, YAML)

extensions/          # Domain-specific plugins (software-kb, zettelkasten [example], encyclopedia [example], social [example], journalism-investigation, cascade)
web/                 # SvelteKit 2 + Svelte 5 frontend (TypeScript + Tailwind)
kb/                  # Pyrite's own KB (ADRs, backlog, components, standards)
```

**Storage model:** Markdown files in git are the source of truth. SQLite FTS5 is a derived index. Rebuild from files at any time with `pyrite index build`. Background embedding pipeline keeps vector index current.

**Content negotiation:** REST API responds in JSON, Markdown, CSV, or YAML via `Accept` header. CLI supports `--format`.

**Access control:** REST API supports role-based tier enforcement (read/write/admin) with hashed API keys.

## Deploy

> Hosting an instance for other people is experimental: read the [multi-user warning](SECURITY.md#multi-user-is-experimental) first.

### One-Click Deploy

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/template/new?repo=pyrite-wiki/pyrite&referralCode=pyrite)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/pyrite-wiki/pyrite)

**Fly.io** — create a volume and deploy:

```bash
fly launch --copy-config --name my-pyrite
fly volumes create pyrite_data --size 1
fly deploy
```

All three platforms use the included Dockerfile, persist data at `/data`, and expose port 8088. They start with accounts turned on (`PYRITE_AUTH_ENABLED=true`). Registration stays closed until an admin exists: create yours after the first deploy with `pyrite-admin user create <name> --role admin` in the container. Set `PYRITE_OPENAI_API_KEY` and other env vars in your platform's dashboard.

The container image binds `0.0.0.0` (`PYRITE_HOST`) and honours the platform `$PORT` when `PYRITE_PORT` is unset (`PYRITE_PORT` wins if both are set). Railway does not attach a persistent volume from the one-click button — add a volume mounted at `/data` in the Railway dashboard after the first deploy, or data will not survive restarts.

### Self-Hosted VPS

Run your own Pyrite instance on any VPS ($6/month, you own your data):

```bash
git clone https://github.com/pyrite-wiki/pyrite.git && cd pyrite
bash deploy/selfhost/setup.sh kb.example.com
```

This installs Docker, starts Pyrite + Caddy (auto TLS), and seeds Pyrite's own KB so you have content to explore immediately. Then create your admin user:

```bash
docker compose -f deploy/selfhost/docker-compose.yml exec pyrite \
  python /app/deploy/selfhost/create-user.py admin yourpassword
```

Auth is required, registration is closed by default — add users manually with the same command.

### Local Docker

For local development without TLS, use the minimal compose:

```bash
docker compose up -d  # http://localhost:8088
```

The compose file publishes the port on `127.0.0.1` only, and the server in it
has no credential, so only this machine can reach it. To serve anyone else,
turn on auth first (`PYRITE_AUTH_ENABLED=true`) and then change the published
address. `pyrite serve` and `pyrite-server` warn at startup when they run
without a credential on a non-loopback address; a server started some other way
(for example `uvicorn pyrite.server.api:app --host 0.0.0.0`) does not, because
the app cannot see its own bind address.

## Install

No PyPI wheel yet. From source (CLI, server, MCP **and** the web UI):

```bash
git clone https://github.com/pyrite-wiki/pyrite.git && cd pyrite
pip install -e ".[all]"      # Core + AI + semantic search + dev tools
cd web && npm ci && npm run build && cd ..   # the web UI (optional)
```

Or straight from a release tag, no clone:

```bash
pip install "pyrite[server,cli] @ git+https://github.com/pyrite-wiki/pyrite@v0.25.7"
```

That gives you the CLI, the REST API and the MCP server, but **not the web
UI** — the built frontend is not packaged yet (tracked in the roadmap; clone
and `npm run build` above if you want it). Semantic search needs the
`semantic` extra too (`pyrite[server,cli,semantic] @ git+...`, a large
download); without it, keyword search works and a semantic search says so.

Narrower extras: `pip install -e ".[server]"` (REST API + web UI),
`pip install -e ".[ai]"` (OpenAI + Anthropic SDKs),
`pip install -e ".[semantic]"` (sentence-transformers + sqlite-vec).

Extensions are installed separately:

```bash
pip install -e extensions/software-kb
pip install -e extensions/zettelkasten
pip install -e extensions/encyclopedia
pip install -e extensions/social
pip install -e extensions/cascade
pip install -e extensions/journalism-investigation
```

(`zettelkasten`, `encyclopedia` and `social` are example plugins — see
[Plugin Protocol](#plugin-protocol) above.)

Prefer not to install anything locally? See [Deploy](#deploy) above for
Docker, one-click cloud (Railway/Render/Fly.io), and self-hosted VPS
options — or [pyrite.wiki](https://pyrite.wiki) for a public demo.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"
for ext in extensions/*/; do pip install -e "$ext"; done
pre-commit install

# Tests (core and extensions; in parallel with -n auto)
pytest tests/ extensions/ -n auto

# Frontend
cd web && npm install && npm run dev
# Port 5173 must be free -- strictPort is on, so a taken port is a startup
# error, not a silent move to 5174.

# Linting
ruff check pyrite/
```

Pyrite's own backlog and architecture docs live in `kb/`:

```bash
pyrite sw backlog        # Prioritized backlog
pyrite sw adrs           # Architecture Decision Records
pyrite sw components     # Module documentation
pyrite sw standards      # Coding conventions
```

## Documentation

- [Getting Started](docs/getting-started.md) — install, create a KB, connect an AI
- [Configuration](docs/configuration.md) — `config.yaml` and every `PYRITE_*` environment variable
- [Plugin Writing Tutorial](docs/tutorials/plugin-writing.md) — build a custom plugin step by step
- [Plugins Directory](docs/plugins.md) — official and community plugins
- [OpenAI / Codex MCP Integration](docs/openai-mcp-integration.md)
- [Gemini CLI MCP Integration](docs/gemini-mcp-integration.md)

## Background

Pyrite was built at [Transparency Cascade Press](https://transparencycascade.org), an independent investigative outfit, because the reporting needed it. Investigations there run on thousands of sourced entries — actors, events, contracts, court filings — that have to stay verifiable months after they were written, and no note-taking tool treated an AI research assistant as a first-class user of that record rather than a chat window bolted onto it.

That constraint shaped the design. Agents are users here: they create and query entries through the CLI and MCP, they get typed errors instead of tracebacks, and they operate under the same schema validation and three-tier permissions a human does. The knowledge base is plain markdown in git precisely so a claim can be traced to the commit that introduced it, which is a journalism requirement before it is a software one.

It is still used in production for that work, and it has since grown past it into a general tool.

**Contributors.** [AsyncLegs](https://github.com/AsyncLegs) deployed Pyrite as
a server for agents and found, then fixed, three bugs the test suite had never
seen (MCP over SSE, KB registry cache, embedding prewarm — v0.24.1). Bug reports
go to [GitHub issues](https://github.com/pyrite-wiki/pyrite/issues); see
[CONTRIBUTING.md](CONTRIBUTING.md) for how the project works.

If Pyrite is useful to you, please [star the repository](https://github.com/pyrite-wiki/pyrite).
Stars are how many people decide whether a project is alive and worth a look,
so a star helps others find it.

Started as a fork of [joshylchen/zettelkasten](https://github.com/joshylchen/zettelkasten). Since substantially rewritten: multi-KB, plugin system, three-tier MCP, FTS5 + vector search, REST API with tier enforcement, SvelteKit frontend, service layer, schema-as-config, content negotiation, collections, block references, web clipper, AI integration. See [UPSTREAM_CHANGES.md](UPSTREAM_CHANGES.md) for divergence history.

## License

MIT — see [LICENSE](LICENSE).
