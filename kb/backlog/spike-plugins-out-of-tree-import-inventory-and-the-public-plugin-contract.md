---
id: spike-plugins-out-of-tree-import-inventory-and-the-public-plugin-contract
title: 'Spike: plugins out of tree — import inventory and the public plugin contract'
type: backlog_item
tags:
- architecture
- plugin
importance: 5
kind: spike
status: proposed
priority: medium
effort: M
rank: 0
---

Maintainer direction, 2026-09-26: the plugins defined what core needs. Now move journalism-investigation (the pilot), cascade, social, encyclopedia and maybe zettelkasten out of tree as separate projects, to show how plugins are built and to allow a wider ecosystem. software-kb stays in tree.

Deliverables:
- The inventory: every pyrite.* symbol each plugin imports. The union is the de facto plugin API; the internals it contains are layer violations to fix first.
- A proposed ADR: 'Extensions live out of tree; the plugin contract is the public API'. It covers the entry-point group, protocols, conformance tests plugins run in their own CI, a compatibility policy, and the trust model (P-K1).
- A template repo outline.

## Spike result (2026-09-26)

The spike read the code and ran no suites. Its findings are in
[[adr-0040]], which was **proposed** when this spike reported (accepted 2026-09-26), and its inventory table has one row per
symbol. The headline numbers:

- The extensions import **43 distinct `pyrite.*` symbols** from 18 modules:
  23 contract, 6 internal and 14 test-only. The security batch adds one
  more, `UNSCOPED`, which makes **24 importable contract symbols**.
- Some contract lives in members that are not imported by name. These are the
  private `Entry._base_kwargs` (37 calls) and `_base_frontmatter` (11 calls),
  seven index reads, five `KBService` writes, the protocol's 20 methods, and
  the validator, hook, MCP-tool and CLI shapes.
- **Internal violation sites in `src/`:**

  | Extension | Sites |
  |---|---|
  | journalism-investigation | 36 |
  | cascade | 17 |
  | social | 31 |
  | encyclopedia | 19 |
  | zettelkasten | 13 |
  | software-kb (stays in tree) | 84 |

  Nearly all of them are bootstrap sites, which #382 removes, or raw SQL and
  index-only writes, which #384 removes.
- cascade imports three journalism-investigation symbols, so it cannot leave
  before JI.
- Core's security-gate tests name JI and cascade tools. They need a fixture
  plugin before JI can leave.

## Groom

The themes are in dispatch order. Themes 1–4 are core prerequisites, and
nothing leaves the tree until they are done. Themes 2 and 3 already exist as
issues; the criteria listed here are what this spike adds to them. The
maintainer accepts ADR-0040, and settles its open decisions 1 (the façade)
and 7 (contract versioning), before theme 1 is dispatched.

### 1. The plugin API façade, the snapshot, and public `Entry` helpers (Opus, M)

- **Acceptance:**
  1. `pyrite/plugin_api.py` re-exports exactly the contract listed in
     ADR-0040 §2 and defines `PLUGIN_API_VERSION`. It also has `TypedDict`s for
     the dict shapes: preset, type metadata, field schema, relationship type,
     workflow, DB table, migration and MCP tool.
  2. `tests/test_plugin_api_snapshot.py` pins the façade's names and
     signatures. Changing the snapshot without a `plugin-api` changelog
     fragment fails CI.
  3. `Entry.base_kwargs` and `Entry.base_frontmatter` are public. The `_`
     names remain as aliases that emit `DeprecationWarning`, and all six
     extensions call the public names.
  4. The registry refuses a plugin whose `requires_plugin_api` major differs
     from core's, with one log line. A plugin that declares nothing still
     loads.
- **Footprint:** new `pyrite/plugin_api.py` and
  `tests/test_plugin_api_snapshot.py`; `pyrite/models/base.py`,
  `pyrite/plugins/registry.py`; `extensions/*/src/*/entry_types.py` (a
  mechanical rename); the changelog-fragment check in `scripts/` and CI.
- **Model:** Opus, because this sets the contract's shape and needs a cold
  read.

### 2. #382: one composition root, plus `plugin_context()` (Opus, L; existing issue)

- **Added criterion:** `pyrite.plugin_api.plugin_context()` returns a
  `PluginContext` built by the runtime, and one extension CLI command uses it
  as the worked example.

### 3. #384: context services (Opus for the API, then Sonnet per extension, L; existing issue)

- **Added criteria:**
  1. `PluginContext.kb_service` exists and is set by both the MCP server and
     the CLI runtime. JI's `investigation_create_entity` succeeds through the
     real `PyriteMCPServer`, which fixes #94.
  2. `ctx.index` exposes `get_entry`, `list_entries`, `search` (which
     sanitises FTS input itself), `get_outlinks`, `get_backlinks`,
     `get_orphans` and `get_kb_stats`. The link and lookup reads take
     `readable_kbs`, with `UNSCOPED` as defined by the security batch.
  3. `ctx.plugin_db` executes SQL only against the plugin's declared
     `<name>_*` tables.
  4. `create_entry`/`create` return the path of the created file, and no
     extension calls `repo._resolve_file_path` or `repo._infer_subdir`.

