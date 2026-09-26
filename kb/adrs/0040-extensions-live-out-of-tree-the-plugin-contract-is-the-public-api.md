---
id: adr-0039
title: Extensions live out of tree; the plugin contract is the public API
type: adr
importance: 5
adr_number: 40
status: accepted
date: '2026-09-26'
tags: [architecture, plugins, extensions, api, compatibility, testing]
links:
- target: adr-0002
  relation: amends
  kb: pyrite
- target: adr-0014
  relation: related
  kb: pyrite
- target: adr-0029
  relation: related
  kb: pyrite
- target: adr-0037
  relation: related
  kb: pyrite
- target: spike-plugins-out-of-tree-import-inventory-and-the-public-plugin-contract
  relation: related
  kb: pyrite
---

# ADR-0040: Extensions live out of tree; the plugin contract is the public API

> **Proposed** (spike, 2026-09-26). The maintainer accepts or rejects it. The
> direction is already set: journalism-investigation moves out first, then
> cascade, social, encyclopedia and perhaps zettelkasten, and software-kb stays
> in tree. This ADR says what the contract is, how it is kept, and what has
> to change before the first extension can leave. The decisions left open are
> listed at the end.

## Context

Six extensions live under `extensions/`. Each has its own `pyproject.toml`, an
entry point in the `pyrite.plugins` group, and `dependencies = []  # pyrite is
a peer dependency`. The packaging is already out of tree; the code is not.
Because they live in the monorepo, the extensions reached into core wherever
that was convenient, and no test noticed.

