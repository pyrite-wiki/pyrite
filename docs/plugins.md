# Awesome Pyrite Plugins

Curated list of Pyrite plugins. Plugins add custom entry types, MCP tools, CLI commands, validators, and presets.

## Official Plugins

These plugins ship in-tree in the [`extensions/`](https://github.com/pyrite-wiki/pyrite/tree/main/extensions) directory and are installable as separate packages.

---

### Software KB

Structured knowledge management for software teams -- ADRs, design docs, coding standards, component documentation, backlog tracking, and runbooks.

**Install:** `pip install -e extensions/software-kb`

**Adds:**

- **Entry types:** `adr`, `design_doc`, `standard`, `programmatic_validation`, `development_convention`, `component`, `backlog_item`, `runbook`, `milestone`, `work_log`
- **MCP tools (23):** read tier (also in write and admin):
  - Reference: `sw_adrs`, `sw_component`, `sw_standards`, `sw_validations`, `sw_conventions`, `sw_milestones`
  - Backlog and kanban: `sw_backlog`, `sw_board`, `sw_pull_next`, `sw_review_queue`, `sw_context_for_item`, `sw_epics`, `sw_epic_detail`, `sw_check_ready`, `sw_refine`

  Write tier (also in admin): `sw_claim`, `sw_submit`, `sw_review`, `sw_transition`, `sw_prioritize`, `sw_log`, `sw_create_adr`, `sw_create_backlog_item`
- **CLI commands:** `pyrite sw`
- **Preset:** `software`
- **Workflows:** ADR lifecycle, backlog workflow
- **Relationships:** `implements`, `supersedes`, `documents`, `depends_on`, `tracks` (and inverses)

**Use case:** Engineering teams that want to keep architecture decisions, coding standards, component docs, and backlogs in version-controlled markdown alongside their code.

#### `board.yaml`: lanes, WIP limits and gates

A software KB's board (its lanes, WIP limits and the Definition of Ready and Done gates of ADR-0021) is read from `board.yaml` in the KB's own root. `pyrite init -t software` writes none; with no file, the built-in board applies (five lanes, both gates at `warn`). A file replaces the built-in board whole; nothing is merged.

```yaml
lanes:                      # optional; absent means no lanes
  - name: Ready             # required, text
    statuses: [accepted]    # required, a list of statuses
  - name: In Progress
    statuses: [in_progress]
    wip_limit: 3            # optional, a whole number, 0 or more
wip_policy: enforce         # optional: warn (default) or enforce
gates:                      # optional; absent means no gates
  in_progress:              # the status the gate guards
    name: Definition of Ready
    policy: enforce         # optional: warn (default) or enforce
    criteria:
      - text: Effort estimated
        checker: has_field  # a checker Pyrite has (pyrite/services/rubric_checkers.py, or no_open_blockers)
        params: {field: effort}
      - text: Problem statement is clear
        type: judgment      # checker (default), judgment or agent_responsibility
```

An empty file declares no lanes and no gates. A file that cannot be loaded (it does not parse, is not a mapping, has a field of the wrong type, a lane with no `statuses`, a policy other than `warn` or `enforce`, or a criterion naming a checker Pyrite does not have) is refused: every tool that reads the board (`sw_claim`, `sw_transition`, `sw_review`, `sw_pull_next`, `sw_check_ready`, `sw_refine`, `sw_board`, `sw_review_queue`, and the `pyrite sw` commands that call them) answers `VALIDATION_FAILED`, not retryable, with a message naming `board.yaml` and the problem, and changes nothing. `kb_orient` still answers, with `board_error` in its software block. A tool called for a KB that does not resolve is refused (`KB_NOT_FOUND`); `sw_board`, `sw_review_queue` and `sw_pull_next` called with no KB span every KB and use the built-in board.

[Full docs](https://github.com/pyrite-wiki/pyrite/blob/main/kb/components/software-kb-extension.md)

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

### Journalism Investigation

**EXPERIMENTAL: Unsupported until the 0.28 alpha plugin/API contract.**
The journalism-investigation MCP tools are not a supported product surface.

Tracked issues: [#92](https://github.com/pyrite-wiki/pyrite/issues/92), [#93](https://github.com/pyrite-wiki/pyrite/issues/93), [#94](https://github.com/pyrite-wiki/pyrite/issues/94), and [#98](https://github.com/pyrite-wiki/pyrite/issues/98).

**Install:** `pip install -e extensions/journalism-investigation`

**Use case:** Local investigative research with sources, claims, entities, and evidence chains. The tools remain available; no tool is removed.

[Full docs](https://github.com/pyrite-wiki/pyrite/tree/main/extensions/journalism-investigation)

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

## Plugin Verification

To install a local plugin and check that its declared entry points load in a fresh
interpreter, run `pyrite extension install ./my-plugin --verify`. Verification uses
the same loader as runtime discovery: each `pyrite.plugins` entry point must load,
instantiate, and expose a `name` attribute. Exit code **3** means installation
succeeded but verification failed, including when the distribution declares no
`pyrite.plugins` entry points. JSON output reports `verified: false` and
`verify_errors`; diagnostic warnings go to stderr.
