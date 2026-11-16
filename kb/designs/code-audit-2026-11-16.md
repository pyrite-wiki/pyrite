---
id: code-audit-2026-11-16
type: design
title: "Code audit, 2026-11-16: where the code does not hold the design's invariants"
status: draft
author: conductor (Fable audit)
date: "2026-11-16"
reviewers: []
tags: [audit, architecture, quality]
links:
- target: test-architecture-review-2026-10
  relation: related
  kb: pyrite
- target: adr-audit-2026-10
  relation: related
  kb: pyrite
---

# Pyrite code audit, 2026-11-16

Audited: `dev` at 1b93dec5 (v0.26.3 plus five commits), read-only worktree
`a read-only worktree`. No code written, nothing filed.
The full suite was not run. Targeted runs only: `tests/test_doc_write_as_patch.py`
(33 passed, 2 strict xfail) and `tests/test_entry_file_writers_are_one_function.py`
with `tests/test_get_reads_the_file.py` (44 passed).

**Evidence marks.**
- **[checked]**: I read the code at that line or ran the command myself.
- **[sweep]**: a delegated read-only sweep read it and gave file:line; I did not re-read every site. Counts marked this way are reproducible with the scripts under `scratchpad/audit/sweepA` to `sweepD`.
- **[assumed]**: inferred, not read.

**Repro environment.** Every repro below uses fresh directories:

```
A=<scratch>/audit
export HOME=$A/env1/home PYRITE_CONFIG_DIR=$A/env1/cfg PYRITE_DATA_DIR=$A/env1/data
PY=<checkout>/.venv/bin/pyrite
$PY init -t software -p $A/env1/kbs/sw  -n sw  --no-examples
$PY init -t research -p $A/env1/kbs/res -n res --no-examples
```

Security findings were reported privately.

---

## (a) Summary: five structural themes

The code has a habit that works: when a question drifted three times, someone
built one function for it and a walking test (the KB resolver, the frontmatter
splitter, the entry writer, field projection). The findings below are the
questions that have not had that treatment yet. Four of the five themes are one
missing model each; the fifth is the size of the two objects everything passes
through.