### 4. The conformance kit and fixture plugin (Opus, M)

- **Acceptance:**
  1. `pyrite.plugins.testing` provides `assert_plugin_conforms(plugin,
     runtime)` and the fixtures `pyrite_runtime`, `scoped_mcp` and
     `cli_runner`. It covers the seven checks in ADR-0040 §6, which are
     generalised from `tests/test_plugin_contract.py`.
  2. `tests/fixtures/example_plugin/` has a cross-KB `kb_names` read tool, a
     per-KB read, a write, a hook and a validator, and it passes the kit.
  3. `test_mcp_tool_registry_is_scoped.py`, `test_mcp_read_scoping.py`,
     `test_mcp_write_scoping.py` and
     `test_every_entry_point_passes_the_policy.py` keep their coverage of
     cross-KB plugin tools through the fixture plugin. They pass with
     journalism-investigation *uninstalled*; to show this, run them in a venv
     without it.
  4. software-kb passes the kit, with its in-tree allowlist written down.
  5. Each MCP tool dict can declare `kb_content: False`. The dispatcher's
     fail-closed rule for undeclared cross-KB tools is unchanged. (This
     coordinates with ADR-0037 theme 4.)
- **Footprint:** new `pyrite/plugins/testing/` and
  `tests/fixtures/example_plugin/`; `pyrite/plugins/registry.py` (tool-name
  collisions refused); `pyrite/server/mcp_server.py` (registration metadata
  only); the four gate tests above; `tests/test_plugin_contract.py`, which
  shrinks to a call into the kit.
- **Model:** Opus. This touches the security gates, so a cold read is
  required.

### 5. journalism-investigation onto the contract, still in tree (Sonnet, M)

- **Acceptance:**
  1. The ratchet from #384 has an empty allowlist for
     `extensions/journalism-investigation/src`. All 36 sites are gone:
     `load_config` and `PyriteDB(` 14 each, index-only `upsert_entry` 6 (in
     `bulk.py`, `dedup.py`, `investigation_setup.py` and `ftm.py`),
     `KBService(` 1, and `SearchService.` 1.
  2. `investigation_bulk_edges` and `investigation_start` write markdown
     files, which fixes #92.
  3. JI's source imports only `pyrite.plugin_api`. Its tests import only that
     and `pyrite.plugins.testing`, which replaces `PyriteDB`, `KBService`, the
     config classes, `PyriteMCPServer` and `MAX_SEARCH_QUERY_LENGTH`.
  4. `tests/test_conformance.py` in the extension passes.
- **Footprint:** `extensions/journalism-investigation/**` only.
- **Model:** Sonnet.

### 6. Extract journalism-investigation (Sonnet, S–M; the maintainer creates the repo and PyPI project)

- **Acceptance:**
  1. A new repo built from the template (ADR-0040 §8) carries the extension's
     history, extracted with `git filter-repo --subdirectory-filter
     extensions/journalism-investigation`. Its CI runs Python 3.11–3.13
     against pyrite at the floor version, at the latest release, and at `dev`
     (informational only), and conformance passes.
  2. `pyrite-journalism-investigation` is published and depends on
     `pyrite>=<minor>,<next>`.
  3. Core deletes `extensions/journalism-investigation/` and
     `tests/test_ji_edge_type_integration.py`, and removes the JI cases from
     `test_plugin_contract.py`, `test_mcp_*_scoping.py`,
     `test_mcp_tool_registry_is_scoped.py` and
     `test_every_entry_point_passes_the_policy.py`. Core CI stays green with
     JI absent.
  4. cascade's dependency on JI becomes a package dependency on the published
     JI, or theme 7 has already deleted cascade.
  5. `scripts/new-worktree.sh`, CI's `extensions/*/` loops and the docs
     (README, getting-started) install JI from PyPI, through a
     `pyrite[journalism]` extra if decision 6 says so.
- **Footprint (core):** `extensions/journalism-investigation/` (deleted),
  the test files above, `.github/workflows/ci.yml`, `pyproject.toml`,
  `scripts/new-worktree.sh`, `README.md`, a changelog fragment.
- **Model:** Sonnet.

### 7 and later. cascade, social, encyclopedia, zettelkasten (Sonnet each)

Each extension repeats themes 5 and 6 as its own pair. The site counts are
cascade 17, social 31, encyclopedia 19 and zettelkasten 13.

- **cascade** depends on ADR-0040 decision 2. If the answer is delete, the
  work is `kb/notes/remove-cascade-plugin.md` and its three
  `ji-absorb-cascade-*` subtasks, done before theme 6.
- **social** needs `ctx.plugin_db` inside hook context, because its hooks
  write `social_*` tables.
- **zettelkasten** waits on decision 4.

**Model:** Sonnet for each pair.
