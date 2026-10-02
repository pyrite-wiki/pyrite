---
id: adr-0042
type: adr
title: "A write changes what was asked and nothing else: entries are edited by operation, read from the file"
adr_number: 42
status: proposed
date: 2026-10-02
tags: [architecture, storage, write-path, identity, hooks, concurrency, invariants]
links:
- target: adr-0001
  relation: related
  kb: pyrite
- target: adr-0029
  relation: related
  kb: pyrite
- target: adr-0038
  relation: amends
  kb: pyrite
- target: adr-0039
  relation: related
  kb: pyrite
- target: adr-0040
  relation: related
  kb: pyrite
- target: adr-0041
  relation: related
  kb: pyrite
- target: adr-0043
  relation: related
  kb: pyrite
- target: adr-0044
  relation: related
  kb: pyrite
- target: adr-0045
  relation: related
  kb: pyrite
---

# ADR-0042: A write changes what was asked and nothing else

> **Proposed** (2026-10-02). The maintainer accepts or rejects it. The rule
> below (decisions 1 to 13) is the maintainer's, stated 2026-10-02. **Its
> acceptance waits on a second spike, on the real write path** (section "What
> is not yet measured"). The first spike measured a text splice, not this
> rule, and an adversarial read of the first draft found holes that the
> measurements could not have shown. Measurements are from `origin/dev` at
> `d35eae77`, ruamel.yaml 0.19.1; line references are at that commit.

## Context

ADR-0041 says a KB is files any tool can use and Pyrite is additive to
someone who maintains them by hand. This ADR applies that to how an entry
file is written and read, how an entry is identified when its file has no
`id:`, and what a hook may do.

Every interface (CLI flags, MCP and REST JSON, the web form) hands Pyrite
plain values. Today Pyrite parses the file into a model and, on save, emits
the whole file again, with records to remember what the file looked like
(`_absent_default_keys`, `_unrepresented_keys`, `_unparsed_timestamp_keys`,
`_source_frontmatter`, `_restyle_like_source` in `pyrite/models/base.py`),
and an echo check against the index row (`KBService.split_echoed_update`).
#46, #151, #173, #557, #561, #568 and #569 are each one more rule in that
scheme.

### Measured: what today's save does to a file nobody asked to change

Load and save with no change, over 28,123 real entry files from the
maintainer's KBs. These rows measure today's code and ruamel itself; they
are not a prototype's self-graded results.

| | Byte-identical | Changed |
|---|---|---|
| Today's code | 20,917 (74.4%) | 7,206 (25.6%) |
| ruamel round trip of the frontmatter alone | 26,588 (94.5%) | 1,535 (5.5%) |

Today's 7,206 include: `id:` added to 321 files that had none; `title: ''`
added to 125; `participants:` renamed `actors:` in 191; comment lines removed
in 88; the end of file or the blank lines after the fence rewritten in about
5,200. On hand-made files: CRLF becomes LF, a BOM is dropped, `flag: no`
becomes `flag: "no"`, `null` becomes empty, a multi-line scalar is
re-wrapped, a zero-indent list is re-indented. A Hugo post gains `id:` the
first time an agent sends its own read back. No value of any key changed in
any file. The 5.5% is the floor for any design that parses and re-emits.

### What the first draft measured, and what it did not

The first draft proposed "work out what changed by comparing the model before
and after, then splice only those keys' text". The splice was built and run
(candidates B and C below). Read honestly, its table says less than it
appeared to:

- **"100% identical over 28,123 files" measures the splice fed the file's own
  values.** The echo was built from the file's own parsed value and compared
  by the same bridge, so it is close to a tautology. It never passed through
  the model, hooks, a `kb.yaml` schema or a plugin type.
- **"Wrong output: none" was graded by the candidate's own equality**, not by
  an independent oracle.
- **The corpus was files Pyrite and its agents wrote:** zero CRLF, zero BOM,
  zero anchors, no Hugo site. Those shapes were tried on 26 hand-made files
  only.
- **The real path was run on seven examples** in one generic KB with no
  hooks and no `kb.yaml`.

### What the adversarial read found

Probes against the prototype (script `probe_review.py`, spike scratch):

- **Writing the model's after-value deletes what the model does not hold.** A
  hand-written link `{to: foo, type: related, since: 2020}` with a trailing
  comment, plus one appended link (as a `before_save` hook does), was
  rewritten with `since:` and the comment gone and the legacy keys respelled;
  the check passed, because it compared against the same lossy value.
  Appending a source deleted `verified: false`, `verified_date:` and
  `outlet: ""` from the untouched first item. Cause: `Link.from_dict` keeps
  four keys and `Source.to_dict` drops defaults
  (`pyrite/schema/provenance.py:11-140`). Today's code has the same defect
  (the "wrong 100" in the table below).
- **Hooks write.** A title edit on a hand-written cascade `timeline_event`
  adds `links:`, and which links depends on what the index holds at that
  moment (`extensions/cascade/src/pyrite_cascade/hooks.py:102-149`). A no-op
  echo in such a KB is not a no-op.
- **Single-entry reads come from the index** (`KBService.get_entry` returns
  `self.db.get_entry`, `kb_service.py:554`). A content-hash rule over an index
  read either loops (the stale row returns the same hash) or lies (the hash
  of the file with the fields of the row, so an echo reverts a hand edit).
- **No lock and no compare-and-replace** (no `flock` or `fcntl` in
  `pyrite/`). Two writers on different keys, a `git checkout` between read
  and replace, and a templated-folder move each lose a change.
- **Undeclared keys on typed entries land under `metadata:`**
  (`kb_service.py:1290-1293`), 1,265 of 2,813 sampled files.
- **Load-time migrations run again on every load** once a save stops stamping
  `_schema_version` (`storage/repository.py:146-156`, `:505-508`).
- **The read is not the file:** the loader drops a first body line that looks
  like `key:` (`repository.py:80-91`; #636). Any body edit made from such a
  read deletes a line the caller never saw.
- **Identity of an id-less file was decided three incompatible ways**
  (title, in code at `models/base.py:505-510`; filename, ADR-0038 question
  5; and "open" in the first draft). Every file with no `id:` and no title
  gets `entry-da39a3ee`.
- Smaller: an echo from a client that sorts JSON object keys rewrote nested
  maps; replacing `title: 'it''s the #1 hit'` produced `title: 'New title'
  #1 hit'` (the old value's tail became a comment); a hook may return a new
  object and discard the loaded reading (`hook_runner.py:106-108`); rename
  rewrites only `[[old]]` by regex, respells CRLF to LF and is not atomic
  (`repository.py:599-638`).

## Decision

### 1. A write is an operation applied to the file's own value

A write to an existing entry names an operation and a path in the file's
frontmatter (or the body). The operations are:

- **set** a field to a value (a scalar or a whole value);
- **append** an item to a list, or **remove** one item from it;
- **set** a nested key (`links[2].relation`, `metadata.source_id`);
- **unset** a key (remove it);
- **replace the body**;
- **replace the whole document** (rare, explicit; decision 11).

An operation changes the file's own text at the span of the value it names.
It never carries bytes for an existing file. An appended item is emitted in
the style of its neighbours and every existing item's bytes stay as they are.
A set of one nested key leaves its siblings' bytes alone.

### 2. The model validates the result; it never supplies bytes

The write pipeline for an existing entry is: read the file under its lock
(decision 10); apply the operation to the file's parsed document and splice
the text; parse the result; check that every path outside the operation parses
to what it was and the operation's path parses to what was asked; validate the
result through the type (kb.yaml schema, validators, hooks, decisions 4 and 8);
publish atomically. If a narrow edit fails the check it widens to the whole
top-level key, still as an operation on the file's own value. If that fails,
the write is refused. **There is no fallback that re-emits the file or writes
the model's rendering of a key the operation touched.**

The role of the type is the one ADR-0014 gives it: structure for the data
and, through the protocols it satisfies, an extensible behavioural interface
(ADR-0045). Here the type and its protocols **validate and project**: they
check the result of an operation, and they supply what the index derives from
the file. They do not serialise an existing file. Types and protocols stay;
per-class serialisation stops deciding what is in a file.

### 3. "Asked" is what the operation says

There is no model diff and no comparison of a request with the index row.

- A **set** whose value equals the file's parsed value writes nothing and is
  reported `unchanged`. Equality: a string equals a YAML date or timestamp
  when it is that value's ISO form or the same instant; int and float compare
  by value; a bool never equals an int; mappings compare unordered (JSON
  objects are unordered); a list compares in order.
- Equality is against the file's value, so setting `importance: 5` over
  `importance: high`, or over no line, is a change and is written. A set is a
  request. #569's `defaulted` and `normalized` lists have nothing to decide.
- **Absent means not mentioned.** A key an operation does not name is never
  touched. Removal is `unset`, not `null` and not omission.

### 4. Hooks do not write

A `before_save` hook may refuse a write (raise). It may not change one: the
return value is ignored, and a hook that needs to change an entry cannot.
`after_save` and `after_delete` hooks may keep their own state (caches, their
own tables) and never write an entry file.

Values that hooks add today are derived data. They are provided another way:
the index derives them from the file at index time, from fields the type's
schema declares as references (ADR-0045), stores them as index rows marked
derived, and reads return them beside the file's own values, **labelled as
derived** (under their own key, never among the file's fields). They are
never written into the user's file, and they are never accepted back as
fields: an operation that names a derived key, or a whole-document replace that
carries one, is refused with a message saying it is derived. This is the
protocol model of ADR-0045: a data contract, derived information and
operations, explicit operations, and refusals.

The hooks that exist today, and two operations of the same shape (checked:
only these define `get_hooks`, plus two core hooks registered in
`task_service.py:1057-1058`):

| Hook | Where | Today | Becomes |
|---|---|---|---|
| `resolve_actor_links` (`before_save`) | cascade | adds `actor_reference` links to `timeline_event`, `solidarity_event` and `scene` from `actors`, resolved against the index | An index-time derivation from `actors`. cascade is deleted (ADR-0040 decision 2); the derivation moves to journalism-investigation if still wanted |
| `enrich_connection_links` (`before_save`) | journalism-investigation | adds links from connection fields (`CONNECTION_LINK_SPECS`) | An index-time derivation from the same field list |
| `before_save_author_check` (`before_save`) | social | refuses a non-author update (stays); on create sets `author_id` to the acting user (a write) | The refusal stays. "The acting principal" as a create-time default for `author_id` is declared in the type's schema and applied by create, the one place Pyrite writes whole files (ADR-0045) |
| `_task_validate_transition` (`before_save`) | core | refuses an invalid status move (the `workflow` primitive of ADR-0014) | Unchanged: it only refuses |
| `_parent_rollup` (`after_save`) | core (`task_service.py:1016`; `rollup_parent` at :594) | registered for every KB; decides from the index when every child of a parent is resolved, writes the parent's `status: done`, cascades to the grandparent, and swallows failures as warnings (the `rollup` primitive of ADR-0014) | **Derived at read time** (maintainer, 2026-10-02). A parent's completion is computed from its children wherever it is shown, as `sw_epics` already computes epic progress at query time. Nothing writes the parent's file as a side effect; the parent's `status` means what a person or an explicit operation set (ADR-0045 decision 7) |
| `unblock_dependents` (not a hook) | core, `task_service.py:643` | when called, moves blocked tasks whose dependencies are all resolved to `in_progress` (writes other entries). No caller in `pyrite/` or `extensions/`, only tests | **Derived at read time** (decided the same way): blocked or ready is computed from the dependencies |
| `aggregate_evidence_to_parent` (not a hook) | core, `task_service.py:684` | copies a child's evidence into the parent's `evidence` (writes the parent). No caller outside tests | **Derived at read time**: subtree evidence is computed from the children |
| `_on_actor_saved`, `after_save_update_counts`, `after_delete_adjust_reputation` | cascade, social | invalidate a cache; write the plugin's own tables | Unchanged: they write no entry file |

**Migration note.** Existing KBs already hold links that hooks wrote. They are
now the user's text and stay. A derived link equal to a file link (same target
and relation) is one link. `qa` lists the file links that a derivation would
now produce, so an operator may remove them by choice. Nothing is removed by
Pyrite.

### 5. Identity of an entry with no `id:`

**With no `id:` key, an entry's identity is its path relative to the KB root.
`id:` is an optional pin that survives a move. Identity is never derived from
the title.** Pyrite writes an `id:` only on files it creates, or when asked.

This amends ADR-0038 (below). The rest follows:

- A hand edit of a title changes no identity. Two files cannot derive the same
  id (paths are unique), which removes the `entry-da39a3ee` collision and the
  Hugo cases (`_index.md`, leaf-bundle `index.md`, `blog/intro.md` against
  `docs/intro.md`). An explicit pin that equals another file's path-derived id
  is a duplicate, handled as ADR-0038 decided (the lexicographically first
  path wins; reported loudly).
- **A move of an id-less file changes its identity.** Links to the old id
  dangle and are reported by `qa` and `index health`. An `id:` pin prevents it.
- **Types whose folder is templated by a field** (`backlog/{status}`) need the
  pin, because a change of that field moves the file. Pyrite creates those
  files and pins them. An update of a templated-folder field on a file with no
  pin sets the field, does not move the file, and reports "not moved: no id
  pin" with the command that pins it.
- **Existing links that point at title-derived ids.** Switching rules changes
  the id of every id-less file whose title-derived id differs from its path.
  Upgrade runs a one-time report: each such file, its old and new id, and
  every entry or database row that referred to the old one (links, stars,
  reviews, `entry_version` rows). A command applies the rewrite on request:
  links in files by decision 12; database rows by re-keying. Nothing is
  rewritten silently. Old ids do not keep resolving (**question 5**).
- `rename` of an entry that has an `id:` changes the pin; of one that has none
  is a move of the file (the path is the identity). Both rewrite inbound links
  by decision 12.

### 6. Spelling of what Pyrite writes

- A new key goes on the last line of the frontmatter, block style.
- A replaced scalar keeps its quote style and trailing comment, located from
  the YAML parser's spans (never by pattern: the `'it''s'` defect above). A
  replaced list keeps flow or block style. A new list item copies its
  neighbours' indentation and quoting. New lines use the file's line ending.
- A string a YAML 1.1 reader would misread is quoted when Pyrite emits it.
  Untouched bare `yes`/`no` stay as written (narrows #568 to bytes Pyrite
  emits).
- **A key is written where the operation names it.** Update never moves a key
  into `metadata:`. A path `metadata.x` is a nested key like any other. Where
  *create* places an undeclared key stays as today until #178 and #447 are
  decided.
- **An alias resolves to the key the file uses.** A set of `actors` on a file
  that carries `participants:` writes `participants:`. A file carrying both is
  refused and reported.
- A key that defines or uses a YAML anchor is refused for change, with the
  reason. TOML and JSON frontmatter are refused untouched (as today).

### 7. Bookkeeping never rides alone; repairs are commands

- `updated_at` is written only where the file already has the key and only
  when the write changed something. A no-op writes nothing (**question 1**).
- Adding `id:`, renaming an alias key, stamping `_schema_version`, dropping a
  stray `body:` key and re-quoting existing values are explicit commands
  (`qa fix`, `schema migrate`), never side effects of an update.
- Migrations are reading rules, never writes, and are idempotent. Files behind
  their type's version are reported, not silently migrated on save.

### 8. Validation: refuse what the write causes, report what it does not

A write is refused for a violation in a field it touches or that its result
causes (a cross-field rule). A violation that exists before and after in a
field the write did not touch is returned as a warning with the write's
result. This generalises the existing downgrade for off-list enum values on
unchanged fields (`kb_service.py:278`, `:340-343`): a hand-written file with a
missing required field can still be edited elsewhere, and the response says
what is wrong.

`qa validate` and `index health` report what the repairs of decision 7
address: files with no `id:`, derived-id collisions, files behind their schema
version, alias keys, both alias and canonical key present, links to retired
derived ids, and files Pyrite would refuse to change (anchors, TOML).

### 9. A single-entry read reads the file

`get` reads the file's current bytes and returns its frontmatter as parsed,
the body exactly as written (no dropped first line; #636), and a
`content_hash` (SHA-256 of the file's bytes, the definition the index already
uses, `storage/index.py:73-82`). It does not read the index row. Lists,
search, graph and counts remain index reads and may lag the file; they carry
`indexed_at` and no token.

### 10. A lock and a compare-and-replace guard every write

- A per-file lock guards read-apply-replace across processes (server, CLI and
  stdio MCP share the files). The lock lives in Pyrite's data directory,
  outside the KB (decision 5 of ADR-0041). A write that moves a file takes the
  locks of both paths in path order.
- Under the lock, the file's bytes are re-checked immediately before the
  atomic replace; if they differ from what was read (a `git checkout`, an
  editor), the operation is retried against the new bytes a bounded number of
  times, then refused. A small window between that check and the rename
  remains for a writer that does not take Pyrite's lock; it is stated, not
  claimed away.
- The replace uses `pyrite/utils/atomic_write.py`, which follows symlinks.
- The index row is built from the bytes written, with their hash, never from
  an in-memory entry.
- **The claim guard** (`claim_entry`, `kb_service.py:2357-2392`) compares
  `status` against the file's value under the lock, and updates the row after.
  The row is never the tiebreaker. This amends ADR-0029 section 4 (**question
  3**).

### 11. The token: field operations need none

An operation on a field carries no token. Only **replace the whole document**
carries the caller's `content_hash`, and is refused with a conflict that says
"re-read" when the file's hash differs. On a match, the request is compared
with the file's value (decision 3's equality, applied to the file's parsed
values), and only differing keys are written, as set and unset operations.
Every write returns the new `content_hash` and a report: what was written,
what was `unchanged`, what was not moved, warnings.

A whole-document echo of a read is therefore either exact (nothing written) or
refused as stale. The web editor sends operations for the fields the user
changed, not the document. Whether a body replace or a set should also accept
an optional guard against a stale form is **question 2**.

### 12. Every write of an entry file goes through this path

Rename's link rewrite (operations on other files' `links:` and wikilinks,
preserving line endings, under their locks), schema migrate, `qa fix`, the
claim and task services, and plugins' commands. `software-kb`'s
`cli.py:704-714` and cascade's `migration.py` write entry files with
`write_text` and regex today; they are converted (cascade is deleted). A
plugin writes entry files only through `ctx.kb_service` (ADR-0040), and so is
bound by decisions 1 to 11. ADR-0041 and this ADR say the same thing.

### 13. Create is the one place Pyrite chooses spelling

A new file is emitted whole, as #86 decided, with an `id:` pin (decision 5).
File-based creates (`pyrite add`, import) copy the author's bytes and add
nothing.

## What the first spike measured

Each candidate was built and run on the same inputs: the corpus above for the
no-op, 2,813 real files and 26 hand-made shapes for ten operations. These
tables measure **the splice on the file's own values**, with the candidate's
own equality. They support decision 2's mechanism, not decisions 1 to 4.

| | No-op, 28,123 files | One scalar, 2,813 files | One item in a list of maps, 1,165 files | Wrong output (self-graded) |
|---|---|---|---|---|
| Today: model, whole-file emit, restyle | 74.4% identical | 74.5% minimal | 17.7% minimal; 8.6% wrong | yes (measured) |
| A: ruamel round-trip document, mutate, dump | 94.5% identical | 94.8% minimal | 88.7% minimal | none seen |
| B: splice at top-level key granularity | 100% identical | 100% minimal | 95.9% minimal | none seen |
| C: B plus token, list-append and nested-leaf edits, falling back to B | 100% identical | 100% minimal | 100% minimal | none seen (the `'it''s'` and link-loss probes above show it is not none) |

Splice cost on 896 files of Pyrite's own `kb/` (the adversarial read): load
and emit 1.28 ms per file today; the splice with its verify parse adds 1.44 ms
on top of the model load. The first spike's "+1%" measured load, not write.

The throwaway wiring of C into `Entry.save` passed 7,625 of 7,647 backend
tests and 1,044 of 1,044 extension tests with the load-time records emptied;
22 tests pin today's behaviour (13 pin defects or normalisation that goes away,
4 read a load-time record, 3 call `to_markdown()` on a loaded entry, 1 pins the
`body:` cleanup, 1 pins #568's requote-on-touch). That the existing suite
stayed green shows it does not pin the new behaviour; it is not evidence for
it. The prototype is not saved, and the figure "about 330 of 969 lines of
`models/base.py` go" is the first spike's, not verified by the read.

## What is not yet measured: spike 2

**Question.** On the real write path, does applying operations to the file's
own value, with the model validating the result, hold three properties on
files Pyrite did not write: (1) a one-field operation changes exactly that
field's lines; (2) a no-op operation changes no byte and says so; (3) no key
outside the operation parses to a different value, and no bytes of an
untouched key are emitted by a serializer?

It must be run on, and report separately, each of the things the first spike
did not touch:

1. **Typed KBs** with `kb.yaml` schemas, `file_pattern` and templated folders.
2. **Hooks**: the table in decision 4, with `before_save` hooks refusing and
   not writing, and the derivations replacing them.
3. **Plugin types**: all 37 extension entry classes, not only core types.
4. **Nested values**: lists of maps with unknown sub-keys, comments inside
   items, `Link` and `Source` shapes (the P1 probes).
5. **Migrations**: a type at an old `version`, a rename migration, a set of a
   migrated field; the "behind schema" report.
6. **Undeclared keys and the `metadata:` block** (#178, #447), and aliases
   with both keys present.
7. **Shapes the corpus lacks**: CRLF, BOM, symlinks, anchors, duplicate keys,
   a real Hugo site, a file at the KB root.
8. **Concurrency**: several processes writing different keys of one file; a
   checkout between read and replace; two writers racing a templated move; the
   claim race (`tests/test_task_claim_concurrency.py` shape).
9. **Reads**: the cost of a file read per `get`; migrations run per load.

It must grade output with an **independent oracle**: the expected text built
without the candidate's equality (a second YAML parser; a naive line editor
for the targeted lines), not computed from the value being checked. Its
corpus must include files nobody at Pyrite wrote. Its pass condition is zero
unexplained diff lines over every shape above. Its deliverable is a findings
file, the doc passage below running as a test, and either "accept" or the
list of decisions that need changing.

## Acceptance

Doc-driven. The passage is `docs/how-pyrite-edits-your-files.md`;
`tests/test_doc_write_as_patch.py` runs every example in it. A `file` block is
written into a KB; the `update` block is sent through `kb_update`; the file's
diff must equal the `diff` block exactly (an empty `diff` is byte-identical).
Flags: `crlf` writes CRLF; `emitter` asserts that during the update the YAML
emitter is never handed a top-level key of the file that the operation did
not name; `echo` sends a `kb_get` result back as the update. The spelling of
the operations is illustrative: the interfaces are alpha (#303). On today's
code 6 of the first spike's 7 examples fail.

**One field edit yields that field's lines.**

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
{"set": {"title": "Field notes, second draft"}}
```
```diff
-title: Field notes   # working title
+title: Field notes, second draft   # working title
```

**Appending to a list leaves the existing items alone** (the case the first
draft got wrong).

```file
---
id: legacy-links
title: Legacy links
type: note
links:
- to: foo          # legacy spelling, hand written
  type: related
  since: 2020
---

Text.
```
```update
{"append": {"links": {"target": "bar", "relation": "related"}}}
```
```diff
+- target: bar
+  relation: related
```

**A save does not add what a hook used to add.** A `timeline_event` in a KB
with the journalism derivations installed.

```file
---
id: hearing
title: The hearing
type: timeline_event
actors: ["[[jane-doe]]"]
---

Text.
```
```update
{"set": {"title": "The hearing, postponed"}}
```
```diff
-title: The hearing
+title: The hearing, postponed
```

**Two writers on different keys both survive** (`concurrent`: two processes
start together from one file).

```file concurrent
---
id: shared
title: Shared
type: note
summary: one
importance: 3
---

Text.
```
```update
[{"set": {"summary": "two"}}, {"set": {"importance": 5}}]
```
```diff
-summary: one
+summary: two
-importance: 3
+importance: 5
```

**Sending a read back changes nothing** (CRLF file).

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

**A stale whole-document replace is refused** (`stale`: the file is edited by
hand after the read; the replace carries the hash of the read).

```file stale
---
id: stale-me
title: Stale me
type: note
status: draft
---

Text.
```
```update stale
{"replace": {"title": "Stale me, renamed", "status": "draft"}, "content_hash": "<hash at read>"}
```
```diff
```

The refusal says the file changed and to re-read; the hand edit is intact.

**An id-less file keeps its identity when its title changes.**

```file noid
---
title: Intro
---

Text.
```
```update
{"set": {"title": "Introduction"}}
```
```diff
-title: Intro
+title: Introduction
```

`get posts/intro` still returns it, and no `id:` was added.

Further examples: a Hugo post gains nothing on an unrelated edit; the same
value in another spelling is not a change; a new key goes at the end; a
body edit leaves oddly spaced frontmatter alone; `unset` removes a key and
leaves a comment above it; an alias key is edited in place; an anchor is
refused with a reason; a templated-folder field on a file with no pin sets the
field and reports "not moved".

## Amends

- **ADR-0038, question 5** ("Derived id when a file has no `id:`. DECIDED
  (maintainer, 2026-09-26): from the filename, not the title") is amended to
  "from the path relative to the KB root, never from the title". The
  amendment is written into ADR-0038 as a proposed amendment dated 2026-10-02.
- **ADR-0038, section 1**, "the id generated from the title when there is
  none", is the sentence the 2026-09-26 decision had already replaced. It
  reads "the id derived from the path" once this is accepted.
- **ADR-0038, section 2**, row Update: "The path changes only for a templated
  subdirectory, and then only the folder" gains "and only for a file with an
  `id:` pin".
- **ADR-0029, section 4**: "the claim CAS is unchanged -- it remains the one
  concurrency guard" is amended by decision 10's last bullet, if question 3
  is answered yes.
- **ADR-0040**: see ADR-0045. The hook contract (decision 4), the meaning of
  `to_frontmatter` and the round-trip clause of the conformance kit, and
  `FRONTMATTER_ALIASES` change. ADR-0014 and ADR-0017 are not amended here.

## Consequences

**Easier**
- A hand-edited file keeps its comments, quoting, line endings, BOM and
  layout. The diff of a Pyrite write is the change.
- A ruamel upgrade cannot respell a KB: untouched keys never pass through the
  emitter.
- One rule decides "was this asked", in one place, against the file.
- Concurrent writers and checkouts are handled by one lock and one check.

**Deleted**
- The load-time records and the restyle code in `pyrite/models/base.py`;
  `split_echoed_update`'s comparison with the index row; #569's `defaulted`
  and `normalized` lists; the `_schema_version` stamp and `touch_updated_at`
  on save (`repository.py:505-511`); the keep-the-subdirectory code in
  `storage/document_manager.py:76-88` apart from the templated move;
  `Entry.save`'s own temp-file code (`base.py:820-841`), which becomes
  `atomic_write`; the hooks' writes (decision 4).

**Harder**
- Two paths produce file text: create (emit) and operations (splice).
- The splice depends on ruamel's line and column data; the check after every
  write is what makes that safe.
- Files stop being normalised as a side effect. A KB that wants uniform style
  runs a formatter on purpose.
- Derived links are a second kind of link row. Reads return file links and
  derived links separately.
- A single-entry read costs a file read and parse (about 1 ms in the review's
  measurement) where it was a row lookup.
- Upgrade changes the id of every id-less file whose title-derived id differs
  from its path, with a one-time report and rewrite commands (decision 5).
- 22 tests change (above).
- **Plugin contract (ADR-0040).** Not "no signature changes", as the first
  draft said. See ADR-0045: `to_frontmatter` stops governing bytes, hooks
  refuse and do not return an entry, aliases are declared by the schema, and
  ADR-0040's round-trip clause needs replacing.
- **Task flows that relied on auto-complete.** `task decompose` parents and the
  conductors' decomposition read the derived completion, or set a parent's
  status explicitly. Tests of `_parent_rollup` and `rollup_parent` change.
  `TaskService.update_task` already reads the file and appends to
  `status_change_log` by writing the whole list; it becomes an `append`.
- **Docs to correct when this lands:** `docs/tutorials/plugin-writing.md`
  tells plugin authors that `from_frontmatter()` "must call
  `generate_entry_id(title)` when the metadata has no `id`" and that
  `to_frontmatter()` omits defaults. The first is decision 5 reversed.

**Phasing** (each one reviewable PR; none before spike 2 reports and the
maintainer accepts):

1. Spike 2 (decision to accept).
2. The doc and its runner, as expected failures.
3. The operation function (pure), not wired.
4. Wire the update path: lock, check, no-op writes nothing.
5. Reads from the file; `content_hash`; the new hash and report on writes.
6. Hooks stop writing; derivations in the index.
7. Identity from the path; the one-time report and rewrite.
8. Delete the load-time records; the other writers; explicit repairs.
9. Interfaces: `unset`, operations on CLI, MCP and REST, the web editor.

Tickets: #569 lands in 0.25.8 as groomed and its internals are replaced at
step 4 (maintainer's release line); #636 (a save that deletes a body line)
ships in 0.25.7 and is the same defect as the unfaithful read; #628 is
untouched; #86 is untouched in its decision; #568 narrows (decision 6); #488
is consistent with ADR-0038's sticky location; #178, #447, #637, #638, #640
are in this ADR's scope and spike 2's list.

## Alternatives considered

- **Model diff, then splice the model's after-value** (the first draft).
  Rejected: it deletes data inside a touched key and counts hooks as asked.
- **A, ruamel round trip.** Rejected: 5.5% of untouched real files change.
- **B alone.** Workable. Rejected: lists of maps re-emit whole and a changed
  scalar loses its trailing comment.
- **Keep today's design and add records.** Rejected: 25.6% of untouched files
  change after seven issues' worth of rules.
- **Diff the incoming document against the file as it is now, with no base.**
  Rejected: a stale whole-document echo reverts hand edits (reproduced).
- **Three-way merge per key with a base from git.** Deferred. It works when
  the caller's read is the base, but an uncommitted hand edit has no base in
  git. Refusing is simple and correct.
- **Identity from the title** (today's code). Rejected: a hand edit of a title
  orphans every link, star and review, and every untitled file collides.
- **Identity from the filename alone** (ADR-0038's 2026-09-26 decision).
  Refined to the path: `_index.md`, `index.md` and same-named files in
  different folders would otherwise share an id.
- **TOML and JSON frontmatter.** Out of scope.

## Questions for the maintainer

Ranked by what they block.

1. **`updated_at`.** Keep stamping it where the file already has the key and
   something was written, never stamp it, or stamp only on request?
   *Recommended: keep, as stated.* A write "does what was asked"; a stamp is
   not asked for. Blocks the doc's examples (step 2).
2. **A guard on a field set or body replace.** Field operations need no
   token. A web form held open for ten minutes then replaces a body that a
   hand edit changed. Offer an optional `expect` (the hash of the value or
   body when read) on set and replace-body, which the web editor and agents
   may send? *Recommended: yes, optional.* Blocks the web editor (step 9).
3. **The claim guard against the file** (decision 10; amends ADR-0029 section
   4). *Recommended: yes.* The row is the tiebreaker today, which decision 5
   of ADR-0041 forbids. Blocks nothing before step 4.
4. **Path identity spelling.** The id is the path without `.md`, with `/`
   separators (`posts/intro`), or the filename stem with a folder prefix, or
   the path with `.md` kept? *Recommended: without `.md`, with `/`.* Routes
   and wikilinks need to accept `/` in an id. Blocks step 7.
5. **Do old title-derived ids keep resolving** for a release after upgrade
   (a deprecation window), or only the one-time report and rewrite?
   *Recommended: report and rewrite only.* A window would keep two
   identities for one file, which is the defect. Blocks step 7.

Defaults taken, change them if you disagree: new keys go at the end of the
frontmatter; a comment directly above a removed key stays; untouched bare
`yes`/`no` stay as written; `to_markdown()` on a loaded entry leaves the
public surface; `unset` is added to the CLI, MCP and REST as one named
operation; the `body:` cleanup, alias renames and `_schema_version` stamping
are explicit commands.
