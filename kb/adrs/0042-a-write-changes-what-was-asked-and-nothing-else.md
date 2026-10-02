---
id: adr-0042
type: adr
title: "A write changes what was asked and nothing else: entries are patched, not re-serialised"
adr_number: 42
status: proposed
date: 2026-10-02
tags: [architecture, storage, write-path, round-trip, plugins, invariants]
links:
- target: adr-0001
  relation: related
  kb: pyrite
- target: adr-0029
  relation: related
  kb: pyrite
- target: adr-0038
  relation: related
  kb: pyrite
- target: adr-0039
  relation: related
  kb: pyrite
- target: adr-0041
  relation: related
  kb: pyrite
---

# ADR-0042: A write changes what was asked and nothing else

> **Proposed** (spike `write-as-patch`, 2026-10-02). The maintainer accepts or
> rejects it. Measurements are from `origin/dev` at `d35eae77`, ruamel.yaml
> 0.19.1; the evidence is in the spike's `findings.md`.

## Context

This ADR applies ADR-0041 (a KB is files any tool can use; Pyrite is additive) to
the entry write path.

A KB lives as Hugo-style files on disk: Markdown with YAML frontmatter, usable by
any tool that handles that kind of file. Pyrite makes such KBs easy to work with,
and it should be additive to a person maintaining the files on their own, enough
that it is their preferred way to work with them (maintainer, 2026-10-02). Also
decided that day: a write is meant to make a change. Do what was asked, lose
nothing, report it. No ownership records or sidecars about files others edit.

So the write path is judged by one question: is someone who edits these files by
hand, or with Hugo or another tool, glad Pyrite touched them?

Every interface (CLI flags, MCP and REST JSON, the web form) hands Pyrite plain
values. Pyrite provides an interface that is not YAML for editing YAML files.
Today it parses the file into a model, and on save emits the whole file again,
with a growing set of records to remember what the file looked like
(`_absent_default_keys`, `_unrepresented_keys`, `_unparsed_timestamp_keys`,
`_source_frontmatter`, `_restyle_like_source` in `pyrite/models/base.py`), and an
echo check against the index row (`KBService.split_echoed_update`). #46, #151,
#173, #557, #561, #568 and #569 are each one more rule in that scheme.

### Measured: what a save does to a file nobody asked to change

Load and save, no change, over 28,123 real entry files from the KBs on the
maintainer's machine:

| | Byte-identical | Changed |
|---|---|---|
| Today's code | 20,917 (74.4%) | 7,206 (25.6%) |
| ruamel round trip of the frontmatter alone | 26,588 (94.5%) | 1,535 (5.5%) |

Today's 7,206 include: `id:` added to 321 files that had none; `title: ''` added
to 125; `participants:` renamed `actors:` in 191; comment lines removed in 88;
the end of file or the blank lines after the fence rewritten in about 5,200. On
hand-made files: CRLF becomes LF, a BOM is dropped, `flag: no` becomes
`flag: "no"`, `null` becomes empty, a multi-line scalar is re-wrapped, a
zero-indent list is re-indented. A Hugo post gains `id:` the first time an agent
sends its own read back. No value of any key changed in any file.

The 5.5% is the floor for any design that parses and re-emits: it is what ruamel
itself respells (blank lines, wrapping, indentation, quoting).

## Options

Each was built and run on the same inputs: the corpus above for the no-op; 2,813
real files and 26 hand-made shapes for ten operations.

| | No-op, 28,123 files | One scalar, 2,813 files | One item in a list of maps, 1,165 files | Wrong output |
|---|---|---|---|---|
| **Today**: model, whole-file emit, restyle | 74.4% identical | 74.5% minimal | 17.7% minimal; 8.6% wrong | yes |
| **A**: ruamel round-trip document, mutate changed keys, dump | 94.5% identical | 94.8% minimal | 88.7% minimal | no |
| **B**: splice at top-level key granularity | 100% identical | 100% minimal | 95.9% minimal | no |
| **C**: B plus token, list-append and nested-leaf edits, falling back to B | 100% identical | 100% minimal | 100% minimal | no |

- **A** keeps comments and is the smallest change to the code. It cannot give a
  byte-identical no-op, and every untouched key depends on the emitter.
