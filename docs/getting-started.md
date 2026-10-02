# Getting Started with Pyrite

Pyrite is a Knowledge-as-Code platform. You keep structured knowledge in markdown files with YAML frontmatter, validated against schemas, versioned in git, and searchable by any AI through MCP. This tutorial walks you from zero to a working knowledge base in about five minutes.

## Install Pyrite

No PyPI wheel yet — install from source:

```bash
git clone https://github.com/pyrite-wiki/pyrite.git && cd pyrite
pip install -e ".[all]"   # Core + AI + semantic search + dev tools
```

Or run the bundled Docker image instead (`docker compose up -d`, serves
on `http://localhost:8088`, reachable only from your own machine: the compose
file publishes the port on `127.0.0.1` and runs with no credential) — see the [README](../README.md#install)
for details, or [pyrite.wiki](https://pyrite.wiki) for hosted/one-click
cloud options.

## Create Your First Knowledge Base

```bash
pyrite init --template research --path my-research
cd my-research
```

This creates:

- **`kb.yaml`** — your KB configuration: name, entry types, field schemas
- Template subdirectories (e.g. `people/`, `organizations/`, `events/`, `notes/`) for the `research` template

The SQLite search index lives outside the KB directory, at `~/.pyrite/index.db` — it's derived from your files and rebuildable anytime (`pyrite index build`), so it's fine to leave out of version control.

`pyrite init` does **not** run `git init` for you. If you want every change versioned from the start (recommended for a git-native product), initialize git yourself:

```bash
cd my-research && git init && git add -A && git commit -m "Initial KB"
```

Templates available: `research`, `software`, `zettelkasten`, `intellectual-biography`, `movement`, `empty`.

Your knowledge base is just a directory of markdown files. You can open them in any editor.

## Create Some Entries

Let's add a few entries to build up some content.

**A person:**

```bash
pyrite create -k my-research --type person --title "Ada Lovelace" \
  --body "Mathematician and writer. Wrote the first algorithm intended for a machine." \
  --tags "mathematics,computing"
```

**A note:**

```bash
pyrite create -k my-research --type note --title "Knowledge management principles" \
  --body "Atomic notes. Link liberally. Let structure emerge from connections, not folders."
```

**An event:**

```bash
pyrite create -k my-research --type event --title "Analytical Engine demonstration" \
  --body "Charles Babbage presented the design of the Analytical Engine." \
  --date "1837-01-01"
```

**A note with tags:**

```bash
pyrite create -k my-research --type note --title "Use markdown for all entries" \
  --body "Plain text is portable, diffable, and future-proof. Markdown adds just enough structure." \
  --tags "process"
```

Each command creates a markdown file with YAML frontmatter like this:

```markdown
---
id: ada-lovelace
title: Ada Lovelace
type: person
tags: [mathematics, computing]
created: 2026-03-03T10:00:00
---

Mathematician and writer. Wrote the first algorithm intended for a machine.
```

The `id` is auto-generated from the title. Pyrite validates fields against the type schema on every write.

## Search Your Knowledge Base

**Keyword search** works out of the box:

```bash
pyrite search "algorithm" -k my-research
pyrite search "mathematics" -k my-research --type person
```

**Semantic search** finds conceptually related content, not just keyword matches. It uses a local embedding model (`all-MiniLM-L6-v2`, ~90 MB) that is downloaded the first time it is needed — expect that one download to take a minute, once.

**Writes never wait on it.** `auto_embed` (on by default) promises that an entry *will be* embedded, not that it is embedded by the time the write returns (ADR-0035): a `pyrite create` or a `POST /api/entries` records the entry, makes it keyword-searchable immediately, and notes the embedding as owed. So on a brand-new KB, a semantic search issued straight after a write may not find that entry yet. Settle the debt — and trigger the download — whenever you like:

```bash
pyrite index embed                 # embed everything not yet embedded
pyrite index sync                  # incremental index update, then embed
```

`pyrite-server` also drains what is owed on every startup and at the end of `POST /api/index/sync`. `GET /api/index/embed-status` reports how much is outstanding, and a semantic search against a KB with no embeddings yet says so in its `warnings` instead of returning a bare empty list. Set `PYRITE_AUTO_EMBED=0` to opt out of embedding entirely and keep keyword search only:

```bash
pyrite index embed -k my-research
pyrite search "early computer science pioneers" -k my-research --mode semantic
```

**Hybrid mode** combines both:

```bash
pyrite search "computing history" -k my-research --mode hybrid
```

## Connect an AI via MCP

Pyrite includes a built-in MCP server.

**One-command setup for Claude Desktop/Code:**

```bash
pyrite mcp-setup
```

This writes the `mcpServers` entry directly into
`~/.claude/claude_desktop_config.json` (or pass `--config` for a
different path) — no manual JSON editing needed. Restart Claude
Desktop/Code afterward to pick up the change.

**Manual setup** (any MCP-compatible client): add this to your config:

```json
{
  "mcpServers": {
    "pyrite": {
      "command": "/absolute/path/to/.venv/bin/pyrite",
      "args": ["mcp"]
    }
  }
}
```

Your AI can now search, read, and create entries in your knowledge base, across three permission tiers (read/write/admin, each cumulative on the last, including tools contributed by any installed plugins). Run `pyrite mcp --help` for the current tool count per tier and representative tool names — it's generated from the live tool registry, so it never drifts from what a running server actually exposes. For read-only access:

```json
{
  "mcpServers": {
    "pyrite": {
      "command": "/absolute/path/to/.venv/bin/pyrite",
      "args": ["mcp", "--tier", "read"]
    }
  }
}
```

See also: [Gemini MCP integration](gemini-mcp-integration.md) | [OpenAI MCP integration](openai-mcp-integration.md)

## Use Templates for Domain-Specific KBs

Templates install extensions with specialized entry types and tools tailored to a domain:

```bash
pyrite init --template software --path my-project
```

The **software** template adds ADRs, components, backlog items, standards, and runbooks — everything you need to manage a software project's knowledge. Other templates include:

- **`zettelkasten`** — note maturity workflows (capture, elaborate, question, refine, connect); the extension behind this template is an example plugin, showing how a Pyrite plugin adds entry types, CLI commands, MCP tools and a preset, not a supported product
- **`encyclopedia`** — articles with review and voting workflows; likewise an example plugin, not a supported product
- **`cascade`** — timeline research with actors and capture lanes

## Launch the Web UI

Pyrite ships an optional web interface for browsing, editing, and visualizing your knowledge base. If you installed with the `server` extra (included in `[all]`):

```bash
pyrite serve
```

Visit [http://localhost:8088](http://localhost:8088). The web UI includes a markdown editor with wikilink autocomplete, an interactive knowledge graph, collections with kanban/table/gallery views, and an AI chat sidebar.

## Next Steps

- [Writing a Plugin](tutorials/plugin-writing.md) — extend Pyrite with custom entry types, MCP tools, and CLI commands
- [Awesome Plugins](plugins.md) — community extensions
- [Gemini MCP Integration](gemini-mcp-integration.md) — connect Pyrite to Gemini
- [OpenAI MCP Integration](openai-mcp-integration.md) — connect Pyrite to OpenAI-compatible clients
- [Docker Deployment](../README.md#deploy) — deploy for teams with Railway, Render, Fly.io, or self-hosted