| # | Theme | Invariant to establish | One enforcement point | Structural test | Size |
|---|---|---|---|---|---|
| 1 | **A type is one schema per KB.** Today "what is type T in KB K" has nine answers, and "which plugins apply to K" is read from the registry's `kb_type`, which `pyrite init` always writes as `generic`. | For a (KB, type) pair, one resolved spec answers class, fields, defaults, validity and which plugins apply, and it is derived from the KB's own `kb.yaml`. The same files give the same behaviour however the KB was registered. | One `type_spec(kb, type)` on the KB schema; `get_entry_class` and `get_all_entry_types` become private to it. | AST walk: no call to `get_entry_class`, `get_all_entry_types` or a `kb_type` comparison outside that module. Property test: one KB directory registered two ways gives byte-identical writes and identical findings; write validation and `qa validate` agree on a fixed value matrix. | L (0.27 design, lands before the 0.28 contract freezes) |
| 2 | **A plugin passes the same single points as core.** Extensions construct their own config, database and service, write SQL on shared tables, and build their own error dicts. | A plugin handler receives a context (resolved KB, scope, identity, database handle) and returns a result or raises a `PyriteError`. It constructs nothing and imports only the facade. | The dispatcher and `PluginContext`; `pyrite.plugin_api`. | The ADR-0040 conformance kit as a shrink-only ratchet now, over `extensions/*/src`: no `PyriteDB(`, `load_config(`, `KBService(`, `_raw_conn`, no import outside the facade, no dict literal with an `"error"` key. | XL (this is 0.28; the ratchet is S and can start now) |
| 3 | **A name is resolved once, at the boundary; inner code takes a handle.** The one resolver exists, but about 110 `config.get_kb(name)` sites behind it each decide what an unknown KB means, and a KB's description and type live in three places. | A service method takes a resolved KB, never a name. An unknown KB is one refusal, raised once. A KB's attributes have one home. | `kb_resolver.resolve_kb` returns the handle; `PyriteConfig.get_kb` becomes private to the registry module (the registry map's invariant R2, which no test pins today). | AST ratchet: `.get_kb(` outside the registry module, list shrinks to zero. | M to L (0.27, with ADR-0039) |
| 4 | **A failure is never a success-shaped answer.** 312 broad handlers, 119 bare `{"error": ...}` dicts, 159 hand-built `HTTPException`s, 58 error codes that belong to no exception class, and MCP never sets `isError`. | A handler returns a result or raises. A guard that cannot judge refuses, or the response says the check did not run. One table of codes. | The three transport boundaries (`_dispatch_tool`, the REST `PyriteError` handler, the CLI root) plus ruff `BLE001`. | Shrink-only baselines for: broad `except`, dict literals with `"error"` and no `error_code`, `HTTPException(` outside the builder, code literals not in the table. | M, incremental (the lint and baselines are S and fit 0.26.4) |
| 5 | **A surface translates; the service decides.** CLI, MCP and REST each prepare a write differently, and the two objects they all pass through are the largest and most-changed files in the tree. | One write request shape, built by one function; a surface parses its transport and nothing else. | A single `KBService` write entry taking that request. | The cross-surface parity test fed by one surface registry (the October review's Q5). | L (0.27, after theme 1) |

Order: 4's lint and baselines first (cheap, stops growth), then 3 with the
registry work, then 1, then 5, with 2 running as a ratchet throughout and
finishing in 0.28.

---

## (b) Findings, ranked

Severity: **High** = wrong results or lost validation without an error, or a
blocker for 0.27/0.28. **Medium** = inconsistency a user or contributor hits.
**Low** = hygiene.

### High

**H1. Which plugins apply to a KB is decided by the registry's `kb_type`, and `pyrite init` always registers `generic`.** [checked]
- Code: `pyrite/cli/init_command.py:549` `kb_config = KBConfig(name=kb_name, path=path, kb_type="generic")`. `pyrite/plugins/registry.py:_plugin_matches_kb_type` (882) returns `kb_type in plugin_kb_types`. `KBService._hook_ctx` (`kb_service.py:1443`) and `storage/index.py:1599` pass `kb_config.kb_type`. software-kb `get_orient_supplement` (`plugin.py:1526`) returns `None` unless `kb_type == "software"`.
- Repro: copy `kbs/sw` to `kbs/sw2`, rename it in `kb.yaml`, `pyrite kb add $A/env1/kbs/sw2 --name sw2 --type software`. Then `pyrite create -k <kb> -t adr --title "Use sqlite" -b x --allow-undeclared` returns a `date_recommended` warning on `sw2` and none on `sw`; `pyrite orient -k sw2` has a `software` block and `-k sw` does not. Same files.
- Breaks: principle 5 (the file decides) and principle 8. The types map lists #572 as a known gap; it is still true and the cause is wider than `init`.
- Blast radius: every KB made by the documented first-hour path runs without its plugin's validators, hooks and orient data.
- Issue: "A KB made by `pyrite init -t <template>` is registered with that template's type" (0.26.4, small). Then "Which plugins apply to a KB is read from its kb.yaml" (0.27).

**H2. The class for a type name is looked up globally, not per KB.** [checked]
- Code: `pyrite/models/core_types.py:get_entry_class` (423) takes no KB: core registry, then `get_registry().get_all_entry_types()` for every installed plugin, then `GenericEntry`; a plugin failure is caught by `except Exception` and silently yields `GenericEntry`. Callers: `kb_service.py:1670, 2047, 2446, 2475`, `storage/backends/base_backend.py:469` (the read shape), `models/factory.py:51`, `schema/field_aliases.py:89, 618`, `cli/schema_commands.py:601`, `cli/protocol_commands.py`. Only `kb_service.py:1400` uses the scoped `get_all_entry_types_for_kb`. The registry's own docstring (`registry.py:1017`) describes the bug class this causes.
- Breaks: principle 6 (a type is a schema), principle 8. #468 (0.26.4 milestone) is one instance.
- Blast radius: what a read returns and what a create writes for type T depend on which packages are installed in the process. This is the first thing an out-of-tree plugin will collide on.
- Issue: "Type resolution takes the KB: one `type_spec(kb, type)`" (0.27; do not freeze the 0.28 plugin contract before it).

**H3. Whether a value is valid is decided per call site, so the write path accepts what QA then flags.** [checked]
- Code: `validate_importance` (`schema/validators.py:103`) is called from `EventEntry.validate` (`models/core_types.py:223`) and twice in QA (`qa_service.py:925, 1179`). No other type's write checks it, though every entry carries `importance`. REST bounds it in pydantic (`server/schemas.py:467, 484`); the MCP schema only describes it (`tool_schemas.py:844, 874`); the CLI has no bound on create (`entry_commands.py:318`).
- Repro: `pyrite create -k res -t person --title "Jane Roe" -b x -f affiliations=acme -f importance=99` succeeds and writes `importance: 99` and `affiliations: acme` (a bare string for a `list[str]` field, `schema/core_types.py`). `pyrite qa validate res` then reports `importance_range`. MCP `kb_create` and `kb_update` do the same (`importance: 99`, then `77`). The same value on an `event` is refused.
- Breaks: principle 8; the types map's first invariant covers enums only.
- Blast radius: agents write values the tool's own QA rejects; both hallway tests on 2026-11-16 hit it (research friction 4 and 5, software friction 8).
- Issue: "A field's range and shape are checked by one function on every write and by QA" (importance for all types: 0.26.4; list-typed fields: 0.27 with H2).

**H4. A plugin validator or gate that fails to run is treated as passed.** [checked]
- Code: `plugins/registry.py:348` (`get_validators` raising leaves an empty list, logged), `:1068` (a raising validator is logged and skipped), `storage/index.py:1600` (validators fail to load, the health checks for that KB are skipped). software-kb `plugin.py:2754-2768`: `try: board_config = load_board_config(...) except Exception: pass`, then the gate is evaluated against the empty preset; the same shape at `:1548, :1937, :2546, :2928` [sweep].
- Breaks: principle 8 and the design's "reported, never silently repaired"; `kb/backlog/in-band-degradation-signaling.md` tracks the signalling side.
- Blast radius: a broken `board.yaml` silently turns off WIP and definition-of-ready gates for claim and submit; a plugin bug turns off its validation with no sign in the response.
- Issue: "A broken board config refuses the transition instead of disabling the gate" (0.26.4). "A validator that did not run is named in the response" (0.27).

**H5. Services re-resolve the KB by name and each decides what "unknown" means.** [checked]
- Code: 110 `.get_kb(` call sites outside the resolver (`grep -rn "\.get_kb(" pyrite extensions/*/src --include=*.py | wc -l`). `qa_service.py` has 13; at `:1234, :1313, :1481, :1678` an unknown KB is `return` with no finding, at `:1777, :1879, :1990` it means "no schema". `storage/index.py:2290` raises `KBNotInConfigError`, `:546` falls back to the file's parent directory. `PyriteConfig.get_kb` (`config.py:1008`) reads config then the registry cache; only `kb_resolver._lookup` also asks the live `kb` table (#925).
- Breaks: principle 8. `kb_resolver.py`'s docstring says "every surface asks this function and none decides on its own"; that holds for the surfaces (pinned by `tests/test_kb_walk.py`) and not for what they call.
- Blast radius: a KB registered after a long-running server loaded its config resolves at the boundary and then gets a silently clean QA result inside. Seven in-tree plugin sites do the same.
- Issue: "Services take a resolved KB, not a name; `get_kb` is private to the registry" (0.27).

**H6. Plugins cannot leave the tree as the code stands, and the distance has grown since ADR-0040 measured it.** [sweep, with spot checks]
- `pyrite/plugin_api` and `PLUGIN_API_VERSION` do not exist [checked by grep].
- Extensions import 63 distinct names from 26 `pyrite.*` modules (ADR-0040 counted 43 from 18). New since then: `pyrite.cli.*` (19 statements), `storage.file_operations` and `storage.entry_writer`.
- Constructions inside `extensions/*/src`: 43 `PyriteDB(`, 53 `load_config(`, 20 `KBService(`, 52 `_raw_conn` uses (software-kb 26, social 17, encyclopedia 9).
- Core knows extension names: `mcp_server.py:369` hardcodes `"sw_review"`; `cli/init_command.py:33, 108, 196-213` define `adr`, `backlog_item` and the zettelkasten types; `services/rubric_checkers.py:342, 410` and `services/qa_analytics_service.py:27, 123-145` name `backlog_item`.
- Six protocol methods have no implementer in any extension: `get_db_columns`, `get_field_schemas`, `get_collection_types`, `get_migrations`, `get_protocols`, `get_rubric_checkers` (software-kb imports `NAMED_CHECKERS` instead).
- 30 core test files import extension packages (software-kb 25).
- Breaks: ADR-0040 (accepted, not implemented).
- Issue: "The plugin conformance kit runs as a shrink-only ratchet over the in-tree extensions" (0.27, S). "Core names no extension type" (0.28). "Decide the six unimplemented protocol methods before the contract is alpha" (0.28).

### Medium

**M1. A KB's description and type live in three places and the listing shows the stale one.** [checked]
- `kbs/sw/kb.yaml` has `description: Software team knowledge base ...`; `data/config.yaml` has `description: ''`; the `kb` table row has an empty description. `pyrite list`, `pyrite kb list` and `pyrite orient -k sw` all print `""`. `kb_schema.py:128` also reads a `kb_type` from `kb.yaml` that nothing reconciles with the registry's.
- Breaks: principle 5; the registry map names three registry designs with none accepted.
- Issue: "A KB's description and type are read from kb.yaml; the registry holds name and path" (0.27, ADR-0039).

**M2. MCP never sets `isError`, and plugin tools return a third error shape.** [checked]
- `grep -rn "isError\|is_error\|CallToolResult" pyrite extensions/*/src` finds nothing; `mcp_server.py:_call_tool` (3459) always returns text content. A domain refusal reaches the client as a successful call whose JSON has an `error` key; the SDK's own schema validation is the only source of `isError: true` [assumed for the SDK half]. This is hallway friction 4 ("two error shapes in one server").
- Repro via `_dispatch_tool` (script `mcp_probe3.py` and the inline probe): `zettel_graph` with `depth: 99` returns `{"error": "depth must be between 1 and 3"}`; `sw_epic_detail` on a non-epic returns `{"error": "Item 'thing-three' is not an epic (kind: bug)"}`. No `error_code`, no `retryable`. The dispatcher's pre-lookup makes missing ids canonical, so the bare shape appears only on handler-specific refusals.
- Count: 119 dict literals with `"error"` and no `error_code` outside the CLI; software-kb 29, journalism 24, zettelkasten 2, encyclopedia 2 [sweep]. No extension calls `_error` or `_refusal`.
- Breaks: ADR-0037 section 3; `docs/json-contracts.md`.
- Issue: "An MCP refusal sets isError and carries the one envelope; a plugin handler raises" (0.27 for the decision, since it changes a public shape; 0.28 for plugins).

**M3. REST error bodies: 159 `HTTPException` sites, one of which uses the canonical builder.** [sweep]
- 94 inline `detail={...}` dicts (93 without `retryable`), 49 string details, 9 `public_text`, 7 `_error_detail` (`endpoints/repos.py:68`). Only `endpoints/write_refusal.py:17` builds the full shape. `api.py:1166` returns `{"error": "Not Acceptable", ...}`. `endpoints/worktree.py:200` returns `{"changes": [], "count": 0, "error": ...}` with status 200.
- The surfaces map lists this as known (ADR-0037 themes 3b and 3c). Still true, and `docs/json-contracts.md` says the dict details are the same wrapped shape.
- Issue: "Endpoints raise PyriteError; `HTTPException(` outside the builder is a shrink-only list" (0.27).

**M4. 58 error codes belong to no exception class.** [sweep]
- 243 code literals, 72 distinct, 58 on no class. Synonyms in use: `MISSING_PARAM` and `MISSING_PARAMETER`; `INTERNAL` (MCP, `mcp_server.py:3067`) and `INTERNAL_ERROR` (`exceptions.py:320`); `NOT_FOUND` and `ENTRY_NOT_FOUND` (one documented CLI override, `utils/errors.py:_CLI_CODE_OVERRIDES` [checked]). `tests/test_json_contracts_transitions.py` checks class codes only.
- Issue: "One table of error codes; a literal outside it fails" (0.27, with #610).

**M5. The three surfaces prepare a write differently.** [sweep]
- CLI create coerces `--field` values against the KB schema (`entry_commands.py:_field_type_for`, `_parse_field_value`); MCP and REST do not. REST create takes a fixed key set (`CreateEntryRequest`) and cannot carry a plugin type's fields; REST update has no append, remove or unset. Only MCP runs a QA pass after the write (`_maybe_validate`). MCP and REST apply the truncated-body guard in their own layers; the CLI relies on the service.
- Breaks: principle 8; the surfaces map's parity invariants.
- Issue: "One write request shape, built once; the surface parity test covers coercion and operations" (0.27).

**M6. Entry writes have four side doors the one-writer ratchet cannot see.** [checked]
- `storage/document_manager.py:170`: the old file of a templated move is removed with `git rm --quiet --force`, which stages the deletion in the user's git index, with a bare-unlink fallback under `except Exception`. Not `remove_entry_file`, no compare with the bytes read.
- `services/schema_service.py:61` (`_save_kb_yaml`): a ruamel re-dump of the whole `kb.yaml` with no `expect` compare; a hand edit between read and write is lost, and nothing checks for a no-op.
- `cli/__init__.py:1067`: `pyrite readme --write` overwrites an existing `README.md` whole.
- `cli/init_command.py:541`: `kb.yaml` is written by `dump_yaml_file`, not through `KBFiles`.
- `tests/test_entry_file_writers_are_one_function.py:33` matches `write_text`, `write_bytes`, `replace`, `rename`, `save`, `atomic_write_text` only: `open(..., "w")`, `unlink`, `shutil` and git subprocesses are outside it.
- Breaks: principle 3, ADR-0042, "Pyrite is a guest" (staging in the user's index is an effect nobody asked for).
- Issue: "The writer ratchet also sees open, unlink and git; a templated move removes the old file through the entry writer" (0.27; the README overwrite is small enough for 0.26.4).

**M7. A create writes empty placeholders nobody asked for.** [checked]
- `pyrite create -k sw -t backlog_item --title "Thing three" -b x -f kind=bug` writes `importance: 5`, `status: proposed`, `priority: medium`, `rank: 0`, `effort: ""`, `assignee: ""`. `-t adr` writes `adr_number: 0`, `deciders: []`, `date: ""`, `superseded_by: ""` into `adrs/0000-use-sqlite.md`; `-t person` writes `research_status: stub` and `role: ""`.
- Code: `kb_service.py:_with_declared_fields` (about 1585, `schema.empty_value()`) and `models/base.py` (`meta["importance"] = self.importance`) [sweep for line numbers].
- `docs/how-pyrite-edits-your-files.md` (line 75) documents "what you sent and what the type puts there" and shows `importance: 5`, so defaults are by design. Empty strings and a zero ADR number are not values; they fail the design's first question (would a hand maintainer be glad).
- Issue: "A create writes a declared default only when it is a value" (0.27, with H2).

**M8. `db.conn` is deprecated for removal in 0.27 and is still load-bearing.** [sweep, deprecation checked]
- `storage/connection.py:621` warns with removal in 0.27. 72 uses in `pyrite/` outside migrations (October: 70): `services/worktree_service.py` 19, `index_worker.py` 15, `embedding_worker.py` 11. Extensions add 52 `_raw_conn` uses.
- Breaks: ADR-0013 phases 2 and 3.
- Issue: "Decide before the 0.27 cut: move the 47 service uses or move the removal release" (0.27).

**M9. Deprecations due at 0.27, one overdue, several with no release.** [sweep; the overdue one checked]
- Due at 0.27: `db.conn`; `FRONTMATTER_ALIASES` (`schema/field_aliases.py:568`); `export --format` as `--target` (`cli/export_commands.py:34`); `task -f` and `task status` (`cli/task_commands.py:207, 331`); `-f` as `--format` (`cli/output.py:140`); `legacy_error_code` (`mcp_server.py:123-316` and others); the `distance` row key (`plugins/context.py:95`); the git identity fallback (`services/git_service.py:232`).
- Overdue: `kb_service.py:3604` "`full` is the default until 0.25.8"; it is still the default at 0.26.3.
- No release named, against the project rule: software-kb `cli.py:549`; `endpoints/admin.py:85-95` (`?wait=true`); `config.py:42` and `:2346-2361`; `plugins/context.py:28, 67, 73`; `models/task.py:384, 564`; `models/core_types.py:180`; `plugins/removed.py:41` names a PR number.
- Issue: "0.27 removal checklist, and a test that every deprecation names a release" (0.27; the orient default decision fits 0.26.4).

**M10. The two objects everything passes through are the largest and most-changed.** [checked for KBService; rest sweep]
- `KBService`: 106 methods, 3,936 lines, 94 commits in 60 days. `PyriteMCPServer`: 96 methods, 3,002 lines, 68 commits. `storage/index.py`: 2,382 lines, 51 commits; `IndexManager._entry_to_dict` is 356 lines with 75 branches. `SearchService.search` takes 23 parameters and the list is repeated across about ten layers. 120 functions exceed 100 lines.
- 48% of `pyrite.*` imports (763 of 1,590) are inside functions; `kb_service.py` has 39.
- Issue: "Split KBService along the write request (theme 5); search takes one query object" (0.27 and backlog).

**M11. A collection create treats a failed existence check as "the slug is free".** [checked]
- `server/endpoints/collections.py:138-148`: `except Exception: break` inside the slug-uniqueness loop, then `svc.create_entry`.
- Issue: "A failed existence check refuses the collection create" (0.26.4).

**M12. CLI errors: 55 commands print and exit outside the one function.** [sweep]
- An `Exit` raised right after a print with no `cli_error`: software-kb `cli.py` 12, `admin_cli.py` 8, `export_commands.py` 6, `read_cli.py` 6, journalism `cli.py` 6. `search_commands.py:385-395` prints `{"query", "count": 0, "results": [], "error": str(e), "error_type"}` with no `error_code`. `admin_cli.py` forces `output_format="rich"` in `require_kb`.
- Both software-KB and research hallway tests name the neighbouring symptom: `-k` spelled four ways, `--format` defaults differing inside `qa` and `sw`.
- Issue: "Every command exits through the outcome root; extension CLIs included" (0.27).

### Low

**L1. Layering exceptions.** [sweep] No service or storage module imports a surface, and surfaces hold no SQL and no `KBRepository` (good). The exceptions: `utils/errors.py` imports `pyrite.cli.output` and `pyrite.cli.outcome` 13 times; `config.py:1948, 1996` imports `server.request_guard`, including the private `_hostname`; `plugins/registry.py:636` imports `server.tool_schemas.CORE_TOOL_NAMES`; `server/api.py:367-371` sets the private `svc._github_token`. `tests/test_layer_boundaries.py` carries an `ALLOWLIST` of 21 [checked]. Issue: "Move the CLI error renderer out of utils; empty the layer allowlist" (backlog).

**L2. Composition roots.** [sweep] 25 `KBService(` constructions in `pyrite/`: `admin_cli.py` 4, `read_cli.py:84`, `link_commands.py:301, 440`, `search_commands.py:46, 212`, `index_commands.py:849`, `init_command.py:561`, and five services that build one internally. Issue: "One factory per process builds KBService" (0.27, with theme 3).

**L3. Duplication.** [sweep] Slug from a title: six copies of the `re.sub` (`schema/validators.py:120, 174`, `endpoints/collections.py:132`, software-kb `plugin.py:3115`, `models/core_types.py:95`, `schema/field_schema.py:44`). Wikilink regexes: `storage/index.py:94, 100`, `renderers/quartz.py:50-56`, `storage/repository.py:92-93`. `fromisoformat` called directly at 17 sites beside `parse_datetime`. Issue: "One slug function, one wikilink grammar" (backlog).

**L4. Dead code.** [checked for the four] Referenced nowhere: `cli/link_commands.py:241 _build_suggest_query`, `storage/index.py:2378 create_index`, `services/oauth_providers.py:39 OAuthProvider`, `services/acting.py:63 acting_account`. Referenced only from tests [sweep]: `config.resolve_config_dir`, `models/protocols.get_all_protocol_info`, `models/task_validators.validate_task`, `cli/export_commands._load_entry_from_result`, `storage/migrations.ensure_migrated`, `schema/field_aliases.undeclared_class_aliases`, `postgres_backend.create_postgres_engine`. 25 handlers carry `# noqa: BLE001` while ruff's `select` (`pyproject.toml:221`) does not include `BLE`, so the marker does nothing [checked]. Issue: "Remove eleven unreferenced definitions; enable BLE001 with a baseline" (0.26.4, small).

**L5. Public shapes with no contract.** [sweep, by name search] 26 of 48 core MCP tools are never named in `docs/json-contracts.md` (all `kb_find_*`, `kb_index_*`, `kb_manage`, `kb_commit`, `kb_push`, `kb_stats`, `kb_timeline`, `kb_schema`, most `task_*`). No extension tool has an envelope section. CLI JSON with no documented envelope: `stats`, `timeline`, `index stats`, `qa status`, `kb list`, `sw board`, `sw backlog`, `task list`, `task claim`. Issue: "Every supported tool has a documented envelope, checked by a test over the tool registry" (0.28).

**L6. Known gaps re-verified as still true** (listed in `kb/design.md`, not new): a single-entry read returns defaults, de-duplicated tags and index timestamps among the file's fields [sweep, by probe]; a `before_save` hook can change a create (journalism `hooks.py:30`, social `hooks.py:15`) [sweep]; an update re-stamps `updated_at` when the file already carries it (`kb_service.py:2318` [checked]), which ADR-0042 decides and `docs/how-pyrite-edits-your-files.md` does not mention; the two strict expected failures in `tests/test_doc_write_as_patch.py` are the stale whole-document replace and the id-less file whose title changes [checked by run]. One stale doc line: the write map says `repo.save(entry)` over an existing file is a whole-file overwrite; `repository.py:1147-1165` refuses it [sweep].

### Tests

**T1. The October plan is half landed and the backlog does not say so.** [sweep; list sizes checked]
- Landed: Q1 as `tests/test_tests_drive_the_surface_not_the_mock.py` (`ALLOWLIST` 52 files); Q2's timing record in CI; T2a; T4 as `tests/_world.py`. Their backlog entries still read `planned` or `proposed`.
- Not landed: Q3 (weak-test lint), Q4 (execution-reach selection), Q6 (flake record), Q7 (golden diff review), Q8 (file budget).
- Since October the tree grew from 428 to 645 test files and 152k to 221k lines (the October scope may differ slightly); files over 1,000 lines went from 20 to 31; `monkeypatch.setattr` sites from 413 to 619; `PyriteDB(` constructions from 512 to 775. The world builder exists and has not been adopted.
- Still mocking the service behind the entry point: `extensions/software-kb/tests/test_software_kb.py` (10 `KBService` patches), `tests/test_search_contract.py`, `tests/test_cli_json_output.py` (`SearchService.search`, the October case).

**T2. Ratchets that could become structure.** [checked sizes]
- `tests/test_tests_drive_the_surface_not_the_mock.py` `ALLOWLIST` 52: goes to zero by adopting the world builder.
- `tests/production_wiring_baseline.txt` 164 lines: same.
- `tests/test_layer_boundaries.py` `ALLOWLIST` 21: goes to zero with L1 and L2.
- `tests/test_kb_reads_are_one_function.py` `DECIDED_INSIDE` 53 and `SAME_ANSWER` 19: candidates for one reader.
- Themes 1, 3 and 4 each add a ratchet that does not exist today.

**T3. Invariants in the maps with no enforcing test.** [sweep]
- The plugins map names `tests/test_plugin_api_snapshot.py`; the file does not exist, so "imports only the facade" and "facade matches a snapshot" are unpinned.
- Registry map R1, R2, R7 and S1 (every table declared derived or state) have no test found.
- The types map's enum invariant has no sibling for ranges or list shapes (H3).
- `tests/test_update_walks_every_surface.py:925, 1071` skip with a reason citing #455, which is closed.
- 117 imports of underscore names from `pyrite` across 71 test files pin implementation (`config._apply_env_overrides` 11 times).

---

## (c) What is in good shape

- **KB resolution at the boundary.** One resolver (`services/kb_resolver.py`), one refusal, and `tests/test_kb_walk.py` walks every REST route, MCP tool and CLI command.
- **The entry write.** Every service path ends in `storage/entry_writer.py`; the not-yet-converted list is empty; a no-op update writes nothing and does not stamp (`changed = new_text != text`, confirmed by probe).
- **One frontmatter splitter** (`utils/frontmatter.py:146`, 38 callers, guarded by a test). The October feedback is closed.
- **The MCP dispatcher's error contract.** Every `PyriteError` becomes one envelope, an unknown exception becomes `INTERNAL` with no exception text, and goldens pin every concrete exception class on all three transports.
- **Layer direction.** Zero imports from services or storage up into a surface; no SQL, ORM or repository use in any surface.
- **Field projection** (`services/read_shaping.py`) and **YAML loading** (`utils/yaml.py`) are each one helper with a guard.
- **Config location** is read through `current_config_file()`; nothing outside `config.py` uses the import-time constants.
- **Test discipline.** No non-strict `xfail`; two strict expected failures, each naming its invariant; no TODO or FIXME debt in source.
- **Deprecations** mostly name their removal release, and none is past it except the `orient` default.

---