- **B** holds the no-op and serializer properties. A changed list of maps is
  re-emitted whole, and a changed scalar loses its trailing comment.
- **C** adds about 150 lines to B and holds all three properties on every input.

## Decision

### 1. The file is the source of truth up to the moment of writing

An update reads the file's current text, works out which keys changed, and
replaces only those keys' spans in that text. Pyrite never emits a byte for a key
it was not asked to change. The body is its own span: the bytes between the
closing fence and the body's first character, and after its last, are kept.

### 2. One function applies changes, and checks itself

`apply_changes(text, set, unset, touch, body) -> (new_text, report)`, a pure
function in one module. After splicing it parses the result: every key outside
the change set must parse to what it was, every changed key to what was asked.
If a narrow edit fails that check it widens to the whole key. If the whole-key
edit fails, the write is refused. There is no fallback to re-emitting the file.

### 3. What counts as a change

- **Asked for:** load the file into the model, apply the request and the hooks,
  and compare `to_frontmatter()` before and after, plus the keys the request
  named. The same code serialises both sides, so its version cancels out.
- **Already so:** a key whose file value equals the new value is not written. A
  string equals a YAML date or timestamp when it is that value's ISO form or the
  same instant; int and float compare by value; a bool never equals an int.
- #569's `defaulted` and `normalized` lists keep deciding whether a sent value
  equal to Pyrite's reading was deliberate.

### 4. Absent means not mentioned; removal is explicit

On every interface a key the request does not carry is left alone. Removing a key
is a named operation (`unset`), not `null` and not omission.

### 5. Spelling of what Pyrite does write

- A new key goes on the last line of the frontmatter, block style.
- A replaced scalar keeps its quote style and trailing comment. A replaced list
  keeps flow or block style. A new list item copies its neighbours' indentation
  and quoting. New lines use the file's line ending.
- A string a YAML 1.1 reader would misread is quoted when Pyrite emits it.
- A key that defines or uses a YAML anchor is refused for change; the response
  says so.

### 6. Bookkeeping never rides alone, and repairs are commands

- `updated_at` is written only where the file has the key, and only when the
  request changed something. A no-op writes nothing and says so.
- Adding `id:`, renaming an alias key, stamping `_schema_version`, dropping a
  stray `body:` key and re-quoting existing values are explicit operations (`qa
  fix`, schema migrate), never side effects of an update.

### 7. A stale request is refused

Single-entry reads return the file's `content_hash`; an update that carries it is
refused when the file's hash differs, with a conflict that says to re-read. A
request that names fields and carries no hash proceeds: it is its own delta.

### 8. Create is the one place Pyrite chooses spelling

A new file is emitted whole, as #86 decided. File-based creates copy the author's
bytes.

## Acceptance tests

The three properties are stated as a document a user can read,
`docs/how-pyrite-edits-your-files.md`, and `tests/test_doc_write_as_patch.py`
runs every example in it: the `file` block is written into a KB, the `update`
block is sent through `kb_update`, and the file's diff must equal the `diff`
block exactly. An empty `diff` block means byte-identical. On today's code 6 of
the 7 examples fail; on the spike's prototype all 7 pass.

**Property 1: a one-field edit yields that field's lines.**

```file
---
# reviewed by hand, do not reorder
id: field-notes
title: Field notes   # working title
type: note

tags: [alpha, beta]
links:
- target: other-entry
  relation: related
flag: no
---

    an indented first line

Text.
```
```update
{"title": "Field notes, second draft"}
```
```diff
-title: Field notes   # working title
+title: Field notes, second draft   # working title
```

**Property 2: sending a read back changes nothing** (the file has CRLF line
endings; `echo` sends the `kb_get` result as the update).

```file crlf
---
# header comment
id: echo-me
title: "Quoted title"
type: note
date: 2026-01-15
tags: [alpha, beta]  # trailing
---

Body.
```
```update echo
{}
```
```diff
```

**Property 3: only the changed key is serialised.** During the update the test
watches the YAML emitter and fails if it is handed any top-level key of the file
that the request did not name.

