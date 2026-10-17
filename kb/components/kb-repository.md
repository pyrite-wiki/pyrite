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
- `save(entry, subdir, *, exclusive)` — a create: the type's values set onto a skeleton with `apply()` (`new_entry_text`) and published with `expect=ABSENT` (`entry_writer.create_entry_text`); enforces the read-only check. The non-exclusive form over an existing file is the model's whole-file overwrite kept for fixtures and `repo.save(entry)` callers, published against the bytes read (a `ConflictError` if they changed); no service path writes an existing entry this way since #955 P4.
- `rename(old_id, new_id)` — `Set("id", new_id)` on the file's own bytes (and the `updated_at` bookkeeping where the file carries it), published at the new path with `expect=ABSENT`, then the old file removed; every linking file gets a `ReplaceBody` and a `Set` per frontmatter value that holds a `[[old_id]]` wikilink, published against the bytes read and re-read on a change (#955 P4)
- `delete(entry_id)` — remove file from disk
- `list_entries()` — iterate all entries and collection YAML files in the KB directory
- `find_file(entry_id)` — locate markdown file by ID across subdirectories
- `validate_all()` — return paths with validation errors
- `_maybe_migrate(frontmatter)` — apply schema version migrations on load
- `_infer_subdir(entry)` — determine target directory from schema/type rules

## Consumers

- `KBService` — primary consumer for all read/write operations
- `DocumentManager` — calls `save` and `delete` as part of the write-path pipeline
- `IndexManager` — calls `list_entries` during full re-index
- CLI commands and extension CLIs — direct access for import/export and migration tasks

## Related

- [[document-manager]] — a create's call of `KBRepository.save`; every write's bytes go through `pyrite/storage/entry_writer.py`
- [[storage-layer]] — overall storage architecture including PyriteDB and IndexManager
- [[index-manager]] — consumes `list_entries` to build and sync the search index
