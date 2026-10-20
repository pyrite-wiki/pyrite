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

## What a link row names (ADR-0049 Q3)

`_entry_to_dict` turns `[[x]]`, `[[kb:x]]`, `![[kb:x]]`, a `references` item and
a frontmatter `links:` item (its `kb` is a KB field in link position) into
link rows. The row's `target_kb` is the **full KB name** the prefix resolves to
(`PyriteConfig.readable_kb_named(prefix, readable_kbs=None)`, unscoped: full
name, then shortname; with duplicate shortnames the first KB in config order,
as `config.validate` warns), the source entry's KB when there is no prefix, or
the prefix as written when it names no KB, so the link counts as broken, which
it is (#952, #979). The prefix as written is kept beside it in
`target_prefix` (migration v28; NULL for no prefix, or a row from before), so
a row can always be checked against what the file wrote (#980). Backlinks, the
graph, `check_health`'s `broken_links` and `qa check-links` therefore agree
with `WikilinkService.resolve_entry` on whether a `[[short:id]]` link
resolves. `check_health`'s `links_stale_kb` counts the rows a rebuild would
point at a different KB: the written prefix (for a row from before v28, its
`target_kb`, which was the prefix as written) resolved now is not the stored
KB. That catches a shortname renamed or added in config since the file was
indexed, which no sync re-reads because the file's hash did not change, and a
row stored before the full name was. It does not count a link whose prefix
names no KB: that is a broken link (`broken_links`), and a rebuild writes the
same row. `index health` warns on it and names `pyrite index build`, which
re-reads every file and so clears the count. `tests/test_cross_kb_links.py`
pins all of it, the renamed-shortname probe through a real `config.yaml` and
the CLI included.
