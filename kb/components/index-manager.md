---
id: index-manager
title: Index Manager
type: component
kind: service
path: pyrite/storage/index.py
owner: core
tags:
- core
- storage
---

Indexes KB entries from markdown files into SQLite for fast search and querying. Handles full re-index, incremental sync, and KB registration. Central coordinator between filesystem (KBRepository) and database (PyriteDB).

## One reconcile (ADR-0038 step 2)

Every path that indexes a KB's files is `reconcile_kb(kb_config, force=...)`:
`index_kb` (`pyrite index build`, `init`, the rebuild job) forces every file,
`sync_incremental` (`pyrite index sync`, rename, the sync job) and `sync_kb`
(`pyrite kb reindex`) force none, and `index_with_attribution` forces the
files git changed and adds history through its `enrich` hook.

- **Plan** (`plan_reconcile`, writes nothing): walk `list_all_files` in
  KB-relative path order. A known path whose recorded `file_mtime_ns` and
  `file_size` still match is not read (`_file_changed`, the one staleness
  rule); every other file is parsed. Claims are grouped by id; the first path
  wins; ids with several files are `duplicates`; unparseable files are
  `malformed`.
- **Apply**: write a winner whose row is missing, moved or has another content
  hash (`added`/`updated`); rewrite silently when only the stat moved (a touch);
  retire every row no parseable file claims (`removed`). A file whose row
  cannot be written is reported as malformed and holds no row.

`DocumentManager.delete_entry` asks the plan who holds the id (#494);
`check_health` takes duplicates, winners and staleness from it.
`tests/test_one_reconcile_structure.py` fails when a function walks files and
writes rows anywhere else.