The extensions did useful work. They defined the parts of core that other
code now relies on: the `PyritePlugin` protocol and its capability split
(ADR-0002 addenda), structural protocols (ADR-0014), claimable work, and the
`kb_scope_clause` narrowing (#223). Moving them out makes the boundary real.
From then on, core can break a plugin only by changing something it
published.

### The inventory (measured on `dev` 4f72d76e)

These are all the `pyrite.*` imports under `extensions/`, found by an AST walk
of every `.py` file. The walk counts imports inside functions as well as
module-level ones. There are **43 distinct symbols in 18 modules**. Calls
through those objects were found with grep: `db.<m>(`, `svc.<m>(`,
`repo._<m>`, `_raw_conn`, `_backend`. The commands are in the footnote.

Each symbol has one of three marks. **contract** means it is public and
stable. **internal** means it is a layer violation to remove before the
extension can move. **test-only** means it is imported only by tests, and
`pyrite.plugins.testing` replaces it (below).

| Module | Symbol | Used by (src; `t` = tests only) | Mark |
|---|---|---|---|
| `pyrite.plugins.capabilities` | `Capability` | all six | contract |
| `pyrite.plugins.context` | `PluginContext` | social t, software-kb t; injected into all six | contract |
| `pyrite.plugins.scoping` | `kb_scope_clause` | encyclopedia, social, software-kb | contract |
| `pyrite.plugins.registry` | module, `PluginRegistry`, `get_registry` | encyclopedia t, social t, software-kb t, zettelkasten t | test-only |
| `pyrite.models.base` | `Entry` | cascade, journalism, social | contract |
| `pyrite.models.core_types` | `NoteEntry`, `EventEntry`, `PersonEntry`, `OrganizationEntry`, `TopicEntry`, `DocumentEntry` | all six | contract |
| `pyrite.models.core_types` | `entry_from_frontmatter`, `get_entry_class` | five, tests only | test-only |
| `pyrite.models.protocols` | `Assignable`, `Locatable`, `Statusable`, `Temporal` | cascade, software-kb | contract |
| `pyrite.schema` | `EventStatus`, `ResearchStatus`, `generate_entry_id` | cascade, journalism, social, software-kb | contract |
| `pyrite.schema` | `get_all_relationship_types`, `get_inverse_relation` | software-kb t, zettelkasten t | test-only |
| `pyrite.exceptions` | `PyriteError`, `QueryTooLongError` | journalism, social, software-kb, zettelkasten | contract |
| `pyrite.exceptions` | `StorageError` | tests only | test-only |
| `pyrite.config` | `PyriteConfig`, `KBConfig` | as the types of `ctx.config` / `get_kb()`; tests construct them | contract (read-only view) |
| `pyrite.config` | `Settings` | tests only | test-only |
| `pyrite.config` | `load_config` | all six | **internal** (bootstrap) |
| `pyrite.services.kb_service` | `KBService` | five | contract *as the type of `ctx.kb_service`*; constructing it is **internal** |
| `pyrite.services.search_service` | `SearchService` | journalism (`sanitize_fts_query`) | **internal** |
| `pyrite.services.search_service` | `MAX_SEARCH_QUERY_LENGTH` | journalism t | test-only |
| `pyrite.services.rubric_checkers` | `NAMED_CHECKERS` | software-kb | **internal** (stays in tree) |
| `pyrite.services.rubric_checkers` | `check_orphan_backlog_item` | software-kb t | test-only |
| `pyrite.services.hook_runner` | `HookRunner` | social t | test-only |
| `pyrite.storage.database` | `PyriteDB` | all six | **internal** |
| `pyrite.storage.repository` | `KBRepository` | social, software-kb, zettelkasten | **internal** |
| `pyrite.storage.index` | `IndexManager` | tests only | test-only |
| `pyrite.server.mcp_server` | `PyriteMCPServer` | journalism t | test-only |
| `pyrite.utils.errors` | `cli_error` | journalism, software-kb, zettelkasten | contract |
| `pyrite.utils.yaml` | `load_yaml_file` | software-kb | **internal** (stays in tree) |

The totals are 23 contract, 6 internal and 14 test-only. The private security
batch, which is not yet on `dev`, adds **one contract symbol**:
`pyrite.services.access_policy.UNSCOPED`. It also adds the `readable_kbs`
keyword that the link and lookup reads now require. That makes **24 importable
contract symbols**.

The imports alone understate the contract. The extensions also use members
that are not imported by name:

- **Private `Entry` helpers.** All six extensions subclass `Entry` through
  `cls._base_kwargs(...)`, called 37 times, and `self._base_frontmatter()`,
  called 11 times. These are private names that nevertheless carry the
  contract, so they must become public.
- **Index reads through `ctx.db`/`PyriteDB`.** The calls are `get_entry` (23),
  `list_entries` (35), `search` (4), `get_outlinks` (13), `get_backlinks` (8),
  `get_orphans` (1) and `get_kb_stats` (1). software-kb, which stays, also uses
  `get_reviews` and `create_review`.
- **Writes through `KBService`.** The calls are `create_entry` (9),
  `update_entry` (2), `claim_entry` (7), `bulk_create_entries` (1) and
  `create` (1).
- **The protocol surface.** This is `PyritePlugin`'s 20 methods, plus `name`
  and `capabilities`. It also covers the validator signature
  `(entry_type, fields, ctx) -> list[{severity, message}]` and the hook
  signature `(entry, ctx)`, both enforced at registration (#379), and the
  hook names `before_save`, `after_save` and `after_delete`. The MCP tool
  shape is `{description, inputSchema, handler}`, where `handler(args, *,
  readable_kbs=None)` returns a dict. The KB argument names are fixed by
  `KB_ARGUMENT_NAMES`, and a CLI command is `(name, typer.Typer)`.

### Internal violations per extension (source files only)

| Extension | Sites | Breakdown |
|---|---|---|
| journalism-investigation | **36** | `load_config` 14, `PyriteDB(` 14, index-only `upsert_entry` 6 (#92), `KBService(` 1, `SearchService.` 1; its write tools also rely on a `ctx.kb_service` that `PluginContext` does not have (#94) |
| cascade | **17** | `load_config` 6, `PyriteDB(` 6, `_backend._session` raw SQL 1 (+2 lines of `getattr`), `upsert_entry` 1, `KBService(` 1; **imports three journalism-investigation symbols** (`InvestigationEventEntry`, `query_network`, `parse_meta`) |
| social | **31** | `_raw_conn` 17 (own tables and `entry`), `load_config` 6, `PyriteDB(` 5, `KBService(` 1, `KBRepository(` 1 + `repo._resolve_file_path`/`_infer_subdir` 1 |
| encyclopedia | **19** | `_raw_conn` 9 (own table and `entry`), `load_config` 5, `PyriteDB(` 5 |
| zettelkasten | **13** | `load_config` 5, `PyriteDB(` 5, `KBService(` 1, `KBRepository(` 1 + private repo methods 1 |
| software-kb (stays) | **84** | `_raw_conn` 27, `load_config` 27, `PyriteDB(` 14, `KBService(` 10, `NAMED_CHECKERS` 2, `KBRepository(` + private repo 2, `load_yaml_file` 1 |

Almost every violation belongs to one of three shapes that #382 and #384
already name:

1. **Bootstrap.** An extension builds its own `load_config()` and `PyriteDB()`,
   in CLI commands and in a `_get_db()` fallback. #382 (one composition root)
   removes this.
2. **Raw SQL and index-only writes.** Queries go through `_raw_conn` and
   `_backend._session`, and `upsert_entry` writes to the index with no
   markdown file behind it. #384 removes this by adding context services and a
   `plugin_db` limited to the plugin's own tables.
3. **Recomputing what core knows.** Three extensions derive the new file's
   path with `repo._resolve_file_path(entry, repo._infer_subdir(entry))`
   after a create, because `create_entry` does not return it.

### Core depends on the extensions too

Core's test suite names extension tools and symbols. The tests that do so are
`test_mcp_tool_registry_is_scoped.py` (JI tool list),
`test_every_entry_point_passes_the_policy.py` (`MCP_NOT_YET_MIGRATED`, with 30+
`investigation_*`/`cascade_*` names), `test_mcp_write_scoping.py`,
`test_mcp_read_scoping.py`, `test_plugin_contract.py` (cascade and JI cases),
`test_ji_edge_type_integration.py`, `test_plugin_integration.py` (social,
encyclopedia, zettelkasten) and `test_verify_red_ci.py`. CI also loops over
`extensions/*/` in five jobs, and so do `scripts/new-worktree.sh` and
`pyproject.toml`'s `testpaths`.

The security gates matter most here. They are core tests that enumerate
plugin tools by name. That works only while core can see every plugin, and an
out-of-tree plugin gives the gate nothing to list. The property has to move
into a kit that each plugin runs, so the check no longer depends on core
listing every tool.

## Decision

### 1. Extensions are separate projects; core publishes one contract

Every extension except software-kb becomes its own repository and its own
PyPI distribution. software-kb stays in tree because Pyrite's own process
runs on it. It is still held to the contract through the same conformance kit
(section 6), so it keeps showing what a plugin may do. It is allowed only a
named in-tree allowlist of extras (`NAMED_CHECKERS`, reviews).

Core publishes **one façade module, `pyrite.plugin_api`**. It re-exports the
contract symbols, and nothing outside it is promised. The existing import
paths keep working, because the façade re-exports and does not move anything.
Only the façade is covered by the compatibility policy. The conformance kit
fails a plugin that imports any other `pyrite.*` module outside its tests.

### 2. What is public

| Area | Contract |
|---|---|
| Discovery | Entry-point group `pyrite.plugins`. The value is a class, instantiated with no arguments. It has a unique `name` (`[a-z][a-z0-9_]*`) and a `capabilities: set[Capability]` (ADR-0002). |
| Protocol | `PyritePlugin`, with its 20 methods and the `Capability` → method map in ADR-0002. New methods are optional; they are added, never required. |
| Context | `PluginContext` holds `config` (a read-only `PyriteConfig` view: `get_kb`, KB listing via the registry per #382), `kb_name`, `user`, `operation`, `kb_type`, `kb_schema`, `extra`, `get`, `search_semantic`. It gains **`kb_service`** (writes: `create_entry`, `update_entry`, `claim_entry`, `bulk_create_entries`, `create`, each returning the entry's path), **`index`** (reads: `get_entry`, `list_entries`, `search` with FTS sanitising inside, `get_outlinks`, `get_backlinks`, `get_orphans`, `get_kb_stats`; the link and lookup reads take `readable_kbs`, and `UNSCOPED` is the explicit unscoped value) and **`plugin_db`** (SQL limited to the tables the plugin declared in `get_db_tables()`). `ctx.db` is deprecated for plugins and leaves the contract when #384 lands. |
| Models | `Entry` with `entry_type`, `to_frontmatter`, `from_frontmatter`, and **`base_kwargs` / `base_frontmatter`** (renamed from `_base_kwargs` / `_base_frontmatter`, with the private names kept as aliases for one minor). Also `NoteEntry`, `EventEntry`, `PersonEntry`, `OrganizationEntry`, `TopicEntry`, `DocumentEntry`, and the protocols `Assignable`, `Locatable`, `Statusable`, `Temporal` (ADR-0014). |
| Schema | `generate_entry_id`, `EventStatus`, `ResearchStatus`. The dict shapes for types, field schemas, presets, relationship types, workflows, DB tables and migrations are specified as `TypedDict`s in the façade. |
| Errors | `PyriteError`, `QueryTooLongError`. An MCP handler returns `{"error": ...}` or raises `PyriteError`, and ADR-0037's error contract shapes what the caller sees. |
| Scoping | `kb_scope_clause` and `UNSCOPED`. |
| CLI helpers | `cli_error`, plus a new `plugin_context()` that gives a CLI command a context from the runtime #382 builds. |
| Testing | `pyrite.plugins.testing` (section 6). |

Everything else is internal, including `PyriteDB`, `KBRepository`,
`IndexManager`, `SearchService`, `HookRunner`, `PyriteMCPServer`,
`load_config`, `mcp_server.*` and `server.*`. A plugin that needs something
internal asks for it to be added to the contract.

### 3. How a plugin registers things

- **Types.** A plugin registers types through `get_entry_types`,
  `get_type_metadata`, `get_field_schemas`, `get_collection_types`,
  `get_protocols`, `get_kb_types` and `get_kb_presets`. A KB selects a type
  or preset by name, and it can select only among the ones installed
  plugins registered.
- **MCP tools.** `get_mcp_tools(tier)` returns every tool the plugin serves
  up to `tier`. A tool's tier is the lowest tier that returns it; this is
  today's rule in `_register_plugin_tools`. Tool names are namespaced
  `<prefix>_*`, and the prefix is declared once on the plugin. A name that
  collides with a core tool or another plugin's tool is **refused at
  registration**, and the plugin's tool is the one dropped.
  Each tool states how it relates to KB content, so core's gate needs no list
  of plugin tool names. There are three ways a tool can do this:
  - it names its KB through a `KB_ARGUMENT_NAMES` parameter;
  - it spans KBs and declares `readable_kbs`;
  - it declares `kb_content: False`.
  This is the tool-level `Action` metadata of ADR-0037 theme 4, and a tool
  that declares none of these is refused for a scoped caller, as today.
- **CLI.** `get_cli_commands()` returns `(name, typer.Typer)`, mounted as
  `pyrite <name>`, and the name must not collide with a core command. Commands
  get their dependencies from `plugin_context()`, and never from
  `load_config()`. Typer's major version is part of the contract, pinned by
  core.
- **Storage.** A plugin's own tables come from `get_db_tables()`, and each
  name must start with `<name>_`. Columns come from `get_db_columns()` and
  migrations from `get_migrations()`. A plugin never writes core tables. Every
  entry it creates is created through `ctx.kb_service`, so it has a file
  (Knowledge-as-Code; the #92 property).
- **REST.** Plugins register no REST routes. REST clients reach plugin types
  through the generic entry endpoints (ADR-0031). Plugin-specific REST is a
  later ADR, if it is ever needed.
- **Plugin to plugin.** One plugin may depend on another as an ordinary
  package dependency on its public API. cascade → journalism-investigation is
  the one case today. It should use ADR-0014 protocols where it can, instead
  of importing classes.

### 4. Compatibility policy

- The façade carries `PLUGIN_API_VERSION = (major, minor)`. A plugin
  declares `requires_plugin_api = (1, n)`. The registry refuses a plugin with
  a different major and logs one clear line. A higher minor is not refused:
  the registry warns, and the plugin still loads.
- While Pyrite is 0.x, plugins pin core by minor, for example
  `pyrite>=0.27,<0.28`. The template (below) runs CI against the floor, the
  latest release, and `dev`. The `dev` job is informational: it warns early
  and does not gate. Contract minor bumps ride on core minor releases.
- **Deprecation window: one minor release.** A contract symbol or signature
  that changes keeps a shim that emits `DeprecationWarning` for the whole of
  the next minor release, and is removed in the one after it. At 1.0 the
  window becomes one major version.
- A core test, `tests/test_plugin_api_snapshot.py`, pins the façade's names
  and signatures. Changing the snapshot needs a `plugin-api` changelog
  fragment. The pre-push and CI fragment checks already exist; this adds
  one more category.

### 5. Trust model

Plugins are code the operator installs and trusts. They run in the server and
CLI process with that process's full privileges, and there is no sandbox. The
access policy (ADR-0037) guards what *callers* can reach through the plugin's
surfaces. It does not guard core against the plugin.

A plugin is discovered only from the installed Python environment through its
entry point. **Nothing in a KB's files can install, import or enable one
(P-K1).** A KB's `kb.yaml` can select a `kb_type` or preset that an installed
plugin provides. It can never name a module, an entry point, a package or a
URL.

Validators and hooks are selected per KB type among installed plugins, and a
KB cannot supply one. The contract forbids a plugin to treat a KB field as
an import path, a command or a URL to fetch; the plugin author signs up to
this, and the kit cannot prove it. The operator's `plugins` list in
`config.yaml` (open decision 3) can narrow which of the installed plugins
load. That list sits in the operator's configuration, never in the KB.

### 6. Conformance tests an external plugin runs in its own CI

Core ships **`pyrite.plugins.testing`**. It is built from
`tests/test_plugin_contract.py`, turned from a sweep over the installed
plugins into a check of one plugin that a plugin runs on itself. A plugin's
entire conformance test is:

```python
from pyrite.plugins.testing import assert_plugin_conforms
from my_plugin.plugin import MyPlugin

def test_conforms(pyrite_runtime):          # fixture from the kit
    assert_plugin_conforms(MyPlugin(), pyrite_runtime)
```

`assert_plugin_conforms` checks the following:

1. The entry point resolves, and `name` and `capabilities` are present. A
   method that is implemented but not declared, or declared but not
   implemented, is reported.
2. Validators bind `(entry_type, fields, ctx)` and return
   `list[{severity, message}]` (`TestValidatorSignatures`,
   `TestSeveritySplitsErrorsFromWarnings`,
   `TestRunValidatorsNormalizesReturnShape`). Hooks bind `(entry, ctx)`, and
   their names are among the known hook points (`TestHookSignatures`).
3. Every entry type survives the round trip
   `to_frontmatter → from_frontmatter → to_frontmatter` unchanged, and every
   preset names only registered types.
4. MCP tools have the right shape and a valid `inputSchema`. They are
   prefixed, and their KB-bearing parameters use only `KB_ARGUMENT_NAMES`.
   Each tool declares one of the three KB relations in section 3.
5. **Scoping holds, generically.** Every read tool is dispatched through the
   real `_dispatch_tool` as a caller scoped to one KB, with a second KB
   holding data. No result carries the second KB's content. This is the
   property `test_mcp_tool_registry_is_scoped.py` pins today, stated so a
   plugin can run it on itself.
6. **Writes make files.** After each write tool runs, every index row that
   the plugin's writes created has a markdown file. This is the #92 property.
7. The plugin's source imports no `pyrite.*` module except `pyrite.plugin_api`,
   and its tests import only that and `pyrite.plugins.testing`. The check is
   an AST walk, the same one this ADR's inventory used.

The kit also provides fixtures. `pyrite_runtime` is a temporary config
directory holding two KBs, an index and a `PluginContext`. `scoped_mcp` is a
dispatcher acting as a scoped principal. `cli_runner` runs the plugin's Typer
app with `plugin_context()` wired in. These replace every test-only symbol in
the inventory.

Core's own CI runs the kit against software-kb, and against a small
**fixture plugin** in `tests/fixtures/example_plugin/`. The fixture plugin
has a cross-KB `kb_names` tool, a per-KB read, a write, a hook and a
validator. Core's security gates keep their coverage through that fixture
plugin instead of through journalism-investigation's tool names.

### 7. Migration order

0. **Core prerequisites, all in tree.**
   - The security batch lands.
   - #382: one composition root, and `plugin_context()`.
   - #384: `ctx.kb_service`, `ctx.index` and `ctx.plugin_db`, with a ratchet
     test.
   - The façade, the snapshot test and the public `Entry` helpers.
   - The conformance kit and the fixture plugin.
   - ADR-0037 theme 4's tool metadata.
1. **journalism-investigation (pilot).** Bring it onto the contract in tree:
   36 violation sites go to zero, and its tests move onto the kit. Then
   extract it with its git history and publish it. Core removes it along with
   the tests and CI lines that name it.
2. **cascade.** It depends on JI, so it moves after JI. Alternatively, finish
   the absorption into JI and delete it (open decision 2).
3. **social** has 31 sites. Its hooks write its own tables, so it needs
   `plugin_db` in hook context.
4. **encyclopedia** has 19 sites.
5. **zettelkasten** has 13 sites, the fewest. It is also a `pyrite init`
   preset (`pyrite/cli/init_command.py`) and an option on the web KB settings
   page (open decision 4).

software-kb stays. It runs the kit in core's CI, and its allowlist is written
into the ratchet test.

### 8. Template repository outline

The template is `pyrite-wiki/pyrite-plugin-template`:

```
pyproject.toml         # hatchling; [project.entry-points."pyrite.plugins"];
                       # dependencies = ["pyrite>=0.27,<0.28"];
                       # [project.optional-dependencies] test = ["pyrite[test]>=0.27,<0.28", "pytest"]
src/pyrite_<name>/
  __init__.py
  plugin.py            # name, prefix, capabilities, requires_plugin_api; thin methods
  entry_types.py       # Entry subclasses via base_kwargs / base_frontmatter
  preset.py            # KB preset (TypedDict from pyrite.plugin_api)
  validators.py  hooks.py
  mcp.py               # tools: handler(args, *, readable_kbs=None) using ctx.index / ctx.kb_service
  cli.py               # typer app; plugin_context() for dependencies
  tables.py            # <name>_* tables, if any
tests/
  test_conformance.py  # the three lines in section 6
  test_<feature>.py    # uses pyrite_runtime / scoped_mcp / cli_runner
.github/workflows/ci.yml   # py 3.11–3.13 × pyrite {floor, latest, dev (informational)}; ruff; pytest
.pre-commit-config.yaml    # ruff, ruff-format
README.md              # install, trust note ("install only plugins you trust"), compatibility line
CHANGELOG.md  LICENSE
```

## Consequences

**Easier:**

- Core can refactor anything behind the façade without asking which
  extension reaches in.
- A third party gets a template, a kit and a stated promise.
- The security gates follow a plugin wherever it lives, because the plugin
  runs them itself.
- The layer violations #382 and #384 are chasing now have an end state that
  a test checks.

**Harder:**

- Two-repo changes. A contract change needs a core release before a plugin
  can use it, and journalism work that needs new core API waits one release.
- The façade and the snapshot are a new promise. Every change to them now
  costs a changelog fragment and a deprecation window.
- CI splits across repos. Nothing in core's CI tells the maintainer that a
  published plugin broke on `dev`, unless the canary job runs (open decision
  5).

**Cost:** the prerequisites are the bulk of the work: #382 (L, Opus), #384
(L, Opus then Sonnet), the façade and kit (M, Opus). After them, each
extension is one in-tree theme and one extraction theme.

## Decisions (maintainer, 2026-09-26: accepted with these)

1. **A façade module, `pyrite.plugin_api`.** Everything not exported there is internal. A snapshot test pins the contract.
2. **cascade is deleted, not extracted.** Its remaining pieces are absorbed into journalism-investigation (`kb/notes/remove-cascade-plugin.md`).
3. **An operator allowlist.** `plugins: [..]` in `config.yaml` narrows which installed plugins load, and defaults to all installed.
4. **zettelkasten moves out after journalism-investigation, not first.** It stays in tree as the reference example until the template repo exists and `pyrite init` can install it on demand, or through `pyrite[zettelkasten]`.
5. **A nightly canary job**, running each published plugin's conformance test against `dev`.
6. **The plugins live in the `pyrite-wiki` org,** one repo per plugin, on PyPI under the existing `pyrite-<name>` names. Core gets a `pyrite[journalism]` extra.
7. **A separate `PLUGIN_API_VERSION`.** Core minors ship without a contract change.

**Sequencing.** Themes 1–4 (the façade, plugin wiring, context services and the conformance kit) are 0.27 core work, after G1. They are worth doing even if nothing moves. Theme 3 fixes #94 and #92. Extracting journalism-investigation is the last step of 0.27.
---

<small>Inventory commands, run in a worktree at `dev` 4f72d76e. Imports: an
`ast.walk` over `extensions/*/**/*.py` collecting `ImportFrom`/`Import` whose
top-level package is `pyrite`, with `src` and `tests` kept apart. Violations:
`grep -rnE '_raw_conn|_backend|upsert_entry\(|repo\._|load_config\(|PyriteDB\(|KBService\(|KBRepository\(|SearchService\.|NAMED_CHECKERS|load_yaml_file' --include=*.py extensions/<ext>/src`.
Method use: `grep -rhoE '\b(db|_db|ctx\.db)\.<m>\('` and `'\b(svc|kb_service|service)\.<m>\('`.
Private `Entry` helpers: `grep -rc '_base_kwargs\|_base_frontmatter' extensions/*/src`.</small>