```file emitter
---
id: narrow
title: Narrow
type: note
tags: [a, b]
summary: left alone
---

Body.
```
```update
{"summary": "rewritten"}
```
```diff
-summary: left alone
+summary: rewritten
```

The document's other examples: a Hugo post gains nothing on an unrelated edit;
the same value in another spelling is not a change; a new key goes at the end; a
body edit leaves oddly spaced frontmatter alone.

## Consequences

**Easier**
- A hand-edited or Hugo-authored file keeps its comments, quoting, line endings,
  BOM and layout. The diff of a Pyrite write is the change.
- About 330 of the 969 lines of `pyrite/models/base.py` go: the three record
  sets, their `__setattr__` hooks, the restyle and the pristine-probe code. In
  the prototype, with those records emptied, 7,625 of 7,647 backend tests and all
  1,044 extension tests passed.
- A ruamel upgrade cannot respell a KB.
- #569's rule is applied in one place against the file.

**Harder**
- There are two code paths that produce file text: create (emit) and update
  (splice).
- The splice depends on ruamel's line and column data for locating spans; the
  check after every write is what makes that safe.
- Files stop being normalised as a side effect. A KB that wants uniform style
  runs a formatter on purpose.
- 22 tests change: 13 pin defects or normalisation that goes away, 4 read a
  load-time record that becomes a computed function, 3 call `to_markdown()` on a
  loaded entry, 1 pins the `body:` cleanup, 1 pins #568's requote-on-touch.

**The plugin contract (ADR-0040).** No signature changes; none of the 37
extension entry types needed an edit. Three clauses must be written before the
contract freezes in 0.27: `to_frontmatter()` is a deterministic, side-effect-free
reading as values and no longer decides a byte of an existing file; what
`save()` and `to_markdown()` mean on a loaded entry; and `FRONTMATTER_ALIASES`
must name each alias's target key or become a migration.

**Phasing** (each one reviewable PR): (1) the doc and its runner, as expected
failures; (2) the patch function, not wired; (3) wire the update path; (4)
delete the load-time records; (5) the other writers and the explicit repairs;
(6) the interfaces: `content_hash`, `unset`, the response report; (7) ADR-0040's
text.

## Alternatives considered

- **A, ruamel round trip.** Rejected: 5.5% of untouched real files change.
- **B alone.** Workable. Rejected for the 4% of list-of-maps edits with larger
  diffs and the lost trailing comments.
- **Keep today's design and keep adding records.** Rejected: 25.6% of untouched
  files change after seven issues' worth of rules.
- **Diff the incoming document against the file as it is now, with no base.**
  Rejected: a stale whole-document echo reverts hand edits (reproduced).
- **Per-key three-way merge using a base from git.** Deferred. It works when the
  caller's read is the base (reproduced), but an uncommitted hand edit has no
  base in git. Refusing is correct and simple.
- **TOML and JSON frontmatter.** Out of scope. Today both are reported as
  malformed and left untouched; the splice would need a locator and emitter per
  format.

## Questions for the maintainer

1. **Bare `yes`/`no`/`on`/`off` in untouched keys stay as written?** This
   narrows #568 to bytes Pyrite emits. Recommended: yes.
2. **A file with no `id:`.** Never add it on update, except that a title change
   would change the derived id (ADR-0038 §1). Pin `id:` on that edit, or refuse
   it? Recommended: pin, and report it.
3. **Stale requests:** refuse on hash mismatch (recommended), or build the
   three-way merge now?
4. **`unset`:** add key removal to CLI, MCP and REST? Recommended: yes, as one
   named argument.
5. **A no-op request writes nothing, including `updated_at`.** Recommended: yes.
6. **A comment directly above a removed key:** leave it (recommended) or remove
   it with the key?
7. **New keys at the end of the frontmatter** (recommended), or in schema order?
8. **`_schema_version`, alias renames and the `body:`-key cleanup become
   explicit commands.** Recommended: yes.
9. **`to_markdown()` on a loaded entry:** remove from the public surface, or
   define it as "the file with pending changes applied"?
10. **Order against #569:** land #569 in 0.25.8 as groomed and replace its
    internals in step 3, or hold #569 for this? Recommended: land #569 first.
