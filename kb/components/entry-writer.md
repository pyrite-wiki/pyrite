---
id: entry-writer
title: Entry Writer
type: component
tags:
- core
- storage
importance: 5
kind: module
path: pyrite/storage/entry_writer.py
owner: core
---

The only module that publishes an entry file's bytes. It does not decide what the bytes are ([[file-operations]] does); it decides how they reach the disk without losing a concurrent change: compare the file with the bytes the change was computed on, replace it under a momentary lock, or refuse and write nothing.

The invariants it keeps are [[adr-0042]] decision 10 (writes are optimistic: merge against the base, lock only the instant of replace, and its amendments 10a), decision 12 (every write of an entry file goes through this path) and decision 13 (a create refuses an existing target). The module docstring states the behaviour in a KB outside git (no lock, `locked: false` in the result).

## Public functions (line numbers on this branch)

- `publish_entry_text(path, text, *, expect, lock_timeout)` — `pyrite/storage/entry_writer.py:386`; replace `path` if it still holds `expect`, else `FileChanged` (nothing written); returns whether the replace ran under the lock
- `create_entry_text(path, text)` — :139; write to a path that must not exist, `FileExistsError` if it does; exclusivity comes from the publishing call, never from an `exists()` check
- `move_entry_text(src, expect, dst, text, *, lock_timeout)` — :211; publish `text` at `dst` (must not exist) and remove `src` (must still hold `expect`), as one write under one lock for both paths
- `publish_exclusive(tmp, path)` — :287; the exclusive publish (`os.link`, else `O_CREAT | O_EXCL`) the two above share
- `updated_at_stamp(old, raw)` — :90 and `raw_scalar(yaml_text, key)` :79; the `updated_at` bookkeeping in the file's own spelling, stamped only where the file carries the key ([[adr-0042]] decision 7)
- `FileChanged` — re-exported from `pyrite/utils/atomic_write.py`

## Who calls it

- `KBService._update` publishes an update (`pyrite/services/kb_service.py:1976`); `KBService` creates a file and the templated move through `create_entry_text` (:2370)
- `KBRepository.save` (a create, `pyrite/storage/repository.py:878`), `rename` (`publish_entry_text` :1084, `move_entry_text` :1098) and the rename's link rewrite (:1180) — see [[kb-repository]]
- `SchemaService` `schema migrate` (`pyrite/services/schema_service.py:225`) and software-kb's `migrate-standards` (`extensions/software-kb/src/pyrite_software_kb/cli.py:810`, :822)
- `ids pin` is the one writer outside it, listed in `ENTRY_FILE_WRITERS_NOT_YET_CONVERTED` in `tests/test_entry_file_writers_are_one_function.py`, the ratchet that fails when another module writes an entry file's bytes

## Related

- [[file-operations]] — computes the text this module publishes
- [[kb-repository]], [[kb-service]], [[document-manager]]
- [[adr-0042]]
