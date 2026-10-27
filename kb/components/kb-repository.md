---
id: kb-repository
title: KB Repository
type: component
kind: module
path: pyrite/storage/repository.py
owner: core
dependencies:
- pyrite.config
- pyrite.exceptions
- pyrite.models
- pyrite.schema
- pyrite.utils.yaml
tags:
- core
- storage
---

File-system layer for a single knowledge base. Reads and writes markdown files with YAML frontmatter, applies schema migrations on load, and routes entries into subdirectories. Source of truth for on-disk entry state. `MultiKBRepository` wraps multiple instances for cross-KB access.

## Key Methods

- `load(entry_id)` — find file, parse frontmatter, apply migrations, return `Entry`
- `_load_entry(path)` — THE strict load, the only kind a writer can call (ADR-0042 decision 8a). Every loader goes through it (`load`, `load_entry_from_file`/`_bytes`, `list_entries`, `rename`, `search_files`, `services/file_search.py`). It returns an entry of the type the file declares, fully loaded, or raises; it never substitutes a type and never returns a model holding defaults for refused values (#1261; #1311 rounds 1-2 did, and a hook-guarded write landed). A read error is itself (`OSError`, `UnicodeError`); frontmatter that is missing, unparseable or not a mapping is `FrontmatterError`; an empty mapping (`---` then `---`) is a note, as `read_entry_id` reads it; a type loader that raises is `EntryLoadError` (`ENTRY_UNREADABLE`, 422), whose message names the KB-relative path, the declared type, the refused fields (`_refused_fields` probes each key alone; `{}` when not isolated) and the loader's message. The reconcile lists such a file under `malformed` and holds no row for it. `load(entry_id)` re-raises `EntryLoadError` (None means only "no file", or a fault #777 has yet to name), so delete, link, the task and qa services and claim are refused before any hook; `delete_entry` used to run `before_delete` only `if entry`. `rename` checks each linking file too (`_write_refusal`) and lists one that does not load under `files_skipped`.
- `load_for_read(path, raw)` — the single-entry read's view, `(entry, load_problems)`, called ONLY by `KBService._entry_file` (a structural test in `tests/test_refused_fields_stay_absent.py`). For a file refused in some fields it builds the declared type without them; the model holds the type's default there, so `IndexManager.read_fields(refused=)` leaves each named key out of the read. The entry never leaves `_read_entry`; problems travel beside it, never on it (`Entry` has no `load_problems`). Tests: `tests/test_unloadable_file_is_never_written.py` (every write method x shape x hooked/unhooked type, from an AST enumeration of every loader and entry-file writer call site), `tests/test_refused_fields_stay_absent.py` (reads), `tests/test_loader_failure_keeps_the_type.py` (the seam and the reconcile, over every registered type).
- `save(entry, subdir, *, exclusive)` — a create, and only a create (`pyrite/storage/repository.py:817`): the type's values set onto a skeleton with `apply()` (`new_entry_text`) and published with `expect=ABSENT` (`entry_writer.create_entry_text`); enforces the read-only check. A target that already exists is refused (`EntryExistsError`, code `ENTRY_EXISTS`, the file as it was), never overwritten. The `exclusive` argument is kept for old callers and has no effect: every save is exclusive. Change an existing entry through `KBService.apply_operations`.
- `rename(old_id, new_id)` — `Set("id", new_id)` on the file's own bytes (and the `updated_at` bookkeeping where the file carries it), published in place against the bytes read (the file keeps its path and name, #488/#489); every linking file gets a `ReplaceBody` and a `Set` per frontmatter value that holds a `[[old_id]]` wikilink, published against the bytes read and re-read on a change (#955 P4)
- `delete(entry_id)` — remove file from disk
- `list_entries()` — iterate all entries and collection YAML files in the KB directory
- `find_file(entry_id)` — the file holding the id (ADR-0038 I8): files named like the id first, each verified, then `_files_holding`, the whole-KB scan `files_to_delete` and a `get` miss share. The scan reads every file's raw text but parses (`read_entry_id`) only those `could_hold_id` cannot rule out: a file whose frontmatter opens with plain `key: value` lines up to a plain `id: <token>` line holds that token. A KB with schema migrations to apply is parsed in full, since a migration may set the id (#135). No id-to-path cache: stateless by the maintainer's decision of 2026-09-30.
- `validate_all()` — return paths with validation errors
- `_maybe_migrate(frontmatter)` — apply schema version migrations on load
- `_infer_subdir(entry)` — determine target directory from schema/type rules

## Inside the KB root

Every write, move and removal of an entry file is made by `KBFiles` (`pyrite/utils/kb_files.py`), through the entry writer, which takes the KB's `root` as a required argument: the path is resolved once, each folder is opened beneath the root without following a link, and every later step goes through that folder's descriptor, so a folder swapped for a link meanwhile is refused. `ids pin` writes through the same code. A path that resolves outside the root, through a link to a file or to a folder, is refused with `OUTSIDE_KB_ROOT` before anything is written; so is a write or move of a file with more than one hard link (`HardLinked`, under git or not), and a move of a file that is itself a symbolic link (`migrate-standards` refuses with the reason; the link's target is never removed). A link whose target is inside the KB is written through and stays a link, a `rename` of one included, since a rename moves no file. `delete` decides every removal before the first. Reads are not routed through `KBFiles` yet: a listing can still return a file reached through a link out; the write is what refuses it. The test grid is `tests/test_kb_writes_stay_inside_the_root.py`.

## Consumers

- `KBService` — primary consumer for all read/write operations
- `DocumentManager` — calls `save` and `delete` as part of the write-path pipeline
- `IndexManager` — calls `list_entries` during full re-index
- CLI commands and extension CLIs — direct access for import/export and migration tasks

## Related

- [[document-manager]] — a create's call of `KBRepository.save`; every write's bytes go through `pyrite/storage/entry_writer.py`
- [[storage-layer]] — overall storage architecture including PyriteDB and IndexManager
- [[index-manager]] — consumes `list_entries` to build and sync the search index
