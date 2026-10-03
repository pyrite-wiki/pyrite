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
Marks: **decided** = accepted ADR; **proposed** = ADR-0041 to ADR-0045 (draft
PR #635) or [[adr-0038]]; **not built** = accepted, code absent.

## The design today

1. The Markdown files are the source of truth; the index is derived and
   rebuildable. **decided** [[adr-0001]].
2. A write to an existing entry is an operation on the file's own value (set,
   append, remove, unset, replace body). The model validates the result and never
   supplies bytes. A request that changes nothing writes nothing. **proposed**
   ADR-0042 decisions 1 to 3. Code today re-emits the whole file from the model.
3. A single-entry read reads the file's current bytes and returns a
   `content_hash`; lists and search stay index reads. **proposed** ADR-0042
   decision 9. Code today reads the index row.
4. Hooks may refuse a write; they may not change one. What hooks added (links,
   parent status) is derived and shown as derived. **proposed** ADR-0042
   decision 4, ADR-0045 decisions 3 and 7.
5. Identity is `(kb, id)`; one function answers "which id does this file
   hold"; lookup is by id and a filename hit is verified. **proposed**
   [[adr-0038]] (step 1 landed).
6. With no `id:`, identity is the path relative to the KB root; `id:` is an
   optional pin; never the title. **proposed** ADR-0042 decision 5 (amends
   [[adr-0038]] question 5). Code derives the id from the title.
7. A rename changes only the id and never moves the file; a wrong path is delete
   plus create. **proposed** [[adr-0038]] question 1 (decided 2026-09-25).
8. Create is the one place Pyrite chooses spelling, and it never overwrites.
   **proposed** ADR-0042 decision 13; [[adr-0038]] I5 holds today.
9. Writes are optimistic: each carries its base, merges per key against the
   file now, and conflicts are reported, never lost; a momentary sidecar
   `flock` covers only compare-and-replace. **proposed** ADR-0042 decision 10
   (measured by spike 3). No `flock` exists in `pyrite/` today.
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

1. [[adr-0001]] why files. 2. ADR-0041 the principle (proposed). 3.
ADR-0042 the write path (proposed; spike 2 ran it on 52 real KBs and found
it holds, with amendments; concurrency and hand-made shapes remain). 4. [[adr-0038]] identity and I1 to I10 (proposed; read with
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
Proposed, not yet written: `tests/test_doc_write_as_patch.py` (ADR-0042).

## Known gaps

- Update re-serialises the file; hooks write links and parent status; the id
  comes from the title; a single read comes from the index. ADR-0042 and
  ADR-0045 address them; ADR-0042 is not accepted; spike 2 and spike 3 (concurrency, `/` in ids; macOS only) are done, and the checks
  not run (CRLF, BOM, anchors, Hugo; Linux and Windows locks) are listed in the ADR.
- `previous_ids` (history across a rename) is absent; most I1 to I10 are
  strict expected failures until [[adr-0038]] steps 2 to 5 land. The ADR is
  still labelled `proposed` though its questions are decided.
- Decided 2026-10-02 (ADR-0042 questions 4 and 5): a path id is the path
  without `.md`, with `/` separators; old title-derived ids do not keep
  resolving; an `id:` is part of the contract, files without one are reported,
  and the upgrade pins them. Not decided: `updated_at` stamping and an optional
  guard on a field set (questions 1 and 2).
