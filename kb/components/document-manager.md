---
id: document-manager
title: Document Manager
type: component
kind: module
path: pyrite/storage/document_manager.py
owner: core
dependencies:
- pyrite.storage.repository
- pyrite.storage.database
- pyrite.storage.index
tags:
- core
- storage
---

Write-path coordination for KB entries. Consolidates the save-register-index pattern into a single class, completing the ODM abstraction layer.

## Pattern

```
KBRepository.save() → PyriteDB.register_kb() → IndexManager.index_entry()
```

## Key Methods

- `save_entry(entry, kb_name, kb_config)` — save to disk, register KB, index
- `delete_entry(entry_id, kb_name, kb_config)` — delete from disk and index
- `index_entry(entry, kb_name, file_path)` — re-index from existing file

## Consumers

- `KBService.create_entry()` (a create: `KBRepository.save(exclusive=True)` writes the type's values onto a skeleton with `apply()` and publishes with `expect=ABSENT`, #955 C2) and `delete_entry()`.
- Not an update and not a link: since #955 (B6 P3a) `KBService._update` applies operations to the file's own bytes and publishes through `pyrite/storage/entry_writer.py`, and since B6 P4 `add_link()`/`add_links()` append one `links:` item through the same write. `save_entry` is only a create's path now; its subdirectory preservation and `keep_filename` serve no caller and go with it.

## Related

- [[storage-layer]] — PyriteDB and IndexManager
- [[kb-service]] — business logic layer that uses DocumentManager
