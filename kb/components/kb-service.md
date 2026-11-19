---
id: kb-service
type: component
title: "KB Service"
kind: service
path: "pyrite/services/kb_service.py"
owner: "markr"
dependencies: ["pyrite.config", "pyrite.storage", "pyrite.plugins", "pyrite.models"]
tags: [core, service]
---

# KB Service

`KBService` is the central orchestrator for all knowledge-base and entry operations. It sits between the transport layers (REST API, CLI, MCP) and the storage/indexing layer, enforcing read-only checks, running plugin hooks, and managing ephemeral KB lifecycles.

## Key Files

| File | Purpose |
|------|---------|
| `pyrite/services/kb_service.py` | `KBService` class (all operations) |

## Constructor

```python
KBService(config: PyriteConfig, db: PyriteDB)
```

Stores config, the database handle, and creates an `IndexManager`. The embedding service is lazy-loaded on first use via `_get_embedding_svc()`.

## KB Operations

- **`list_kbs()`** -- Returns all configured KBs with stats (entry count, last indexed).
- **`get_kb(name)`** -- Retrieves a `KBConfig` by name.
- **`get_kb_stats(name)`** -- Delegates to `PyriteDB.get_kb_stats()`.

## Entry CRUD: the write pipeline (#378)

Every create/update decision is made here, once, for every surface. REST, MCP and the CLI only map their arguments into a spec and map the errors back out; they make no create decisions of their own (a parity test, `tests/test_write_surface_parity.py`, sends the same spec through all six create surfaces).

