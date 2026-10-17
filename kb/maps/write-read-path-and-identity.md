---
id: map-write-read-path-and-identity
title: "How should an update change a file? Topic map: the write path, reads, identity and hooks"
type: note
tags:
- map
- design
- write-path
- read-path
- identity
- update
- hooks
- files
---

# How should an update change a file?

Answers "how should an update change a file?", "what is an entry's id?",
"may a hook change an entry?". Layer 2 under [[design]] (principles P2 to P5).
Marks: **decided** = accepted ADR; **proposed** = ADR-0044 only (all of ADR-0038, 0039 and
0041 to 0045 but 0044 were accepted 2026-10-03, so their items read **decided**); **not built** = accepted, code absent.

## The design today

1. The Markdown files are the source of truth; the index is derived and
   rebuildable. **decided** [[adr-0001]].
2. A write to an existing entry is an operation on the file's own value (set,
   append, remove, unset, replace body). The model validates the result and never
   supplies bytes. A request that changes nothing writes nothing. **decided**
   ADR-0042 decisions 1 to 3. Built for update (#955, B6 P3a: `KBService._update`
   applies `Set`/`ReplaceBody` with `file_operations.apply` and publishes through
   `storage/entry_writer.py`), and the surfaces take `append`, `remove` and
   `unset` (P3b: CLI `--append/--remove/--unset`, MCP `kb_update`, REST PATCH
   `op`; the in-process callers send one operation through
   `KBService.apply_operations`); a create writes its values onto a skeleton
   the same way (C2); `add_link` and the other writers still emit whole files
   (P4).
