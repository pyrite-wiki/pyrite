---
id: adr-0051
title: 'metadata is not a field: metadata.k is a second spelling of field k, one level only, one spelling per file'
type: adr
importance: 5
adr_number: 51
status: accepted
date: '2026-10-17'
links:
- target: adr-0042
  relation: amends
  kb: pyrite
---

# ADR-0051: metadata is not a field: metadata.k is a second spelling of field k, one level only, one spelling per file

> **Accepted** by the maintainer, 2026-10-19. It amends ADR-0042 decision 6;
> it is the directed fix of Andon #994 (decision A, chosen by the maintainer on
> #994), landed on `arch/2026-10` with #973 and #1001 (261e1d5a).

## Context

ADR-0042 decision 6 says "A path `metadata.x` is a nested key like any
other." Nothing in the code said which *field* a frontmatter path denotes,
and `metadata` carried four meanings at once:

- **The file's bag.** `metadata:` is read into `entry.metadata`
  (`Entry._base_kwargs`); a typed entry's undeclared keys land there too,
  and `create` writes a typed entry's undeclared kwargs under `metadata:`
  (#178).
- **A second home for a declared field.** The reader lifted five keys
  (`rank`, `effort`, `kind`, `status_reason`, `parked_awaiting`) from the
  bag into the row, and the index row's `metadata` JSON was built with the
  bag merged *last*, so a file holding `evidence: [real]` and
  `metadata: {evidence: [fake]}` read `[real]` in the model and `[fake]` in
  the row. The reader disagreed with itself.
- **A request envelope.** REST PUT, MCP `kb_update` and the web form send
  undeclared fields as `metadata: {k: v}`; the CLI as `-f metadata.k=v`.
- **A path prefix.** The resolver stripped one `metadata.` and treated the
  rest as a file location, so `metadata.metadata.evidence` named a nested
  key the managed-field refusal never saw, and wrote a task's evidence on
  every surface (#973 fix round 1, the third round in which the invariant
  failed; hence the Andon cord).

The resolution rounds kept adding a check per path form. A hand list of
path forms (`PATH_FORMS` in the surface test) is what let the fourth form
through.

## Decision

**`metadata` is not a field.** `metadata.k`, in a file or a request, is a
second spelling of field `k`, one level only, the same as an alias. A field
has one spelling per file. A file that spells a field twice is reported,
and an update of that field is refused. A path whose field would be
`metadata` is refused, at any depth.

This amends ADR-0042 decision 6, bullet "A key is written where the
operation names it": the sentence "A path `metadata.x` is a nested key like
any other" is replaced by the paragraph above. The rest of the bullet
stands: update never moves a key, and where *create* places an undeclared
key stays as today (#178, #447) -- that placement is a spelling choice, and
the new file spells `k` once.

**One enforcement point**, in `pyrite/schema/field_aliases.py`:

- `field_at(cls, entry_type, path) -> (field, rest)`: the field a path
  denotes. The first key, an alias folded to its target
  (`class_field_aliases`), one leading `metadata.` folded away. `metadata`
  alone, `metadata.metadata`, `metadata.metadata.k` raise
  `NotAFieldError`; the rest of the path (`[0].x`) is returned unchanged.
- `spellings_of(cls, entry_type, field)`: the spellings a file may use --
  `k`, each alias, and each of those under `metadata:`.
- `spelling_in(file_values, cls, entry_type, field) -> path`: the one path
  the file spells the field at; the field's own top-level name when the
  file has none; `SpelledTwiceError` when it has two.
- `double_spellings(file_values, cls, entry_type)`: every field the file
  spells twice, with its paths.

Every refusal, on every surface (service, MCP, REST PATCH and PUT, CLI) and
for every operation kind (set, append, remove, unset, add-subkey), is
decided on `field_at(output path)`; the operation is then written at
`spelling_in(file, field)`. The one-level strip, `_spelling_for` and
`PATH_FORMS` are removed.

**A path is parsed once** (#1001 round 3). Naming the field was half the
model; the other half is what a path *is*. A request path is parsed into
segments once, at each surface's boundary, by the one parser in
`pyrite/utils/field_path.py`, and that tuple is both what `field_at`
decides on and what `apply()` writes. Nothing between the surface and
`apply()` joins a path back into a string or parses it again, and a key
that still holds `.` or `[` inside one segment is parsed, never passed
through. Round 2 showed why: a dotted key inside the `metadata` envelope
(`{"metadata": {"status_change_log[0]": ...}}`) was one literal segment to
the refusal and a list index to the writer, so the refusal decided on one
path while `apply()` wrote another.

**The reader agrees with itself.** The model's reading wins everywhere:
in the index row (`_entry_to_dict` merges the bag first, the model's
frontmatter over it) and in the model's own bag for an undeclared key
(`TaskEntry` and `GenericEntry` read the top-level spelling over the nested
one, as a declared field already did). The double spelling is reported:
`index health` lists `double_spellings` (`{kb, id, field, spellings}`,
status `warning`) and `qa validate` one `field_spelled_twice` issue per
field.

**The test is generated from the code**, not a hand list: entry classes ×
managed fields × the spellings `spellings_of` generates, each wrapped in
`metadata.` to depth 3 × operation classes × surfaces
(`tests/test_metadata_is_a_spelling_not_a_field.py`). The oracle checks
the effect: the request is refused, or every managed field reads back
unchanged in both the model and the index row.

## Consequences

- A fifth path form cannot slip past a refusal: there is no list of forms,
  only `field_at`.
- A file carrying a key both at the top level and under `metadata:` is
  reported, and its other fields are still editable; an update of that
  field is refused until one spelling goes. Before this, the file was
  silently read two ways.
- The reader lift of the five bag keys is `spelling_in` applied at read
  time; its rule is unchanged, and it can no longer disagree with the row.
- `metadata` as a request envelope (REST PUT, MCP, the web form) stays:
  each nested key is a path whose field `field_at` answers. A client that
  sends `metadata: {metadata: {...}}` is refused.
- On create, a declared field or an alias sent inside `metadata` lands at
  its own key (`build_entry`); only an undeclared key keeps the nested
  placement (#178). Before, create nested the caller's value and emitted
  the field's default at the top level: Pyrite itself wrote files spelling
  `source_type` twice, and the row's bag-wins merge hid it. The repo's own
  KB holds such files (13 entries, 47 fields, nine of them disagreeing on
  `status`), reported by `index health` now.
- `aliases` had no column of its own: the row held it only in its
  `metadata` JSON, and only for a file that nested it under `metadata:`, so
  a top-level `aliases:` (the field's own spelling) never resolved a
  wikilink. It now has its own index column (decision 5 below, migration
  29), which the wikilink alias pass reads.
- A key a person wrote with a `.` or `[` in its name (`v1.2: x`) cannot be
  addressed by path; `docs/json-contracts.md` says so beside the spelling
  rule. Keys that only look alike (a leading space, a Cyrillic letter) are
  distinct keys and are written as such.
- Out of scope, on #990: the `sources` publish-before-index failure and
  `links[x]` INTERNAL.

## The maintainer's decisions on the open questions (2026-10-17, #1001)

1. **Two spellings in one request.** `refuse_conflicting_spellings`
   treats `k` and `metadata.k` (envelope or dotted) as one field under the
   #720 rule: different values are refused, equal values accepted.
2. **Equal values in a file** are still a double spelling: an update of
   the field is refused. `qa fix` (rule `field_spelled_twice`) merges an
   equal pair into the top-level spelling, losing nothing; a pair that
   disagrees stays a hand fix, listed by `index health` and `qa validate`.
   "Equal" means the same YAML value, kind included (`values_identical`):
   `true`, `1` and `1.0` are three values, while `x`, `'x'` and `"x"` are
   one. The merge removes only the nested key (and a `metadata:` block it
   empties), and it is granted only to `qa fix`'s own call, never to an
   update from a surface. A doubly spelled `type` is always a hand fix.
3. **Precedence:** for an undeclared key, the top-level spelling wins
   over the nested one.
4. **A save through the model loses nothing:** `rank: 1` +
   `metadata: {rank: 2}` keeps both values (`_shadowed_nested` on
   `GenericEntry` and `TaskEntry`).
5. **Read shape:** a read spells a field where the file does. `aliases`
   is its own index column (migration 29); a top-level `aliases:` never
   appears under a read's `metadata`, and the wikilink alias pass reads
   the column (and the bag, for a file that nests it).
6. **Create** refuses a managed field sent in the `metadata` envelope,
   as update does, on REST POST, MCP `kb_create` and the service, and
   applies decision 1's rule: one field named at two spellings with
   different values is refused and no file is written; equal values are
   written once. (A managed field sent at the top level of an MCP create is
   #1010.)
7. **The repo KB's doubly spelled entries** are fixed by hand in a
   separate `kb/` PR to `dev`.

## Amendment 2026-10-20: the single-entry read (proposed, #1025, PR #1039)

> *Proposed* by the #1025 fix round for the maintainer to accept; the
> accepted text above is unchanged. The maintainer's decisions of
> 2026-10-20 on PR #1039 (option B; undeclared keys not lifted;
> `parked_awaiting` declared) are what this amendment records.

The decision above landed on the writers first: every write went to
`spelling_in(file, field)`, while every class's `from_frontmatter` still
read top-level keys only, so a field spelled only under `metadata:` was
written where nothing read it (`update -f importance=8` then `get` showed
5; `pyrite link` refused a file spelling `links` nested, #975). The reader
holds the same invariant: **for every field, every reader takes its value
from `spelling_in(file, field)`, the path the writer writes.** A file
spelling a field only under `metadata:` reads exactly as if it spelled it at
the top level: in the model, the index row, the single-entry read (CLI, MCP,
REST), search and health.

- **One lift**, `read_view(file_values, cls, entry_type)` in
  `pyrite/schema/field_aliases.py`, called by the two loaders
  (`entry_from_frontmatter`, `Entry.from_markdown`) in front of
  `cls.from_frontmatter`: a declared field (`declared_fields`: the class's
  public dataclass fields that are file keys, less alias names, plus alias
  targets) that the file spells only nested is spelled at that name at the
  top of the view the class reads. `capture_extra_frontmatter` keeps the
  raw mapping. A structural test pins that every `from_frontmatter` call in
  production code sits behind one of these two (`build_entry`, a create
  from a request, is the third and folds its envelope itself).
- **Decision 5, reworded (option B):** *a single-entry read spells each
  field once, at its own name; its `metadata` holds only keys the type does
  not declare. Where the file spells a field is the writer's concern
  (`spelling_in`).* The scope is the single-entry read (`read_fields`:
  `get`, REST `GET /api/entries/{id}`, MCP `kb_get`). The index row
  (`base_backend._entry_to_dict`, the storage dict the plugins read declared
  fields from) keeps its whole `metadata` bag, as does the model's; a
  follow-up moves the plugins to top-level fields. Rationale (the decision
  packet on PR #1039): the "both" shape made an ordinary echo of a read fail
  (`metadata.provenance` sent back is a managed field) and spelled a field
  twice in one read; "the file's spelling only" brought back "update says
  done, get says no".
- **The row's rule:** `lift_declared_fields(result, bag, cls, entry_type)`
  replaces `_LIFTED_METADATA_KEYS` and the backend's copy of it. A declared
  field the row keeps in its `metadata` bag (no column of its own: a
  backlog item's `rank`/`effort`/`kind`, a task's `status_reason`, every
  task's `evidence`, an entry's `provenance`, which used to be dropped from
  every read) is read at its own name too, through `spelling_in` over the
  bag. The read's `metadata` is then `read_metadata`: the bag less every key
  that spells a declared field the read carries at its own name. A declared
  field a class never reads from its frontmatter (`UNREAD` below) keeps its
  bag spelling, so a read loses nothing.
- **Undeclared keys are not lifted** (the maintainer's decision, 2026-10-20):
  `rank` on an `actor` reads under `metadata` only. A key a tool reads at
  the top of a read is declared instead: `parked_awaiting` is a field of
  `TaskEntry`, read and emitted at the top level as before.
- **Not read under `metadata`:** `type:` picks the class before any
  spelling is read, and `id:` is the identity line (design principle 5).
- **Migrations run before the lift**, on the file's own spellings:
  `_load_entry` migrates the raw frontmatter and hands it to the loader;
  `schema migrate` diffs a migration's result against the file.
- **A double spelling** is left to the model (the top-level spelling wins,
  decision 3) and to `index health`; the lift skips the field, and the
  read keeps the reported second value under `metadata`: a read never
  loses a value the file holds. The rule for a bag key (round 3): it leaves
  `metadata` whenever the file has no top-level spelling of the field (the
  field has one value, the lift's, whatever a column made of it:
  `metadata: {importance: '8'}` reads `importance: 8` and no
  `metadata.importance`); a key the file also spells at the top level, a
  true double spelling, leaves only when the two values agree.
- **Every key the read returns reaches every surface** (REST `GET`, MCP
  `kb_get`, the CLI's JSON) with the same value. REST's `EntryResponse`
  declares the stable core and passes every other key through; its fixed
  field list dropped a lifted field with no slot (a task's `status_reason`,
  a backlog item's `rank`, `provenance`). A parametrised test compares the
  three surfaces for one entry of every registered type, and one test
  compares the key sets of REST `GET` (as an anonymous reader of a public
  KB) and MCP `kb_get`, bookkeeping fields (`created_by`, `modified_by`,
  `indexed_at`, `assignee`) included. REST's one key of its own is the
  `participants: []` default of `EntryResponse` on a type without that
  field; the web client reads it unguarded, so whether REST drops it is
  the maintainer's call (round 3).
- A declared field a class never reads from its frontmatter at any
  spelling (protocol mixin fields a class took but did not wire up: a
  person's `location`, an event's `start_date`) has nothing to lift; the
  grid pins the list (`UNREAD`) so it can only shrink.

The grid: `tests/test_reader_reads_where_the_writer_writes.py`, every entry
class × declared field × spelling, with a twin oracle (the file spelling the
field at a spelling reads as the file spelling it at its own name) over the
model, the row, `get`, search, health and the writers, plus the decision
packet's round trips (a read echoed back through REST `PUT` and MCP
`kb_update`, with a key added or a field edited, is written).

## Amends

- **ADR-0042, decision 6**, "A key is written where the operation names
  it": "A path `metadata.x` is a nested key like any other" is replaced by
  the decision above. ADR-0042's text was edited on acceptance (2026-10-19).
