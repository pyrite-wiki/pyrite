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

A software KB's board (its lanes, WIP limits and the Definition of Ready and Done gates of ADR-0021) is read from `board.yaml` in the KB's own root. `pyrite init -t software` writes none; with no file, the built-in board applies (five lanes, both gates at `warn`). A file replaces the built-in board whole; nothing is merged. A board.yaml is read only from the root of the KB a call is about: the KB it names, or, for the tools that take an item id, the KB that item is in. With no KB and no item, `sw board`, `sw pull-next` and MCP `sw_review_queue` use the built-in board.

```yaml
lanes:                      # optional; absent means no lanes
  - name: Ready             # required, text, one per lane
    statuses: [accepted]    # required, a list of statuses; a status is in one lane only
  - name: In Progress
    statuses: [in_progress]
    wip_limit: 3            # optional, a whole number, 0 or more
wip_policy: enforce         # optional: warn (default) or enforce
gates:                      # optional; absent means no gates
  in_progress:              # a backlog status: proposed, planned, accepted, in_progress, review, done, retired, deferred, wont_do
    name: Definition of Ready   # optional, text
    policy: enforce         # optional: warn (default) or enforce
    criteria:
      - text: Effort estimated  # text
        checker: has_field  # a checker Pyrite has (pyrite/services/rubric_checkers.py, or no_open_blockers)
        params: {field: effort} # a mapping the checker can use
      - text: Problem statement is clear
        type: judgment      # checker (default), judgment or agent_responsibility
        hint: Add a Problem section   # optional, text
```

These keys are the only ones read; any other key, at any level, is refused rather than ignored. An empty file declares no lanes and no gates.

**Which tools read the board.** MCP `sw_claim`, `sw_transition`, `sw_review` when approving, `sw_pull_next`, `sw_check_ready`, `sw_refine`, `sw_board`, `sw_review_queue` and the software block of `kb_orient`; and the `pyrite sw` commands that call them: `sw check-ready`, `sw refine`, `sw review` (approving), `sw pull-next`, `sw board`, plus `pyrite orient`. `pyrite sw claim`, `pyrite sw transition`, `pyrite sw submit`, MCP `sw_submit` and a `changes_requested` review change status without consulting the board (#1288).

**A board that cannot be loaded is refused.** Every tool above answers one refusal, with a message that names `board.yaml` and the kind of problem and quotes nothing from the file, and changes nothing. The code is `VALIDATION_FAILED` (not retryable), or the KB file reader's own when it refused the read: `OUTSIDE_KB_ROOT` (a link on its path leaves the KB, or more than one hard link), `KB_UNREADABLE`, `INVALID_FRONTMATTER` (over the YAML limits). Refused:

- a file that does not parse, is not UTF-8, is nested too deeply, is not a file (a directory), or is not a mapping;
- a key Pyrite does not read, at any level;
- `lanes` not a list; a lane that is not a mapping, has no `name`, or no `statuses` list; two lanes with the same name, or one status in two lanes; a `wip_limit` that is not a whole number of 0 or more;
- `wip_policy` other than `warn` or `enforce`;
- `gates` not a mapping; a gate keyed by something that is not a backlog status; a gate that is not a mapping, whose `name` is not text, whose `policy` is not `warn` or `enforce`, or whose `criteria` is not a list;
- a criterion that is not a mapping, whose `text` or `hint` is not text, whose `type` is unknown, a checker criterion with no `checker` or naming one Pyrite does not have, `params` that is not a mapping, or `params` its checker cannot use: a parameter it reads that is missing or of the wrong kind (`field`, `heading` and `pattern` must be text that is not blank; `fields` must be a list of names that are not blank; `rubric_text`, if given, must be text) or a pattern that does not compile. These are checked when the file is loaded, so the file is refused by every tool above, not only by those that run the gate;
- a criterion that could not be evaluated on one item (a pattern that takes too long on its body, or a checker that fails): that call is refused, every time it is asked, and the criterion is never recorded as passed. A failure Pyrite did not expect is logged on the server and the refusal says the criterion could not be evaluated.

**Two limits.** A criterion whose `type` is `judgment` or `agent_responsibility` is not run by Pyrite and is recorded as passed, even when it also carries `checker` or `params`: those are not checked when the file is loaded and are never run (#1586). When the `no_open_blockers` lookup itself fails (the index cannot be read, a storage error), the tools that evaluate the gate answer the same "could not be evaluated on this item" refusal (`VALIDATION_FAILED`, not retryable) although the file is not the cause; the cause is in the server log, and `sw_claim` answers the lookup's own error, because it checks dependencies first (#1585).

Accepted: a lane listing a status the workflow does not have (the lane shows what has that status, which is nothing), and keys inside `params` a checker does not read. `kb_orient` and `pyrite orient` still answer: the software block carries `board_error` (the refusal's text), `board_summary` is empty and `recommended_next` is null. A tool called for a KB that does not resolve is refused (`KB_NOT_FOUND`); `sw_refine` (MCP) and `pyrite sw refine` called with no KB are refused.

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