3. A single-entry read reads the file's current bytes and returns a
   `content_hash`; lists and search stay index reads. **decided** ADR-0042
   decision 9. Built for the read (#753, `KBService._read_entry`); the type's
   defaults and normalised values still come back among the fields (#730 P5).
4. Hooks may refuse a write; they may not change one. What hooks added (links,
   parent status) is derived and shown as derived. **decided** ADR-0042
   decision 4, ADR-0045 decisions 3 and 7.
5. Identity is `(kb, id)`; one function answers "which id does this file
   hold"; lookup is by id and a filename hit is verified. **decided**
   [[adr-0038]] (step 1 landed).
6. With no `id:`, identity is the path relative to the KB root; `id:` is an
   optional pin; never the title. **decided** ADR-0042 decision 5 (amends
   [[adr-0038]] question 5). Code derives the id from the title.
7. A rename changes only the id and never moves the file; a wrong path is delete
   plus create. **decided** [[adr-0038]] question 1 (decided 2026-09-25).
8. Create is the one place Pyrite chooses spelling, and it never overwrites.
   **decided** ADR-0042 decision 13; [[adr-0038]] I5 holds today. Built (#955,
   B6 C2): `KBRepository.new_entry_text` writes the type's values onto
   `---\nid: <id>\n---` with `apply()`, published with `expect=ABSENT`
   (`storage/entry_writer.create_entry_text`); `to_frontmatter` decides the
   values, no emitter the bytes.
9. Writes are optimistic: each carries its base, merges per key against the
   file now, and conflicts are reported, never lost; a momentary sidecar
   `flock` covers only compare-and-replace. **decided** ADR-0042 decision 10
   (measured by spike 3). Built for update (#955): the base is the file at
   request start, a changed file is re-read and re-applied up to five times,
   then `ConflictError`; the lock is `utils/file_lock.py` in the KB's git dir
   (§10a A1, A2); a KB outside git is written with no lock and the result says
   `locked: false` (D1 (b), 2026-10-15).
10. `auto_embed: true` enqueues the entry and returns; it never loads the model
    in the write. **decided** [[adr-0035]].
11. A truncated body is never valid input to a write. **decided** [[adr-0034]].
12. An off-list enum value is refused when the write touches the field, and
    returned as a warning when its value is unchanged. **decided** [[adr-0008]]
    amendment 2026-10-01.

## Invariants a test could check

- I1 one file per id; I4 only delete removes content; I5 create never
  overwrites; I6 location is sticky; I7 delete is precise; I8 lookup is by id.
  [[adr-0038]] section 3.
- I9 after a write the touched rows match the files with no reconcile between
  ([[adr-0038]]); I10 history is by id, including across a rename.
- A one-field edit produces a diff of exactly that field's lines; a no-op update
  produces an empty diff and says so; a stale whole-document replace is refused
  and the hand edit survives (ADR-0041 acceptance, ADR-0042 acceptance).
- No `body_truncated` input is accepted by a write ([[adr-0034]] decision 2).

## ADRs in reading order

1. [[adr-0001]] why files. 2. ADR-0041 the principle (accepted). 3.
ADR-0042 the write path (accepted; spike 2 ran it on 52 real KBs and found
it holds, with amendments; concurrency and hand-made shapes remain). 4. [[adr-0038]] identity and I1 to I10 (accepted; read with
ADR-0042's amendment). 5. [[adr-0035]] and [[adr-0034]] for embedding and
bounded bodies. History only: [[adr-0015]] (on-load migration; ADR-0042
decision 7 makes migration an explicit command), [[adr-0003]] (superseded).

## Where the code starts

Components [[kb-service]], [[kb-repository]], [[document-manager]],
[[index-manager]], [[entry-model]]. Paths: `pyrite/services/kb_service.py`
(`_prepare`, `_update`, `rename_entry`), `pyrite/storage/repository.py`
(`find_file`, `_load_entry`, `rename`), `pyrite/storage/document_manager.py`,
`pyrite/models/base.py` (`Entry.save` and its load-time records).

## Tests that pin it

`tests/test_storage_invariants.py` ([[adr-0038]], strict xfails, one per known
violation), `tests/test_roundtrip_identity.py`,
`tests/test_update_never_loses_a_key.py`,
`tests/test_truncated_body_refused_on_write.py`,
`tests/test_writes_never_block_on_embedding.py`, `tests/test_enum_enforcement.py`.
`tests/test_doc_write_as_patch.py` runs ADR-0042's examples from
`docs/how-pyrite-edits-your-files.md` through `kb_update`; each one today's
code fails is a strict xfail in its `KNOWN_DIVERGENCES`, with what the code
does instead (two left after #955: the stale replace, the id-less file).
Remove a line when its example passes.
`tests/test_update_walks_every_surface.py` walks the hand-made shapes (CRLF,
BOM, YAML 1.1 bare values, a Hugo map, a legacy link, an alias, a nested
`metadata:`, both `updated_at` forms, an anchor, a root file, a tagged
value) through every update surface and in-process caller, with a byte
oracle; `tests/test_entry_file_writers_are_one_function.py` is the ratchet
over who may write an entry file's bytes.

## Known gaps

- `add_link`/`add_links`, rename's link rewrite, `schema migrate`, `ids pin`
  and software-kb's `migrate-standards` still emit or write whole files (B6
  P4); `Entry.save`, `to_markdown` and `_publish_exclusive` stay until the
  last of them is converted; hooks
  still change a create, and what a hook sets in place reaches an update's
  index row until the index derives it (P6); the id comes from the title; a
  single read returns defaults among the file's fields (P5). ADR-0042 and
  ADR-0045 address them; spike 2 and spike 3 (concurrency, `/` in ids; macOS
  only) are done. The CRLF, BOM, anchor and Hugo shapes are now pinned by the
  walking test; Linux locks run in CI; Windows is refused.
- `previous_ids` (history across a rename) is absent; most I1 to I10 are
  strict expected failures until [[adr-0038]] steps 2 to 5 land. The ADR was
  accepted 2026-10-03.
- Decided 2026-10-02 (ADR-0042 questions 4 and 5): a path id is the path
  without `.md`, with `/` separators; old title-derived ids do not keep
  resolving; an `id:` is part of the contract, files without one are reported,
  and the upgrade pins them. Not decided: `updated_at` stamping and an optional
  guard on a field set (questions 1 and 2).
