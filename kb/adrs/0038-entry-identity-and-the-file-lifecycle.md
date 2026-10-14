---
id: adr-0038
type: adr
title: "Entry identity and the file lifecycle"
adr_number: 38
status: accepted
date: 2026-09-25
tags: [architecture, storage, index, history, invariants, testing]
links:
- target: adr-0037
  relation: related
  kb: pyrite
- target: adr-0042
  relation: related
  kb: pyrite
---

# ADR-0038: Entry identity and the file lifecycle

> **Accepted by the maintainer, 2026-10-03.** Accepted as amended by ADR-0042 (the path
> id, `id:` in the contract); the amendment below is accepted with it.
>
> **Written as proposed** (spike, 2026-09-25). The maintainer accepts or rejects it. It
> answers the root cause of four circuit-breaker trips on 2026-09-25: Pyrite
> has no single definition of what an entry *is* on disk, so each storage
> change found another implicit rule, one per cold read (#438, #466, #447).
> The code described is `fix/391-extension-writes-through-pipeline` at
> `ce7dce71` (PR #466, still in the merge queue when this was written), so
> the ADR describes storage as it will be once #466 lands. Line numbers are
> from that commit.

## Context

An entry has three representations: a **file** (Markdown with YAML
frontmatter under the KB directory), an **index row** (`entry` table, keyed
`(id, kb_name)`, holding an absolute `file_path` and a `content_hash`), and a
**history** (git commits, recorded as `entry_version` rows keyed
`(entry_id, kb_name, commit_hash)`). Nothing states how the three relate.
Seven code paths each decide it for themselves.

### What each operation does today

| Operation | Code | File | Index row | History |
|---|---|---|---|---|
| **Create** | `KBService._prepare` (`services/kb_service.py:533-628`), `_prepare_and_save` (:630), `DocumentManager.save_entry(is_create=True)` (`storage/document_manager.py:33`) | Path = `type_schema.resolve_filename` (a `file_pattern` built from fields) or `<id>.md` (`storage/repository.py:206-217`), in the type's inferred subdirectory. Refused if `find_file(id)` finds anything (:606) or the resolved path exists (:620-627); the write is exclusive (`os.link`, falling back to `O_EXCL` + `os.replace`; `models/base.py:683-732`). | Upserted by `index_entry` straight after the write. | None until a commit; `VersionService.record_commit` then maps changed paths to ids *through the index* (`services/version_service.py:85-105`). |
| **Update** | `KBService._update` (:986-1111) → `save_entry(is_create=False)` | Loaded with `repo.load(id)` → `find_file`. Filename kept (`keep_filename=True`, #466). The subdirectory is kept unless the type's subdirectory is templated (`backlog/{status}`), then the file moves and the old one is `git rm`'d (`document_manager.py:76-100`, `_remove_old_file` :140). Type changes are refused (:1027). Undeclared keys go to `extra_frontmatter` if they came from the file, else `metadata` (:1071-1074); create puts them in `metadata` via `build_entry` (#447's rule, now in two places). | Upserted. | As create. |
| **Rename** (id change) | `KBService.rename_entry` (:1171-1236) → `KBRepository.rename` (`repository.py:463-616`) | Wikilinks rewritten in other files first (:568). `file_pattern` type: filename kept, only `id:` changes (:591-596, #466). Any other type: written to `<new_id>.md` in the same folder, old file unlinked (:597-607). Not through `DocumentManager`. | Not written directly: the service runs a full `sync_incremental` of the KB and then checks the new id resolves (:1219-1234). | Depends on the type: see §History below. |
| **Move** / **type change** | No operation. The only move is the templated-subdirectory move inside update. Type change is refused. `rename`'s docstring lists "move" as an r1700 follow-up. | | | |
| **Delete** | `KBService.delete_entry` (:1131) → `DocumentManager.delete_entry` (:175) → `KBRepository.delete` (`repository.py:445`) | `find_file(id)`, then `unlink` that one file. | Row `(id, kb)` deleted. | Its `entry_version` rows go with it (`ON DELETE CASCADE`, `storage/models.py:396-399`, with `PRAGMA foreign_keys = ON`, `storage/connection.py:81`). The same happens whenever a sync retires a row, including the old id's row after a rename. |
| **Restore** (git revert, checkout, restore) | No Pyrite operation. It is an external edit. `git checkout` sets mtime to now, so the next sync sees it. | | | |
| **External edits** (hand edit, `git mv`, a second file with the same id, a file without `id:`) | Nothing runs until a sync. | | See §Sync. | |
| **Sync, incremental** | `IndexManager.sync_incremental` (`storage/index.py:1004-1162`); `pyrite index sync`, `KBService.sync_index`, and every rename | Walks files by **path**. Known path: re-parsed only if file mtime > `indexed_at` (`_is_stale`, :699); if the id in it changed, the old row is retired when it still points at this path (:1079-1118, #466). Unknown path: parsed and upserted (:1124-1143). Rows whose id was not seen are deleted (:1149-1152). | | |
| **Sync, per KB** | `IndexManager.sync_kb` (:1164-1222); `pyrite kb reindex`, `KBRegistryService.reindex_kb` | Walks files, keys by **id**. Known id: re-indexed only if mtime > `indexed_at` (:1204-1213): **the path is not compared**. Unseen ids deleted. | | |
| **Full index** | `IndexManager.index_kb` / `index_all` (:491-574); `pyrite index build`, `init`, the index worker's rebuild | Upserts every parseable file (:524). **Never deletes a row.** | | |
| **History** | `IndexManager.index_with_attribution` (:1225) with `_same_entry_history` (:119-153); `VersionService.record_commit` (`version_service.py:36-127`) with `_same_entry_at` (:130) | `git log --follow` on the current path. The id is compared only **where the path changes**; at the same path, every older commit is attributed to the current id. | | |
| **Registry → KBConfig** | `KBRegistryService.get_kb_config` (`services/kb_registry_service.py:357`) → `PyriteConfig.kb_config_from_registry_row` (`config.py:512`) | A KB is `name → path`; rows store absolute paths built from it. `KBConfig` resolves its path (probed: syncing the same KB through a symlinked path changed 0 rows), so the spelling is stable. | | |

### Lookup: two definitions of "the file with id X"

`KBRepository.find_file` (`repository.py:306-357`) answers "which file is
entry X" for load, update, delete, rename and create's exists check:

1. **A filename match wins without reading the file** (`_find_by_filename`,
   :279-304): `X.md` at the root, then anywhere below. Whatever id that file
   holds, it is returned.
2. Otherwise a scan compares an **explicit** `id:` only (:349), with a naive
   `text.find("---", 3)` delimiter (:346), skipping `_`-prefixed folders
   (:339) that `list_files` (:618) does not skip.

The loader (`_load_entry`, :54; `entry_from_frontmatter`) and sync use a
different definition: the line-anchored delimiter, and an id **derived from
the title** when there is no `id:` line. `entry_id_from_markdown`
(`models/core_types.py:468`) is a third caller of the loader's definition.
So sync can index an id that no lookup can find, and a lookup can return a
file that holds another id.

### History

`_same_entry_history` stops at a path change whose older path held another
id, and at the commit that added the file. A rename of a `file_pattern`
entry keeps its path (#466), so its pre-rename commits stay in its history.
A rename of any other entry moves the file, the older path holds the old id,
and the history is cut at the rename. The same user action gives two
different histories depending on the type (#489). An in-place id change
(a hand edit) inherits the other entry's history (#490).

### Measured: the invariants against today's code

`tests/test_storage_invariants.py` (on branch
`spike/entry-identity-invariants`) drives a real KB on disk, a real
`PyriteDB`, `KBService`, `IndexManager` and `KBRepository` with a Hypothesis
`RuleBasedStateMachine`. Rules: create (a plain `note`, and a `decision`
type with `file_pattern: "{number:04d}-{title}.md"`), update (title, body,
tags), rename, delete, `sync_incremental`, `index_kb`, `sync_kb`, and
external edits (write a file, including a duplicate id or no `id:` line;
change an `id:` in place; edit a body keeping the mtime; `mv`; `rm`).

It has two layers. **One hand-written test per known violation** replays the
sequence Hypothesis shrank it to. Each is a strict xfail tied to one issue,
and it must fail with an `AssertionError` whose message starts `I<n>:`, so a
crash does not count as the expected failure. **One exploratory run** checks
only the invariants with no known violation (I5, and I9 outside the shapes
of its known bugs, which are excluded by the state that causes them, not by
seed). It runs 150 examples of 20 steps with a pinned `@seed(20260925)`. An
earlier version derandomized by the test's name, so renaming the machine
class flipped results and hid the I9 violations below. The three history
checks are plain tests with a real git repo. The whole file runs in about
10 s at `-n 4`.

Mutation check: each of these, applied alone, turns the exploratory run red
on the pinned seed, and on seeds 1 to 10, while the unmutated code passes on
all eleven seeds:

- M1, create may overwrite: the exists checks off and a non-exclusive write.
  This fails I5.
- M2, update does not write the index (`document_manager.py:109` skipped for
  updates). This fails I9.
- M3, delete keeps the row (`document_manager.py:188` removed). This fails
  I9.

| Invariant | Today | Minimal failing sequence (shrunk) | Issue |
|---|---|---|---|
| I1 | **fails** | `ext_write(alpha.md, no id:, title "Beta")`; `create(note "Beta")` → two files claim `beta` | #484 |
| I2 | **fails** | `create(decision dec-1)`; `ext_rm`; `index_kb` → row with no file. `create`; `mv notes/alpha.md x.md`; `sync_kb` → row keeps the old path. `create`; edit body keeping mtime; `sync_incremental` → stale row | #486, #487, #495 |
| I3 | **fails** | `create(note "Alpha")`; `ext_write(alpha.md, no id:, title "Alpha")`; `sync`; `sync` → `{'updated': 1}` every time | #485 |
| I4 | **fails** | `ext_write(alpha.md, no id:, title "Beta")`; `delete(alpha)` → removes entry `beta` | #483 |
| I5 | holds | on the exploratory run (seed 20260925 and seeds 1 to 10, 150 × 20 steps each), which M1 turns red. #466's exclusive create | |
| I6 | **fails** | `ext_write(alpha.md at root, id gamma)`; `update(gamma, title)` → moved to `notes/alpha.md`. `rename` of an id-named file moves it (the proposed rule forbids that) | #488, #489 |
| I7 | **fails** | `ext_write(alpha.md, id beta)`; `delete(alpha)` → removes `beta`'s file | #483 |
| I8 | **fails** | `ext_write(beta.md, no id:, title "Alpha")` → `alpha` indexed, `find_file('alpha')` is None. `ext_write(alpha.md, no id:, title "Beta")` → `find_file('alpha')` returns a file holding `beta` | #483, #484 |
| I9 | **fails** | `create(note "Gamma")`; `ext_write(x.md, id gamma)`; `delete(gamma)` → ok; `x.md` still holds `gamma` and has no row. Otherwise holds (exploratory run; M2 and M3 turn it red) | #494 |
| I10 | **fails** (holds for `file_pattern` renames) | note `alpha`: commit, edit, commit, rename→`omega`, commit → history(`omega`) = `[rename]`. Hand edit `id: alpha`→`zeta` → history(`zeta`) = `[zeta, alpha]` | #489, #490 |

One of the ten holds (I5). Every failure is one of three root causes:

- Lookup and loading disagree on a file's id, and nothing handles an id held
  by two files (#483, #484, #494).
- The three index walks each reconcile differently (#485, #486, #487, #495).
- "Keep the location" and "change the id" have no single owner (#488, #489,
  #490, #493).

## Decision

### 1. The identity key is `(kb, id)`, and the frontmatter is its only source

An entry **is** its id within its KB. The id a file holds is what the
loader derives from it: the explicit `id:`, or the id generated from the
title when there is none. There is exactly **one function** that answers
"what id does this file hold", and lookup, sync, history and create's
exists check all call it.

The path is a **location**, derived once at creation (`resolve_filename` or
`<id>.md`, in the type's subdirectory) and then **sticky**. The filename is
a hint for a fast lookup, never evidence: a filename hit is verified against
the id the file holds. The index row is a **cache** of `(kb, id) → (path,
content_hash, fields)`. It can always be rebuilt from the files, and it is
never the tiebreaker.

Why the id and not the path: links, the API, MCP and the CLI address entries
by id; `file_pattern` names are built from fields and can collide across ids
(#466); and a KB's files are edited by hand and by git, so the path is the
part most likely to change without Pyrite knowing. Why not a new immutable
UUID in every file: it is the cleanest answer to history across renames and
hand edits, but it is a migration of every file in every KB, and a
hand-written file has none. `previous_ids` (§3) gives the rename half of it
without a migration. The UUID stays the fallback if `previous_ids` proves
insufficient.

### 2. What each operation does

| Operation | File | Index row | History |
|---|---|---|---|
| Create | Writes a new file at the resolved location, exclusively; refuses if the id is held by any file or the path exists. | Upserts `(kb, id)`. | Starts at the commit that adds the file. |
| Update | Rewrites the one file that holds the id, in place. The path changes only for a templated subdirectory, and then only the folder. | Upserts. | Continues. |
| Rename (id change) | Refuses `rename(x, x)` with a `ValidationError`: a same-id rename is the case that exposed the I9 violation #493, and has nothing to do. Otherwise rewrites `id:` in the same file, **for every type**; appends the old id to a managed `previous_ids` list; rewrites wikilinks. The path does not change. | Upserts `(kb, new)`, moves the old id's `entry_version` rows to it, then retires `(kb, old)` (today the cascade drops them): directly, no full sync. | Continues across the rename: a commit whose file held any of `previous_ids` belongs to the entry. |
| Move (location change, a future explicit operation) | Moves the file (for example to the current naming rule). The id does not change. | Upserts the new path. | Continues across the path change, because the id matches at both ends. |
| Delete | Removes the files that hold the id, and only those. | Deletes `(kb, id)`. | Its `entry_version` rows go with it, as today; git keeps the commits. |
| External edit, restore | Pyrite does not write. | The next reconcile makes the rows match the files. | By id, as below. |
| Reconcile (`sync_incremental`, `sync_kb`, `index_kb`) | Never writes a file. | One implementation: rows become exactly the ids of parseable files. Two files that claim one id are **reported** (`duplicates` in the result, `index health`) and the same one wins every time (the lexicographically first KB-relative path). A known path is re-parsed when its mtime **or size** differs from the indexed one, with the hash as a tiebreaker. | |
| History | | | The commits for `(kb, X)` are those in which the entry's file held `X` or one of `X`'s `previous_ids`, checked **at every commit**, not only at path changes. |

The one behaviour change a user sees: `pyrite rename alpha omega` leaves the
file at `notes/alpha.md` with `id: omega`, where today it writes
`notes/omega.md`. That is already what happens for every `file_pattern` type
since #466. A wrong *path* is corrected by delete + create, with a fresh
history (open question 1, decided).

### 3. The invariants

Each is checked by `tests/test_storage_invariants.py` after every step. "A
Pyrite operation" means create, update, rename, delete and the three
reconciles. "Holds X" means the loader derives id X from the file.

- **I1: one file per id.** No Pyrite operation raises the number of files
  that hold an id above one.
- **I2: the index equals the files.** After any reconcile, the index has a
  row for exactly the ids that some parseable file holds; each row's path is
  a file that holds its id; and when one file holds the id, the row's
  `content_hash` is that file's current hash.
- **I3: reconcile is idempotent and deterministic.** A second reconcile with
  no file change reports nothing added, updated or removed, and moves no
  row. Duplicate ids are reported, not resolved by walk order.
- **I4: only delete removes content.** After any Pyrite operation other than
  a successful `delete(X)`, every id held on disk before is still held
  after, except the old id of a successful rename. A reconcile changes no
  file.
- **I5: create never overwrites.** A create, successful or refused, leaves
  every file that existed before byte-identical.
- **I6: the location is sticky.** A successful update leaves the entry's
  path unchanged (except the folder of a templated subdirectory). A
  successful rename leaves the path unchanged for every type.
- **I7: delete is precise.** `delete(X)` removes only files that held X.
- **I8: lookup is by id.** `find_file(X)` returns either nothing or a file
  that holds X; and when exactly one file holds X, it returns that file.
- **I9: writes are written through.** After a successful create, update,
  rename or delete, the rows of the ids it touched match the files (path and
  hash), with no reconcile in between.
- **I10: history is by id.** The recorded history of `(kb, X)` contains only
  commits in which the entry's file held X or one of X's `previous_ids`, and
  a Pyrite rename keeps the pre-rename history, for every type.

### 4. Conflicts with today's code

- Violates **I1** at `repository.py:349` (`find_file` compares only an
  explicit `id:`; the loader derives one) → create's check at
  `kb_service.py:606` passes and a second file is written. #484.
- Violates **I2** at `index.py:524-531` (`index_kb` only upserts). #486.
- Violates **I2** at `index.py:1204-1213` (`sync_kb` ignores a moved path).
  #487.
- Violates **I2** at `index.py:699-703` (staleness is mtime-only in both
  syncs, though `content_hash` is stored at :180). #495.
- Violates **I9** at `repository.py:501-509` with the `renamed` guard in
  `KBService.rename_entry`. `rename(x, x)` is a silent no-op that reports
  success, so an entry that is not yet indexed stays unindexed. #493.
- Violates **I9** at `document_manager.py:175-189`. Delete unlinks the one
  file `find_file` returns and drops the row, while a second file holding the
  id stays on disk with no row. #494.
- Violates **I3** at `index.py:1124-1136` (a duplicate id's losing path is
  re-parsed and re-upserted on every sync; nothing reports it). #485.
- Violates **I4**, **I7** and **I8** at `repository.py:279-284, 327-330`
  (a filename hit is returned without reading its id; `delete` unlinks it at
  :450-452). #483.
- Violates **I8** at `repository.py:337-356` (derived ids are not found).
  #484.
- Violates **I6** at `document_manager.py:84-88` with `repository.py:417-418`
  (`subdir=None` means both "at the root" and "infer"). #488.
- Violates **I6** and **I10** at `repository.py:597-607` (an id-named file
  moves on rename) with `index.py:146-149` (history is cut where the path
  changes). #489.
- Violates **I10** at `index.py:146` and `version_service.py:108-113` (the id
  is checked only when the path changes). #490.
- Not an invariant violation, but the same root cause: where an undeclared
  key goes is decided twice, in `build_entry` (create) and in
  `kb_service.py:1071-1074` (update), which is #447.

### 5. Relationship to ADR-0037

Both ADRs have the same shape: a rule that was shared but decided in many
places gets one owner, and a structural test that keeps it there. ADR-0037
does it for access decisions (`services/access_policy.py`), this one for
entry identity (`find_file` and one reconcile in `storage/`). Their
footprints barely touch. ADR-0037's themes live in `server/`, `auth_service`
and `exceptions.py`; this ADR's live in `storage/repository.py`,
`storage/index.py`, `storage/document_manager.py`, `services/version_service.py`,
and the rename, update and delete methods of `KBService`. The two shared
points:

- **Exceptions.** A new `DuplicateIdError` (or a `duplicates` result) should
  carry ADR-0037 theme 2's `error_code`. Steps 1 to 3 below report
  duplicates in results, not exceptions, so they do not wait for it.
- **`KBService` signatures.** ADR-0037 decision 3 defers "services take a
  principal". Step 5 below changes only the bodies of `rename_entry` and
  `delete_entry`, so the two do not conflict. If decision 3 is revisited,
  sequence it after step 5.

The migrations can run in parallel: ADR-0037 themes 0 to 3a are pulled into
0.26, and so are steps 0 to 2 here.

## Migration

Each step is one PR and ends with its xfails removed from
`tests/test_storage_invariants.py` (an XPASS fails the suite, so a fix
cannot forget).

| # | Step | Fixes | Files | Model | After |
|---|---|---|---|---|---|
| 0 | **Land the invariant harness.** `tests/test_storage_invariants.py` and the `hypothesis==6.168.1` dev pin, with its strict xfails. No production change. | — | the test, `pyproject.toml` | Sonnet | #466 |
| 1 | **One id reader, lookup by id.** One function (the loader's) answers "which id does this file hold"; `find_file` verifies a filename hit and scans with it; `entry_id_from_markdown` uses it; the scan's skip rules match `list_files`. | #483, #484 (I1, I4, I7, I8). Delete removes only files certain to be the entry and refuses an ambiguous derived id; #494 (copies of an explicit id elsewhere in the KB) moves to step 2 | `storage/repository.py`, `models/core_types.py`, `storage/document_manager.py` (delete) | Sonnet | 0 |
| 2 | **One reconcile.** `index_kb`, `sync_kb` and `sync_incremental` share one walk-and-reconcile: unseen rows retired, a path change applied, staleness by mtime or size, duplicates reported and resolved by path order. `pyrite index sync` and `index health` print duplicates. | #485, #486, #487, #495 (I2, I3), #494 (explicit-id copies found at reconcile) | `storage/index.py`, `cli/index_commands.py`, `services/kb_registry_service.py` | **Opus** | 1 |
| 3 | **Sticky location.** "Keep this folder" and "infer the folder" become different arguments; `KBRepository.rename` keeps the path for every type (open question 1, decided); `rename(x, x)` is refused. | #488, #489 file half (I6), #493 (I9) | `storage/document_manager.py`, `storage/repository.py`, `services/kb_service.py` (`rename_entry`) | Sonnet | 1 |
| 4 | **History by id.** `rename` records `previous_ids` (a managed field); `_same_entry_history` and `VersionService.record_commit` check the id at every commit and accept `previous_ids`. | #489 history half, #490 (I10) | `storage/index.py`, `services/version_service.py`, `storage/repository.py`, `services/kb_service.py` (`_MANAGED_FIELDS`) | **Opus** | 3 |
| 5 | **One write owner.** `DocumentManager` owns every file-and-row transition: rename and delete go through it; rename writes its two rows directly (I9) instead of a full `sync_incremental`; the undeclared-key rule (#447) lives in one place. A structural test fails if `storage/repository.py`'s write methods are called from outside `DocumentManager`. | I9 for rename without a full sync; #447's split | `storage/document_manager.py`, `services/kb_service.py`, a guard test | **Opus** | 2, 4 |

Backlog items, one per step: [[storage-invariants-harness-land-the-adr-0038-state-machine-with-strict-xfails]] (0),
[[storage-one-id-reader-find-file-looks-up-by-id-not-by-filename-adr-0038-step-1]] (1),
[[index-one-reconcile-for-index-build-reindex-and-sync-report-duplicate-ids-adr]] (2),
[[storage-a-file-s-location-is-sticky-across-update-and-rename-adr-0038-step-3]] (3),
[[history-by-id-rename-records-previous-ids-attribution-checks-the-id-at-every]] (4),
[[storage-documentmanager-owns-every-file-and-row-transition-adr-0038-step-5]] (5).

## Consequences

- Every future storage change has a list to be checked against. A cold read
  asks "which invariant does this touch", and the state machine asks it on
  every push: about 10 s at `-n 4`.
- `find_file` gets slower on a miss after step 1: a filename hit must be
  parsed, and a scan is still a scan. The index can answer first once step 2
  makes it trustworthy. Measure on the pyrite KB (about 2,000 entries)
  before and after.
- After step 3, filenames and ids drift apart after renames. That is already
  true for every `file_pattern` type. An explicit `move` operation is the
  way back to the naming rule.
- Duplicates become visible. KBs that have them today (hand-copied files)
  will start reporting them. That is the point, but it is a new line of
  output in `pyrite index sync`.

## Open questions for the maintainer

1. **Rename and the filename. DECIDED (maintainer, 2026-09-25): (a).** A
   rename changes only the id and never moves a file, for any type. When the
   *path itself* is wrong -- the maintainer's example: `jacob_is_a_monkey.md`,
   where Jacob is in fact a rabbit, so the filename makes a false claim --
   that is not a rename. It is a **delete followed by a new create**: the new
   entry starts a fresh history, and nothing links it to the old one. Links to
   the old id become dangling and are reported by `qa` and `index health`, which
   is intended, since they pointed at the false claim. Out of scope here and
   noted for later: the deleted file's content, false claim included, stays
   in the KB's git history. Removing a claim from the record (a legal or
   defamation concern) is a separate purge or redaction operation that delete
   does not provide.
2. **`previous_ids` or `aliases`. DECIDED (maintainer, 2026-09-26): a separate managed `previous_ids`.** Old ids and history follow the entry; user-facing `aliases` stays the user's. A separate managed field (this ADR), or
   the existing user-facing `aliases`, which would also make `[[old-id]]`
   resolve after a rename.
3. **Duplicate winner. DECIDED (maintainer, 2026-09-26): the lexicographically first KB-relative path wins, and the duplicate is reported loudly by `index health` and sync; the other id is never silently dropped.** Lexicographically first KB-relative path (this ADR),
   or the most recently modified file, or refuse to index either until the
   duplicate is resolved.
4. **Staleness signal. DECIDED (maintainer, 2026-09-26): mtime or size for sync, with a content hash on rebuild and in `index health`.** mtime or size (cheap; misses a same-size edit that
   keeps the mtime), or always hash (exact; one full read per file per
   sync). This ADR takes mtime or size and leaves hash-always for measurement.

5. **Derived id when a file has no `id:`. DECIDED (maintainer, 2026-09-26): from the filename, not the title.** Filenames are stable; titles change. This unblocks step 2.

> **Amendment (2026-10-02), to question 5. Accepted by the maintainer,
> 2026-10-03, with ADR-0042.** Written by the ADR-0042 revision. It amends the
> decision above and two sentences of this ADR.
>
> **With no `id:` key, an entry's identity is its path relative to the KB
> root. `id:` is an optional pin that survives a move. Identity is never
> derived from the title.** Pyrite writes an `id:` only on files it creates, or
> when asked.
>
> - **Why not the filename alone.** `_index.md`, a leaf bundle's `index.md` and
>   `blog/intro.md` against `docs/intro.md` would share one id, and §2's
>   duplicate rule would then index the first path and report the rest. A path
>   is unique.
> - **Section 1**, "the id generated from the title when there is none", is the
>   sentence the 2026-09-26 decision had already replaced; it reads "the id
>   derived from the path".
> - **Section 2, row Update**, "The path changes only for a templated
>   subdirectory, and then only the folder": gains "and only for a file with an
>   `id:` pin". A type whose folder is templated by a field (`backlog/{status}`)
>   needs the pin, because a change of that field moves the file. Pyrite creates
>   those files and pins them. An update that would move an unpinned file sets
>   the field, leaves the file, and reports "not moved: no id pin".
> - **A move of an unpinned file changes its identity.** Links to the old id
>   dangle and `qa` and `index health` report them. Pinning prevents it.
> - **Existing links that point at title-derived ids.** Upgrade runs a one-time
>   report of every id-less file whose old (title-derived) id differs from its
>   path, and of every link, star, review and `entry_version` row that referred
>   to the old id. A command applies the rewrite on request. Nothing is
>   rewritten silently (ADR-0042 decision 5).
> - **I8 and the `entry-da39a3ee` case.** An untitled file no longer collides:
>   each has its own path.
> - **Spelling of the path id** (extension, separators) is ADR-0042 question 4.

---

<small>First measured on `ce7dce71` (#466's head); re-run after #466 merged,
on `dev` `c7863013`. Invariant results:
`.venv/bin/pytest tests/test_storage_invariants.py -n 4` (2 passed,
15 xfailed, about 10 s), shrunk sequences with
`PYRITE_INVARIANT_SHRINK=1`. The single-operation checks in the I2 row
(`sync_incremental` misses a same-mtime edit; `sync_kb` keeps a moved path;
`index_kb` keeps a deleted or re-id'd row; `sync_incremental` handles `mv`,
`rm` and an in-place id change) were probed one by one against the state
machine's own rules. The registry-path probe ran with `HOME`,
`PYRITE_CONFIG_DIR` and `PYRITE_DATA_DIR` set to one temp dir.</small>