- **`_prepare(kb_name, kb_config, spec, allow_undeclared, builder)`** -- the checks, in order: the ADR-0034 truncated-body refusal (`ensure_not_truncated`) and marker stripping; the title; the undeclared-type refusal (core types not exempt, #197); plugin type resolution scoped to the KB type; `entry.validate()`; `_validate_write` (KB schema + plugin validators, returns warnings); the exists check. Raises a `ValidationError` subclass with a stable `error_code`: `UndeclaredTypeError` (`UNDECLARED_TYPE`), `EntryExistsError` (`ENTRY_EXISTS`), `SchemaViolationError` (`SCHEMA_VIOLATION`), `TruncatedBodyError` / plain `ValidationError` (`VALIDATION_FAILED`).
- **`_prepare_and_save(...)`** -- `_prepare`, then `before_save` hooks, save + index via `DocumentManager`, embed, `after_save` hooks. Returns a `WriteResult(entry, warnings)`.
- **`create(kb_name, spec, allow_undeclared=True)`** / **`create_entry(kb_name, entry_id, title, entry_type, body, *, allow_undeclared=True, **kwargs)`** -- one entry through the pipeline. `allow_undeclared` defaults to True for in-process callers writing their own types (tasks, collections, daily notes, plugins); every user-facing surface passes the caller's own override flag (False unless asked). The `metadata` envelope is a second spelling (Andon #994, ADR-0051): a declared field or an alias sent there lands at its own key (`build_entry`), a field Pyrite or the type manages is refused on a create at every spelling (top level, dotted `metadata.k`, the envelope, an alias; `_refuse_managed_on_create`, #1010), as `update` refuses it; `links`/`sources` stay a new entry's own content, and `create_entry(..., owns=...)` exempts only the managed fields an in-process owner names (`sw new-adr`'s `adr_number`), and a field named at two spellings with different values is refused (`refuse_conflicting_spellings`, #720).
- **`bulk_create_entries(kb_name, specs, *, allow_undeclared, validate_only, drop_managed=False)`** -- the same pipeline per item; `drop_managed=True` (REST `/entries/import`, `pyrite import`) drops the index/location keys an export carries and a type's managed fields and names each in the item's `ignored` (#1010), where the default (MCP `kb_bulk_create`) refuses them; a refused item fails alone with `{"created": false, "error", "error_code"}`, siblings are created, order is kept. Used by MCP `kb_bulk_create`, REST `/entries/import`, `pyrite import` and task decomposition.
- **`add_entry_from_file(kb_name, path, *, validate_only, allow_undeclared)`** -- the same pipeline, with a builder that reads the frontmatter the way the loader does.
- **`update(entry_id, kb_name, updates)`** / **`update_entry(entry_id, kb_name, **updates)`** -- `update` is the surfaces' form: it refuses a truncated body at any depth and fields Pyrite maintains (identity fields plus the type's `Entry.managed_fields`, e.g. a task's audit trail); `update_entry` is the in-process form, which may write managed fields. Both are `_update` (ADR-0042; #955, B6 P3a): read the file once (`KBRepository.read_for_write`, the bytes are the base and the model is parsed from them); map the request's fields (`_request_operations`: a `Set` per key, `ReplaceBody` for `body`, one `Set(("metadata", *k))` per nested key; every key parsed into path segments once, here, by `file_operations.parse_path`, so the resolver and `apply()` never read a string again and a key such as `status_change_log[0]` inside the `metadata` envelope is the field it names) and the item operations through the one resolver (`_resolve_operation`, #973 fix round 1; Andon #994 decision A, ADR-0051): the path is resolved to its field by `schema.field_aliases.field_at` (the first key, an alias folded to its target, one leading `metadata.` folded away -- `metadata` is not a field, so `metadata` alone or `metadata.metadata.k` is refused at any depth) and to the one path the file spells that field at by `spelling_in` (`k:`, an alias, either under `metadata:`; the field's own top-level name when the file has none; a file that spells the field twice refuses the update), and every refusal is decided on that field for every operation kind -- `type`/`entry_type`, `body` as a key, a managed field (`links`/`sources` take a whole item appended or removed, never `links[0].x`), an appended link or source item checked as one before anything is written -- so `metadata.type`, `metadata.evidence`, `metadata.metadata.evidence` and `links[0].note` are refused as their top-level spellings are, whatever the surface or operation; `_item_operations` refuses a malformed `append`/`remove`/`unset` shape as `VALIDATION_FAILED`; `file_operations.apply`; `updated_at` stamped only where the file carries it and something changed, in the file's own spelling (`_updated_at_stamp`: the separator, a `Z` or an offset or none, the fraction's digits, a date-only value; a timestamp the request sets is written as sent, `TimestampText`, when the loader reads it as a date or timestamp (`_yaml_reads_a_timestamp`, asked of the loader itself; a `datetime-local` `YYYY-MM-DDTHH:MM` is written with `:00` seconds), and one YAML reads as a string is refused); parse the result, `_validate_write` once on it, `before_save` hooks with their return ignored; the index row built from the result before anything is published (`IndexManager.row_for`; an unstorable result is refused); publish with compare-and-replace under the momentary lock (`storage/entry_writer.publish_entry_text`; no lock outside git, `locked: false`); on `FileChanged` re-read and re-apply, at most `_UPDATE_ATTEMPTS`, then `ConflictError`; a lock held past `_LOCK_TIMEOUT` is `StorageBusyError` (retryable), any other storage fault on the publish (a hard-linked file, the lock directory) `StorageError`, whose public text names no path. A no-op writes nothing (`changed: false`). The only move is a pinned templated-folder move (`_templated_move`; an unpinned file, or one whose target already holds a file, reports `not_moved` with the reason and the KB-relative target, #990). Nothing serialises an `Entry` on an update. Returns `WriteResult` (`entry`, `warnings`, `changed`, `locked`, `not_moved`) / `Entry`.
- **`apply_operations(entry_id, kb_name, ops)`** -- the in-process operation write: `file_operations` operations (`Set`, `Append`, `Remove`, `Unset`, `AddSubkey`, `ReplaceBody`) whose path is resolved like a field update's (`field_at`, then `spelling_in`). `qa fix`'s link repair (`Set(("links", i, <the item's own key>), target)`; a bare string item `Set(("links", i), target)`) and tag repair (`Set(("tags", i), tag)`; a scalar `tags:` value `Set("tags", tag)`, kept a scalar), `TaskService.link_qa_assessment` and `aggregate_evidence_to_parent` (`Append("evidence", item)`) and `update_task`'s audit trail (`Append("status_change_log", entry)`) write here, never a list read through the model.
- **`updatable_fields(entry_id, kb_name)`** -- the entry type's field set (model fields from the type registry + every `kb.yaml` field for the type, less identity fields, `managed_fields` and timestamps), computed by the same `_field_sets` that `update` uses. MCP `kb_update` no longer filters by it: it goes through `split_echoed_update`, which sets aside identity, managed, timestamp and index keys and `null`s for undeclared keys, and reports them as `ignored` (#455); in an echoed read result it also sets aside every field still at the value the read returned, reported as `unchanged` (#561).
- **`delete_entry(entry_id, kb_name)`** -- Runs `before_delete` hooks, deletes from file system and index, then runs `after_delete` hooks.
- **`add_link(source_id, source_kb, target_id, relation, target_kb, note)`** -- Appends one `links:` item (`Append("links", Link.to_dict())`) through `apply_operations` and re-indexes (#955, B6 P4): every other key and every existing item keeps its bytes, `updated_at` is stamped where the file carries it, the publish is compare-and-replace under the momentary lock. Deduplicates existing links before the write (the check reads the file once; a link another writer adds between that read and the publish is not re-checked: both items land, Q2's last-writer rule, and a duplicate pair is what the check tolerates on a replay). The file must spell `links` at the top level: the model and the index read it there alone, so an item at `metadata.links` would be indexed by nothing and seen by no duplicate check, and the resolver refuses that spelling with the reason, citing the reader's gap (#1025). A write the filesystem refuses is a `StorageError` (`StorageBusyError` for a lock held too long) whose detail goes to the log; a file with more than one hard link and an unusable lock directory name their reason in the public message, without the path.
- **`add_links(kb_name, specs, *, dry_run)`** -- many links, each source loaded once and written once with one `Append` per new link, in place (used by `pyrite links bulk-create`, #375); a failed write fails every position of that source with the public message.
- **`check_link_spec(target, relation, *, note, target_kb)`** and **`check_link_target(link_dict)`** (module functions, #846) -- two rules for two kinds of input. `check_link_spec` is for a link the caller is creating (`add_link`: CLI `link`, MCP `kb_link`; `add_links`: `links bulk-create`, per item with `error_code`, a `target_kb` that does not exist is `KB_NOT_FOUND` for that item; `pyrite create --link`, before the create): the target must be non-blank text (a number, boolean or date is refused with a hint to quote it, never converted), a given relation non-blank text, note and target_kb text. `check_link_target` is for links that arrive in a file or frontmatter (`_prepare`: `add`, a create's `links` field, `import`, `kb_bulk_create`): it checks `Link.to_dict()`, what `_sync_links` receives, against `_LINK_INSERT_RULES` and refuses exactly what the index insert cannot store (null target or relation; a list or mapping in any field) -- the files `index sync` reports as malformed -- and never rewrites the file. The duplicate key in `add_link`/`add_links` compares targets as text (`_same_target`), so a hand file's `target: 2024` and a typed `"2024"` are one link. Neither check runs on read or in `_validate_write`. Link reads look their KB up first through `PyriteConfig.require_kb` (`LinkDiscoveryService`, `WikilinkService.check_links`), so a typo is `KB_NOT_FOUND`, not an empty result. Every `KBService` method that acts on one KB looks it up through **`require_kb(name)`** (#952): `PyriteConfig.require_kb` through this service's database, the one resolver -- an unknown name or a shortname is `KBNotFoundError` with `did_you_mean`, a blank name `ValidationError`, and a KB registered after the config was loaded is found.

## Query Operations (read-only)

- **`list_entries(kb_name, entry_type, tag, sort_by, sort_order, limit, offset)`** -- Paginated entry listing.
- **`count_entries(kb_name, entry_type, tag)`** -- Filtered count.
- **`get_distinct_types(kb_name)`** -- Unique entry types in the index.
- **`get_timeline(date_from, date_to, min_importance, kb_name)`** -- Date-ordered timeline events.
- **`get_tags(kb_name, limit)`** / **`get_tag_tree(kb_name)`** / **`search_by_tag_prefix(prefix, kb_name)`** -- Tag queries.
- **`get_graph(center, center_kb, kb_name, entry_type, depth, limit)`** -- Graph data for the knowledge-graph visualization.
- **`get_backlinks(entry_id, kb_name)`** / **`get_outlinks(entry_id, kb_name)`** -- Link queries.
- **`get_refs_to(entry_id, kb_name)`** / **`get_refs_from(entry_id, kb_name)`** -- Object-reference queries.

## Cross-KB Resolution

- **`locate(entry_id, kb_name=None, *, readable_kbs, entry_type=None)`** (#911) -- the one KB an id names, for a surface that takes an id and an optional KB and then works in that KB. It shares `get_entry`'s unqualified pass (`_find_unqualified`): a named KB holding the id answers that KB; with no KB named (absent or blank), one readable match answers its KB, several are `AmbiguousEntryError` with `candidate_kbs` (readable only, config order), none is `EntryNotFoundError` naming only the KB the caller named. `entry_type` counts only entries of that type: a command or tool that acts on one type passes it (`sw` `backlog_item`, `wiki` `article`, `social vote` `writeup`), one that takes any passes none, on CLI and MCP alike (#1310). A KB is a full name; a shortname is link syntax only (#952). The CLI reaches it through `cli.context.require_entry_kb`, which merges registry-only KBs; every plugin command with an id and an optional `-k` calls that before it reads or writes (`sw epic/claim/submit/transition/review/log/check-ready/prioritize`, `wiki review/quality`, `social vote`), so none works in `kb_name or ""` and no row is written with an empty KB. `tests/test_ambiguous_id_is_refused.py` walks the plugin registry's CLI apps and fails on an id-taking command with no case.
  - **Over MCP** (#1310) a plugin tool declares `locates_entry` and `PyriteMCPServer._dispatch_tool` calls `locate` for it. With `kb_name` not named, it does so first, before it computes the KBs the call names, and writes the found KB into `kb_name`; the read, write and landing checks then run on that KB like any other, so the KB a handler works in is always a checked KB. A KB named under another argument (`kb`, `target_kb`, ...) never skips the lookup. With `kb_name` named, the checks run first and `locate` verifies the id in that KB inside the call's binding. `tests/test_plugin_mcp_tools_locate_ids.py::test_the_handler_acts_only_in_a_kb_the_checks_ran_on` holds it for every `locates_entry` tool.
  - **Change-set limit.** The lookup with no `kb_name` reads the canonical index: no change set is bound yet, because the change set is chosen by the KB. An id that exists only in a caller's pending change set (a `landing: review` KB, ADR-0044) is therefore `ENTRY_NOT_FOUND` until the call names its KB; named, `locate` runs inside the binding and finds it (`tests/test_writes_land_in_the_users_worktree.py::test_an_id_only_in_a_change_set_is_found_only_in_a_named_kb`). What a handler then reads through `db._raw_conn` is main's index, not the change set (#1315).
- **`resolve_entry(target, kb_name)`** -- Resolves a wikilink target. Supports `shortname:entry-id` and `kb-name:entry-id` syntax by looking the prefix up among the caller's readable KBs, by full name then shortname (`kb_resolver.readable_kb_named`) -- the one place a shortname is a name (#952, ADR-0049 Q3), and a KB the caller cannot read never changes the answer; `kb_name` itself is a full name. Falls back to title-based matching.
- **`resolve_batch(targets, kb_name)`** -- Batch-resolves a list of wikilink targets. Cross-KB targets are resolved individually; same-KB targets use an `IN` query for efficiency.
- **`get_wanted_pages(kb_name, limit)`** -- Returns link targets that have no corresponding entry (wanted pages), ordered by reference count.

## Ephemeral KB Lifecycle

- **`create_ephemeral_kb(name, ttl, description)`** -- Creates a temporary KB directory under `workspace_path/ephemeral/`, registers it in config and database with `ephemeral=True`, `ttl`, and `created_at_ts`.
- **`gc_ephemeral_kbs()`** -- Garbage-collects expired ephemeral KBs by checking `created_at_ts + ttl < now`. Removes files, unregisters from DB, and removes from config. Returns list of removed names.

## Hook Integration

- **`_run_hooks(hook_name, entry, context)`** -- One call to `HookRunner` (`services/hook_runner.py`), which owns core and plugin hooks under one contract: any exception from a `before_*` hook (or a failed hook lookup, or a `before_*` hook dropped for the wrong signature) aborts the write; an `after_*` hook's exception is logged and the next hook still runs (#379).

## Other Operations

- **`list_entry_titles(kb_name, query, limit)`** -- Lightweight ID+title listing for wikilink autocomplete.
- **`list_daily_dates(kb_name, month)`** -- Dates with daily notes for calendar views.
- **`load_entry_from_disk(entry_id, kb_name)`** / **`index_entry_from_disk(entry, kb_name)`** -- Low-level disk access.
- **`get_entry_versions(entry_id, kb_name, limit)`** / **`get_entry_at_version(entry_id, kb_name, commit_hash)`** -- Git-based version history.
- **`sync_index(kb_name)`** / **`get_index_stats()`** -- Index synchronization and health.
- **`get_setting(key)`** / **`set_setting(key, value)`** / **`get_all_settings()`** / **`delete_setting(key)`** -- Key-value settings store.

## Export & Git Operations (Delegated)

These methods are facades that delegate to `ExportService`:

- **`export_kb_to_directory(kb_name, target_dir)`** -- Exports entries as markdown files
- **`export_kb_to_repo(kb_name, repo_url, github_token, branch, commit_message)`** -- End-to-end export to a GitHub repo (clone, export, commit, push)
- **`commit_kb(kb_name, message)`** -- Commit KB changes to git
- **`push_kb(kb_name)`** -- Push KB commits to remote

## Design Notes

- All mutating operations check `read_only` and raise `KBReadOnlyError` before doing any work.
- Auto-embedding is best-effort: failures are logged at debug level and silently ignored.
- The service never imports `EmbeddingService` at module level; it is lazy-loaded to avoid hard dependency on `sentence-transformers`.
- Hook dispatch uses `PluginContext` dataclass, not a raw dict.

## Consumers

- **REST API endpoints** (`pyrite/server/endpoints/`) -- all entry and KB endpoints delegate to `KBService`.
- **CLI commands** -- entry create/update/delete, index sync, KB management.
- **MCP server tools** -- tool implementations call `KBService` methods.

## Related

- [[config-system]] -- provides `PyriteConfig` consumed by `KBService`
- [[storage-layer]] -- `PyriteDB` and `IndexManager` used internally
- [[plugin-system]] -- hooks dispatched via plugin registry
- [[entry-model]] -- `Entry` objects built by `models.factory.build_entry`
- [[rest-api]] -- primary transport consumer
