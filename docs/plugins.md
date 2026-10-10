# Awesome Pyrite Plugins

Curated list of Pyrite plugins. Plugins add custom entry types, MCP tools, CLI commands, validators, and presets.

## Official Plugins

To install a local plugin and check that its declared entry points load in a fresh
interpreter, run `pyrite extension install ./my-plugin --verify`. Verification uses
the same loader as runtime discovery: each `pyrite.plugins` entry point must load,
instantiate, and expose a `name` attribute. Exit code **3** means installation
succeeded but verification failed, including when the distribution declares no
`pyrite.plugins` entry points. JSON output reports `verified: false` and
`verify_errors`; diagnostic warnings go to stderr.

These plugins ship in-tree in the [`extensions/`](https://github.com/pyrite-wiki/pyrite/tree/main/extensions) directory and are installable as separate packages.

---

### Software KB

Structured knowledge management for software teams -- ADRs, design docs, coding standards, component documentation, backlog tracking, and runbooks.

**Install:** `pip install -e extensions/software-kb`

**Adds:**

- **Entry types:** `adr`, `design_doc`, `standard`, `component`, `backlog_item`, `runbook`
- **MCP tools:** `sw_adrs`, `sw_component`, `sw_standards`, `sw_backlog`, `sw_create_adr`, `sw_create_backlog_item`
- **CLI commands:** `pyrite sw`
- **Preset:** `software`
- **Workflows:** ADR lifecycle, backlog workflow
- **Relationships:** `implements`, `supersedes`, `documents`, `depends_on`, `tracks` (and inverses)

**Use case:** Engineering teams that want to keep architecture decisions, coding standards, component docs, and backlogs in version-controlled markdown alongside their code.

[Full docs](https://github.com/pyrite-wiki/pyrite/tree/main/extensions/software-kb)

---

### Zettelkasten *(example plugin)*

Example plugin. `zettelkasten` exists to show how a Pyrite plugin adds entry types, CLI commands, MCP tools and a preset; it is a reference for plugin authors, not a supported product. It demonstrates atomic note-taking with a CEQRC workflow, maturity tracking, and literature notes.

**Install:** `pip install -e extensions/zettelkasten`

**Adds:**

- **Entry types:** `zettel`, `literature_note`
- **MCP tools:** `zettel_inbox`, `zettel_graph`
- **CLI commands:** `pyrite zettel`
- **Preset:** `zettelkasten`
- **Relationships:** `elaborates`, `branches_from`, `synthesizes` (and inverses)

**Use case:** What this example shows a plugin author -- a `NoteEntry` subclass with domain-specific maturity fields, two read-tier MCP tools, a dedicated Typer sub-app, and custom relationship types wired through `get_relationship_types()`.

[Full docs](https://github.com/pyrite-wiki/pyrite/tree/main/extensions/zettelkasten)

---

### Encyclopedia *(example plugin)*

Example plugin. `encyclopedia` exists to show how a Pyrite plugin adds entry types, CLI commands, MCP tools and a preset; it is a reference for plugin authors, not a supported product. It demonstrates a collaborative knowledge base with quality assessment, review workflows, protection levels, and talk pages.

**Install:** `pip install -e extensions/encyclopedia`

**Adds:**

- **Entry types:** `article`, `talk_page`
- **MCP tools:** `wiki_quality_stats`, `wiki_review_queue`, `wiki_stubs`, `wiki_submit_review`, `wiki_assess_quality`, `wiki_protect`
- **CLI commands:** `pyrite wiki`
- **Preset:** `encyclopedia`
- **Workflows:** Article review workflow
- **DB tables:** Encyclopedia review tables

**Use case:** What this example shows a plugin author -- tier-gated MCP tool registration (read/write/admin), a `get_workflows()` state machine, and plugin-owned DB tables prefixed to avoid collisions.

[Full docs](https://github.com/pyrite-wiki/pyrite/tree/main/extensions/encyclopedia)

---

### Social *(example plugin)*

Example plugin. `social` exists to show how a Pyrite plugin adds entry types, CLI commands, MCP tools and a preset; it is a reference for plugin authors, not a supported product. It demonstrates user-authored writeups, voting, reputation tracking, and author-only editing enforcement.

**Install:** `pip install -e extensions/social`

**Adds:**

- **Entry types:** `writeup`, `user_profile`
- **MCP tools:** `social_top`, `social_newest`, `social_reputation`, `social_vote`, `social_post`
- **CLI commands:** `pyrite social`
- **Preset:** `social`
- **Hooks:** `before_save` (author check), `after_save` (count updates), `after_delete` (reputation adjustment)
- **DB tables:** Social vote and reputation tables

**Use case:** What this example shows a plugin author -- lifecycle hooks (`before_save`, `after_save`, `after_delete`) enforcing authorship and keeping derived counts in sync, plus a full read/write MCP tool split.

[Full docs](https://github.com/pyrite-wiki/pyrite/tree/main/extensions/social)

---

### Cascade

Investigative journalism knowledge management covering actors, organizations, events, themes, mechanisms, scenes, victims, statistics, and timelines.

**Install:** `pip install -e extensions/cascade`

**Adds:**

- **Entry types:** `actor`, `cascade_org`, `cascade_event`, `timeline_event`, `theme`, `victim`, `statistic`, `mechanism`, `scene`, `solidarity_event`
- **MCP tools:** `cascade_actors`, `cascade_timeline`, `cascade_network`, `solidarity_timeline`, `solidarity_infrastructure_types`, `cascade_capture_lanes`
- **KB types:** `cascade-research`, `cascade-timeline`, `cascade-solidarity`
- **Relationships:** `member_of`, `investigated`, `funded_by`, `capture_mechanism`, `built_on`, `responded_to` (and inverses)

**Use case:** Investigative journalists and researchers mapping networks of actors, organizations, events, and power structures across large-scale investigations.

[Full docs](https://github.com/pyrite-wiki/pyrite/tree/main/extensions/cascade)

---

## Community Plugins

Community plugins will be listed here. See [Building Your Own](#building-your-own) to create and submit one.

## Building Your Own

Pyrite plugins are standard Python packages that expose entry types, MCP tools, CLI commands, validators, and presets through a simple class interface.

**Resources:**

- **Plugin writing tutorial:** [`docs/tutorials/plugin-writing.md`](tutorials/plugin-writing.md)
- **Claude Code skill:** Use the `extension-builder` skill in Claude Code to scaffold a new plugin interactively
- **Reference implementations:** Browse the [extensions/](https://github.com/pyrite-wiki/pyrite/tree/main/extensions) directory for working examples

**Submitting a community plugin:**

1. Build and test your plugin against the latest Pyrite release
2. Open a PR to [pyrite-wiki/pyrite](https://github.com/pyrite-wiki/pyrite) adding your plugin to the Community Plugins section of this page
3. Include a link to your plugin's repository, a one-line description, and what it adds
