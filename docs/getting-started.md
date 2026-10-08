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
for details, or [pyrite.wiki](https://pyrite.wiki) for a public demo and the
one-click cloud options.

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
git init
git add -A
git commit -m "Initial KB"
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

Each command creates a markdown file with YAML frontmatter. Here is the person's:

```bash
cat people/ada-lovelace.md
```
<!-- runner: expect-text id: ada-lovelace title: Ada Lovelace type: person -->
<!-- runner: expect-text tags: - mathematics - computing importance: 5 research_status: stub -->

```markdown
---
id: ada-lovelace
title: Ada Lovelace
type: person
tags:
- mathematics
- computing
importance: 5
research_status: stub
---

Mathematician and writer. Wrote the first algorithm intended for a machine.
```

The `id` is auto-generated from the title. Pyrite validates fields against the type schema on every write.

## Search Your Knowledge Base

**Keyword search** works out of the box:

```bash
pyrite search "algorithm" -k my-research
```
<!-- runner: expect-ids ada-lovelace -->

Narrow it to one entry type. Search matches an entry's title and body, not its tags, so this finds Ada by the word "Mathematician" in her body:

```bash
pyrite search "mathematician" -k my-research --type person
```
<!-- runner: expect-ids ada-lovelace -->

**Semantic search** finds conceptually related content, not just keyword matches. It needs the `semantic` extra (in `[all]`; a [release-tag install](../README.md#install) adds it by name) and uses a local embedding model (`all-MiniLM-L6-v2`, ~90 MB) that is downloaded the first time it is needed — expect that one download to take a minute, once.

**Writes never wait on it.** `auto_embed` (on by default) promises that an entry *will be* embedded, not that it is embedded by the time the write returns (ADR-0035): a `pyrite create` or a `POST /api/entries` records the entry, makes it keyword-searchable immediately, and notes the embedding as owed. So on a brand-new KB, a semantic search issued straight after a write may not find that entry yet. Settle the debt — and trigger the download — whenever you like:

```bash
pyrite index embed                 # embed everything not yet embedded
pyrite index sync                  # incremental index update, then embed
```

`pyrite-server` also drains what is owed on every startup and at the end of `POST /api/index/sync`. `GET /api/index/embed-status` reports how much is outstanding, and a semantic search against a KB with no embeddings yet says so in its `warnings` instead of returning a bare empty list. Set `PYRITE_AUTO_EMBED=0` to opt out of embedding entirely and keep keyword search only. Now search by meaning:

```bash
pyrite search "early computer science pioneers" -k my-research --mode semantic
```
<!-- runner: expect-text pyrite index embed -->

With the embeddings built, that finds Ada Lovelace. Until they are, the search prints `warning: semantic leg skipped ... run pyrite index embed` and returns nothing. The tutorial's test run is offline, so it sees that warning. Without the `semantic` extra the warning says instead that sentence-transformers is not installed and suggests `pip install pyrite[semantic]`; there is no PyPI wheel yet, so install it the way you installed Pyrite: `pip install -e ".[semantic]"` in a clone, or `pyrite[server,cli,semantic] @ git+...` from a release tag (see the [README](../README.md#install)).

**Hybrid mode** combines both, and still answers from the keyword leg while the embeddings are missing:

```bash
pyrite search "algorithm machine" -k my-research --mode hybrid
```
<!-- runner: expect-ids ada-lovelace -->

## Connect an AI via MCP

Pyrite includes a built-in MCP server.

**One-command setup for Claude Code and Claude Desktop:**

```bash
pyrite mcp-setup
```

This points a server entry named `pyrite` at this install, in every client it
finds, and prints a JSON report of what it did to each (`--format rich` or
`PYRITE_FORMAT=rich` for text). Restart the client afterwards.

- **Claude Code**: through `claude mcp add -s user`, which stores the entry in
  `~/.claude.json`. `--project` writes `./.mcp.json` in the current directory
  instead.
- **Claude Desktop**: in its own `claude_desktop_config.json`
  (macOS `~/Library/Application Support/Claude/`, Windows `%APPDATA%\Claude\`).
- **Any other client**: `--config PATH` names its `mcpServers` file.

A new entry runs this install's `pyrite` by absolute path at the `write` tier
(`--tier read` or `--tier admin` to choose).

It is safe to run again, and safe on a config you edited by hand:

- An entry that already points here is left alone and nothing is written,
  unless `--tier` asks for a tier it does not have; then only the tier changes.
- An entry that points somewhere else has its `command` path changed, and its
  tier only if you pass `--tier`. Everything else you put in the entry (`env`,
  extra arguments, other keys) is kept. The report gives the old and new
  values.
- `env` is never added to an entry that exists. If your shell sets
  `PYRITE_CONFIG_DIR` and the entry does not, the report says so and gives the
  line to add. A new entry does pin it.
- Other servers and other keys are never touched. Servers named `pyrite-read`,
  `pyrite-write` or `pyrite-admin` (an older command wrote those) are
  reported and left in place.

The command stops, changes nothing in that client and exits 1 when it cannot
do this safely:

- the file is not valid JSON (comments and trailing commas included), has a
  duplicate key, is read-only or hard-linked, or changed while the command ran;
- the `pyrite` entry's arguments do not begin with `mcp`, or the entry is not
  a server entry at all (`--force` discards it and writes the default entry);
- the entry's own arguments, with this install's path, would not start
  (it runs them with `--help` first; an old `-t X` is rewritten to `--tier X`);
- in Claude Code's user scope, the entry has `env` or other keys and its path
  must change. Claude Code can only remove and re-add an entry, which would
  drop them, so the report gives the commands to run yourself;
- `~/.claude.json` is empty or unreadable (Claude Code would replace it).

`--force` means one thing: discard the existing `pyrite` entry and write the
default one. The report names what was discarded.

A config file that a client wrote comes back byte for byte outside the
`pyrite` entry. A file you formatted by hand keeps every value and its indent,
line endings and trailing newline, but some values are respelled: one-line
arrays and objects are opened out, mixed indentation or line endings are made
uniform, `\/` becomes `/`, a `\u` escape may become
its character, and numbers are written Python's way (`1.10` as `1.1`, `1e5` as
`100000.0`). If a value could not come back equal (a number with more digits
than a float holds), nothing is written.

**Manual setup** (any MCP-compatible client): add this to your config:

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

The templates `pyrite init` accepts are the six listed above. Each works with nothing extra installed:

```bash
cd ..
pyrite init --template software --path my-project
```

The **software** template adds ADRs, components, backlog items, standards, and runbooks — everything you need to manage a software project's knowledge. Its `pyrite sw` commands (`sw adrs`, `sw backlog`, ...) come from the software-kb extension, which is a separate install: `pip install -e extensions/software-kb`. Extensions are never installed by a template; the [README](../README.md#install) lists them.

The `zettelkasten` template gives note-maturity types; the extension behind it is an example plugin, showing how a Pyrite plugin adds entry types, CLI commands, MCP tools and a preset, not a supported product.

## Launch the Web UI

Pyrite ships an optional web interface for browsing, editing, and visualizing your knowledge base. It is built from the `web/` directory of your clone, so from the repository root, once:

```bash
cd web && npm install && npm run build
```

Then, with the `server` extra (included in `[all]`):

```bash
pyrite serve
```

Visit [http://localhost:8088](http://localhost:8088). The web UI includes a markdown editor with wikilink autocomplete, an interactive knowledge graph, collections with kanban/table/gallery views, and an AI chat sidebar.

An install from a release tag has no web UI yet: the built frontend is not packaged. `pyrite serve` there starts the API and says so. The CLI and the MCP server are unaffected.

## Next Steps

- [Writing a Plugin](tutorials/plugin-writing.md) — extend Pyrite with custom entry types, MCP tools, and CLI commands
- [Awesome Plugins](plugins.md) — community extensions
- [Pin your entry ids before upgrading](pinning-entry-ids.md) — upgrading a KB with files that have no `id:` line
- [Gemini MCP Integration](gemini-mcp-integration.md) — connect Pyrite to Gemini
- [OpenAI MCP Integration](openai-mcp-integration.md) — connect Pyrite to OpenAI-compatible clients
- [Docker Deployment](../README.md#deploy) — deploy for teams with Railway, Render, Fly.io, or self-hosted
