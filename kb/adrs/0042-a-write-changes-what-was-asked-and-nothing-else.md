---
id: adr-0042
type: adr
title: "A write changes what was asked and nothing else: entries are edited by operation, read from the file"
adr_number: 42
status: accepted
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

> **Accepted by the maintainer, 2026-10-03.**
>
> **Written as proposed** (2026-10-02). The maintainer accepts or rejects it. The rule
> below (decisions 1 to 13) is the maintainer's, stated 2026-10-02. The first
> spike measured a text splice, not this rule, and an adversarial read of the
> first draft found holes that the measurements could not have shown. **Spike
> 2 (2026-10-02, 28,041 files across 52 real KBs) ran the rule on the real
> write path and found it holds, with the changes now written into decisions
> 2, 5, 8, 9 and 11, and the hooks table.** What it did not measure is listed
> in section "What spike 2 measured, and what remains". First-spike
> measurements are from `origin/dev` at `d35eae77`, ruamel.yaml 0.19.1; line
> references in the context are at that commit. Spike 2 ran on `d3fdc172`.

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
- **No compare, no merge and no lock** (no `flock` or `fcntl` in
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

The write pipeline for an existing entry is: read the file and its base
(decision 10); apply the operation to the file's parsed document and splice
the text; parse the result; check that every path outside the operation parses
to what it was and the operation's path parses to what was asked; validate the
result through the type (kb.yaml schema, validators, hooks, decisions 4 and 8);
publish atomically. If a narrow edit fails the check it widens to the whole
top-level key, still as an operation on the file's own value. If that fails,
the write is refused. **There is no fallback that re-emits the file or writes
the model's rendering of a key the operation touched.**

**A widened emit copies each unchanged item's bytes from the file, or the write
is refused.** It never re-emits a sibling item through a serializer. Spike 2
found why: when a sub-key cannot be added by a narrow edit, a widen that
re-emits the whole key with the document's detected list indent respelled every
sibling item in 52 of 376 "add a sub-key to `links[0]`" operations (some files
indent different lists differently). The values were equal and the bytes were
not, so decision 1 ("siblings' bytes alone") and "widen to the whole key" could
not both hold. Two consequences:

- The narrow edit **add a sub-key to a mapping item** is an operation of its
  own: insert one line at the item's indent. It was one line in 324 of the 376
  link cases (the other 52 took 4 to 49 lines) and one line in 4,940 of 5,071
  source cases (the other 131 took 2 to 9).
- Where the narrow edit does not apply, the widen is item-wise: unchanged
  items are copied byte for byte from the file, and only the item the operation
  names is emitted. If that cannot be done, the write is refused with the
  reason.

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

**What spike 2 found the hooks leave in real files** (52 KBs, hooks and plugin
types on):

- **The link hooks left no values on the reference corpus.** Cascade's
  `resolve_actor_links` left 0 `actor_reference` links in the 3 cascade-typed
  KBs; re-running it derives nothing for 6,484 events (their actors live in
  another KB and its lookup is same-KB only) and 8 links for 3 scenes.
  `enrich_connection_links` left 0 in the one journalism KB. The social
  `author_id` hook fires in no KB. So the index-time derivation is **a new
  capability, not a preservation of something files hold**; the generic
  backlinks and graph (used by the investigation, network-mapper and research
  skills) lose nothing if hooks stop writing, and nothing reads the
  `actor_reference` relation by name (the Hugo converter and `static_export`
  read `actors`).
- **The cascade derivation must resolve actors across KBs, or say that it does
  not.** Same-KB resolution derives nothing for the 6,484 events above. The
  derivation's contract names which it does (ADR-0043 and the one KB registry
  decide what "across KBs" means).
- **Only the parent rollup left values: 37 parent tasks** in the research KB
  (83 parents) are `done` with no `status_change_log` entry and `updated_at`
  within 5 s of their last child (a timestamp check; git history was not read).
  The same KB has 4 parents closed by hand, 10 with every child resolved and
  not `done`, and 8 `parent` values that point at no entry. If hooks stop
  writing and nothing replaces the rollup, decomposed research tasks stay open
  after their children finish, a worker could claim one, and the investigation
  conductor's drain check would count it.
- **The rollup's consumers** are `task list --status open --parent <epic>`, the
  investigation conductor's drain check, and `task decompose`. Those read
  completion; they must read the derived completion.
- No `parent` value points at an id-less file, so the identity switch
  (decision 5) orphans no task tree. The 8 dangling `parent` values predate it.

**The derived completion lands in the same change as the hook stops writing.**
It is an acceptance criterion of this ADR: *`task list` shows a parent's
derived completion, and its open filter honours it* (a parent whose children
are all resolved is not listed as open), and the drain check and
`task decompose` read the same value. Step 6 of the phasing does not merge
without it.

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
  pin" with the command that pins it. (Inert on the maintainer's corpus: no
  type has a templated subdirectory. The rule stays for other users.)
- **Existing links that point at title-derived ids.** Switching rules changes
  the id of every id-less file whose title-derived id differs from its path.
  Upgrade runs a one-time report: each such file, its old and new id, and
  every entry or database row that referred to the old one (links, stars,
  reviews, `entry_version` rows). A command applies the rewrite on request:
  links in files by decision 12; database rows by re-keying. Nothing is
  rewritten silently. Old ids do not keep resolving (decided 2026-10-02).
- **Files with no `id:` are warned about, and the upgrade notes say how to
  migrate them** (maintainer, 2026-10-02). `index health` and `qa` list every
  file with no `id:`. The release notes and the upgrade doc give the steps:
  list the files, and pin each one's current id by adding an `id:` line
  before upgrading, so nothing that links to it changes. This is a task an
  agent can do. Most KBs have no such files.
- **An `id:` is part of Pyrite's contract with a file** (maintainer,
  2026-10-02: "pyrite does have a contract with your files, but most of that
  contract is explicit. I think an ID is worth having in the contract"). The
  contract is written down and small: YAML frontmatter, and an `id:`. A file
  with no `id:` is outside the contract: it is still read and addressed by
  its path, so nothing breaks, and it is reported as missing an id until one
  is added. Pyrite does not add the id as a side effect of another write;
  adding it is an explicit command or a hand edit. What else the contract
  names (`type`, `title`) is a question for the maintainer.
- **Dry run on the real corpus (spike 2).** 292 of 28,041 files (1.0%, in 9
  KBs) have no `id:`; two agent-workflow KBs are almost all id-less (111 of
  115 files and 64 of 70). All 292 change id under the path rule (16 are at a
  KB root; 4 take the `entry-xxxx` hash fallback; 1 is `_index.md`). Of the
  290 distinct changed ids, 69 are referenced, by 1,106 references from 296
  files: 694 wikilinks, 412 bare id fields and 0 frontmatter `links:` (57 of
  the 412 are `tags` values, probably coincidental strings). `entry_version`,
  `starred_entry`, `review`, `entry_ref` and `edge_endpoint` rows: 0 each, so
  database re-keying is empty on those installs. No path id equals another
  file's `id:`, and the path rule has 0 collisions.
- **Pinning changes nothing.** On a copy, each id-less file got `id: <the id
  the index holds for it today>` as one `set id` operation (every diff is one
  added line). Compared with the unmigrated index: every `(kb, id, path)`
  unchanged, link rows 52,445 = 52,445, resolved links 41,457 = 41,457, tags
  and sources identical. No id-less file remains, so the path rule is never
  consulted.
- **Bare id fields are references too.** Of the 412 references, fields such as
  an outlet id, a contact id, an exploiting party and a list of firms hold
  entry ids. Wikilinks and `links:` are not the only references: `qa` reports
  a dangling bare id field the way it reports a dangling link.
- **A shadowed collision exists under today's title rule:** one group of 3
  files, 2 of which are shadowed in the index and unreachable. Pinning cannot
  choose for the operator: the operator picks which file keeps the id and
  renames the others (the dry run used `<id>-2` and `<id>-3`, which is a
  choice). The two shadowed files become reachable after pinning (+2 entries,
  +145 blocks).
- **Two commands ship one release before the switch,** so an operator pins
  while the old rule is still in force: one lists files missing an id
  (`ids missing`), one pins them (`ids pin`, with `--dry-run` and
  `--rename <path>=<id>`). **The pin is a single `set id` operation.** It is
  not `pyrite update`: today's update would also nest keys under `metadata:`,
  rename aliases, refuse the files that are invalid in another field, move
  root-level files and rewrite the whole file. None of the commands exists
  today: no command lists id-less files, `schema validate` reports only
  collisions and `qa fix` has no id fixer. (The names are illustrative; the
  CLI contract is #303.)

  **Upgrade steps, as a reader follows them:**
  1. With the old version installed, run `pyrite ids missing -k <kb>`.
  2. Run `pyrite ids pin -k <kb> --dry-run`, read the plan, then
     `pyrite ids pin -k <kb>`. Each file gains one line.
  3. If two files share an id, choose which keeps it and rename the other
     (`--rename <path>=<id>`). The other file was never reachable.
  4. Commit (`git diff --stat` shows one added line per file), upgrade, run
     `pyrite index build`, and check that `pyrite index health` reports 0
     files without an id.

  An agent runs the same steps with `--format json` on `kb list`,
  `ids missing`, `ids pin --dry-run`, and `index health`.
- **`/` in an id (spike 3, 2026-10-02; choices decided by the maintainer,
  2026-10-03).** An id may contain `/`. No segment may be empty, `.`, `..`,
  `-` or start with `.`; `\` and NUL are refused; the built path is still
  checked to be inside the KB. Wikilinks accept `/` (`[[posts/intro]]`,
  `[[kb:posts/intro]]`, Obsidian's spelling). REST routes take the id with the
  path converter, and **sub-resources go after a `/-/` separator**
  (`/api/entries/<id>/-/blocks`, `/-/versions`), so no entry id can collide
  with one; a folder named `-` is refused in ids. The same separator carries
  site pagination (`/site/{kb}/page/N` moves behind it, or an equivalent
  reserved segment), so an entry `page/2` cannot collide. The web encodes ids
  with `encodeURIComponent` everywhere (one `entryHref` helper) and keeps the
  `[id]` route. The MCP resource URI is percent-decoded. **Create with a `/`
  id writes `<id>.md` from the KB root**, not under the type's folder, so the
  path and the pinned id agree. **Ids that differ only in case are reported by
  `index health`** (APFS is case-insensitive, Linux is not). The site cache
  names pages without collisions (`posts/intro` and `posts_intro` today both
  become `posts_intro.html`). Measured on scratch KBs: the storage and index
  changes are two lines (`_validate_entry_id`, and dropping `/` from the
  wikilink target check); today REST returns 404 for `posts%2Fintro` because
  routing decodes `%2F` first; ten web components link to an unencoded id; 3
  of 925 files in this repo's KB hold a `/` wikilink that is not an entry and
  will be reported dangling. Rename's link-rewrite gap (`[[x:old]]` and
  `[[old#H]]` are not rewritten, with plain ids too) is #684.
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
  refused and reported. Spike 2 confirmed the rule on real files (95 appends
  to `actors` were written under the file's own `participants` key) and found
  what it depends on: **`FRONTMATTER_ALIASES` is a set of alias names with no
  target**, so the spike needed a hand-written map. Alias resolution therefore
  requires the targets to be schema data: **ADR-0045 decision 8 is required
  before this lands, not optional.**
- A key that defines or uses a YAML anchor is refused for change, with the
  reason. TOML and JSON frontmatter are refused untouched (as today).

### 7. Bookkeeping never rides alone; repairs are commands

- `updated_at` is written only where the file already has the key and only
  when the write changed something. A no-op writes nothing. **Decided by the
  maintainer, 2026-10-03** (was question 1): `updated_at` is stamped only
  where the file already carries the key and the write changed something; it
  is never added to a file that lacks it.
- Adding `id:`, renaming an alias key, stamping `_schema_version`, dropping a
  stray `body:` key and re-quoting existing values are explicit commands
  (`qa fix`, `schema migrate`), never side effects of an update.
- Migrations are reading rules, never writes, and are idempotent. Files behind
  their type's version are reported, not silently migrated on save. Spike 2
  found migrations inert on the maintainer's corpus: 0 of 365 types declare a
  `version`, no plugin registers a migration and no file carries
  `_schema_version`. Keep the "behind schema version" report; **acceptance
  does not wait on it.** The review's re-run-on-every-load case cannot happen
  there today and stays covered for other users by the rule.

### 8. Validation: refuse what the write causes, report what it does not

A write is refused for a violation in a field it touches or that its result
causes (a cross-field rule). A violation that exists before and after in a
field the write did not touch is returned as a warning with the write's
result. This generalises the existing downgrade for off-list enum values on
unchanged fields (`kb_service.py:278`, `:340-343`): a hand-written file with a
missing required field can still be edited elsewhere, and the response says
what is wrong.

Spike 2 sized it. Across 14,034 files, 143 (1%, in 8 KBs) are invalid in a
field the write did not touch. **The most common existing violation is an
undeclared type** (an `entry_type` the KB's `kb.yaml` does not declare), then
missing required fields; the undeclared type is a warning, not a refusal. The
candidate edited all 143 with a warning. Today's path refuses every update of
34 of 1,427 sampled files (2.4%), including 21 echoes refused for an enum value
already on disk, which `_keep_on_disk_enum_values` was meant to allow (#676).
The candidate still refused 183 of 2,667 sets of a `kb.yaml`-declared key
(enum) and 45 of 14,033 unsets (required field), as it should: those are
violations the write causes.

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

**The fields of a read are the file's parsed values.** Type defaults,
normalised values (a coerced enum, a re-typed date) and anything computed
(derived links, derived completion) come back **only under a key labelled
derived**, never among the fields. Spike 2 measured why: sending
`entry.to_frontmatter()` (the type's reading) back through the same splice
wrote 1,295 of 2,826 files (46%), adding `verification_status` (606 files),
`importance` (316), `research_status` (180), `tier` (58), `rank` (45),
`status` (42) and `id` (33), and changing `sources` in 484. An echo of the
file's own parsed values was byte-identical on all 14,034. **Echoed defaults,
normalised values and derived links are one rule:** anything the type or the
index computes must come back from a read under a derived key, or an echo
writes it into the file. (This is the same property as ADR-0045 decision 3.)

### 10. Writes are optimistic: merge against the base, lock only the instant of replace

*Reconsidered by the maintainer before merge: "the git way is not to lock and
to just surface conflicts and make them easy to resolve", then "go with
optimistic plus the momentary lock".*

- **Scope: local file changes between commits.** The usual way changes to a
  KB are shared is git: a pull request, a patch, a branch rebased and merged,
  a commit. There git's own merge and conflict tools are the conflict surface,
  and Pyrite does not reinvent them (ADR-0044 builds on that). This decision
  covers only the narrower case of several writers (agents, the CLI, a server,
  a person's editor) changing files in one working tree before the next
  commit (maintainer, 2026-10-03).
- **Optimistic concurrency.** No lock is held while a caller reads, decides
  and sends its change (server, CLI and stdio MCP share the files). Every
  write carries the base it was made against: the content hash, or for a field
  operation the field values it read, as git merges against a base.
- **Three-way merge at write time, per key.** Base is what the caller read,
  theirs is the file now, yours is the request. A change to a key nobody else
  changed merges automatically (spike 3's "8 writers, different keys" case
  needs no lock to be correct). The same key changed on both sides, or
  overlapping body changes, is a **conflict**: nothing is written, and the
  response carries base, theirs and yours and the one operation that resolves
  it (re-apply on theirs, keep theirs, or replace with an explicit value). The
  CLI, MCP and REST map it to their conflict code; the CLI prints it
  readably. A human editing the file in another tool is just another "theirs".
- **The momentary lock, as git takes for a ref update.** Held only for the
  instant of compare-and-replace: acquire, re-read the file and compare it
  with what the merge was computed on (if it changed, merge again, at most 5
  times, then conflict), write and fsync the temp file, compare again
  immediately before `os.replace`, rename, release. Never held while work is
  done. A write that moves a file takes the locks of both paths in sorted
  real-path order. It is an OS lock (`flock`; `msvcrt.locking` on Windows) on
  a **sidecar lock file that is never replaced or deleted**, named by the
  SHA-256 of the entry file's real path, in a per-user lock directory outside
  the KB that does not depend on which config or data directory a process
  resolved (decision 5 of ADR-0041; not `default_data_dir()`, which varies by
  working directory), plus an in-process lock keyed by the same path for the
  server's threads. This gives git's momentary semantics without git's one
  wart, a stale `index.lock` after a crash: the kernel releases an OS lock if
  the holder dies (0.2 ms to the next acquire). Spike 3 rules out the
  alternatives: a lock on the entry file lost 52% of increments (the atomic
  replace changes its inode); a process-wide POSIX record lock lost 85% across
  threads; an `O_EXCL` lock file is left behind by `kill -9` and recovery
  races.
- **Per host only.** Two machines writing one KB through a shared or synced
  folder share no lock; they rely on the compare and the merge, and conflicts
  surface there too. A small window between the last compare and the rename
  remains for a writer that does not take Pyrite's lock; it is stated, not
  claimed away. Spike 3, an editor saving every 3 ms against 300 locked
  increments: comparing before `atomic_write_text` lost 9 to 97 of the
  human's saves; comparing after the fsync lost 5 to 19. What nothing
  prevents: an editor that saves an old buffer later.
- **A file missing at write time** is a conflict ("moved or deleted"), never
  recreated: only create creates a file.
- The replace uses `pyrite/utils/atomic_write.py`, which follows symlinks.
- The index row is built from the bytes written, with their hash, never from
  an in-memory entry.
- **The claim** (`claim_entry`, `kb_service.py:2357-2392`) is a conditional
  write: "set `status` to claimed if it is still open", decided against the
  file, then the row is updated. Anyone else's claim is a conflict, including
  a human's that is not yet indexed. The row is never the tiebreaker. This
  amends ADR-0029 section 4 (question 3, decided by the maintainer 2026-10-03: the claim is decided against the file). Measured on today's code (spike
  3): a hand-set claim not yet indexed is overwritten and the agent is told it
  won. As a file operation under the sidecar lock, 8 claimants raced 5 times
  gave exactly 1 winner each; with no lock, 2 to 4.
- **Spike 3 (2026-10-02, scratch KBs on macOS APFS, 10 cores, Python 3.12).**
  8 processes x 50 increments, 3 runs a row. Lost updates of one counter, of
  400: sidecar `flock` 0, sidecar `lockf` 0, `O_EXCL` file 0; no lock 227 to
  238 (58%); `flock` on the entry file 203 to 214 (52%); sidecar locks in two
  different lock directories 172 to 185 (45%). Different keys, 8 writers: 0
  writers lost updates with the sidecar, 8 of 8 without. 8 threads in one
  process: sidecar `flock` 0 lost, sidecar `lockf` 346 of 400 (85%). The lock
  costs about 5% (1 writer 1,071 ops/s unlocked, 1,018 with `flock`; 8
  contending 582 to 796). A file moved by one writer while another sets a
  field leaves two files with one id today; with the compare it leaves one and
  reports the setter's conflict. Linux, Windows, NFS/SMB and synced folders were not
  run.

**Decided by the maintainer, 2026-10-03** (was question 2, an optional guard
on a field set or body replace): answered by this decision. Every write
carries its base, so no separate optional `expect` argument is added to set
and replace-body for the web editor; the base is the guard.

### 11. The token: field operations need none

An operation on a field carries no token. Only **replace the whole document**
carries the caller's `content_hash`, and is refused with a conflict that says
"re-read" when the file's hash differs. On a match, the request is compared
with the file's value (decision 3's equality, applied to the file's parsed
values), and only differing keys are written, as set and unset operations.
Every write returns the new `content_hash` and a report: what was written,
what was `unchanged`, what was not moved, warnings.

A whole-document echo of a read is therefore either exact (nothing written) or
refused as stale. That holds only because a read returns the file's values as
its fields (decision 9): an echo of the type's reading would differ from the
file in 46% of real files, and the comparison would write the difference. The web editor sends operations for the fields the user
changed, not the document. Whether a body replace or a set should also accept
an optional guard against a stale form is **question 2**.

### 12. Every write of an entry file goes through this path

Rename's link rewrite (operations on other files' `links:` and wikilinks,
preserving line endings, each against its base), schema migrate, `qa fix`, the
claim and task services, and plugins' commands. `software-kb`'s
`cli.py:704-714` and cascade's `migration.py` write entry files with
`write_text` and regex today; they are converted (cascade is deleted). A
plugin writes entry files only through `ctx.kb_service` (ADR-0040), and so is
bound by decisions 1 to 11. ADR-0041 and this ADR say the same thing.

**The root-file move (#488) is a fix in this decision.** Today every update of
a root-level file whose type has `subdirectory: ""` and a `file_pattern` moves
it into the type's folder: 6,071 real files are exposed, and the sample of
today's path saw 1,854 moves (reproduced on one file in isolation). An operation never moves a file except
the templated move of decision 5.

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

## What spike 2 measured, and what remains

**Spike 2 (2026-10-02, 28,041 files across 52 real KBs).** Copies of every KB
in the maintainer's registry (read-only originals, a second copy to work on;
no symlinks, CRLF or BOM in the corpus), run through `KBService` with hooks,
`kb.yaml` schemas, plugin entry classes and `FRONTMATTER_ALIASES` on, in a
scratch home. Building the index wrote nothing (a SHA manifest of the copy was
identical before and after). The candidate was the first spike's candidate C
with its `'it''s'` defect fixed (`''` inside single quotes is an escape),
followed by `KBRepository._load_entry`, `_validate_write` and the real
`before_save` hooks on a copy (recorded, not applied). The oracle was
independent of the candidate: PyYAML parses before and after, its `compose`
gives each top-level key's line span, every other key must parse equal, the
target must equal the operation applied to PyYAML's value, and key order, body
bytes, line endings and the span of every changed line are checked. Run against
today's path as a control, the oracle caught today's known defects, so it can
see failures.

**The rule held on the real path.** Over every second file (14,034 files,
about 127,000 operations): no collateral key changed, no target was wrong, no
changed line fell outside the target key's span, and no splice refused or
raised.

| Operation | n | Result |
|---|---|---|
| Echo of the file's parsed values | 14,034 | all byte-identical |
| Set `title` | 14,015 | 2 lines changed |
| New undeclared key | 14,034 | 1 line, top level, at the end |
| Set a `kb.yaml`-declared key | 2,667 | 183 refused (enum), as they should be |
| Append to / remove from `tags` | 11,898 / 11,896 | 1 to 3 lines |
| Append to `sources` / `links` | 5,096 / 397 | 2 lines |
| Append to `actors` | 3,351 | 95 written under the file's own `participants` key |
| Set `links[0].relation` | 374 | 2 lines |
| Add a sub-key to `sources[0]` | 5,071 | 1 line in 4,940; 2 to 9 in 131 |
| Add a sub-key to `links[0]` | 376 | 1 line in 324; **4 to 49 in 52** (decision 2) |
| Unset a key | 14,033 | the key's own lines; 45 refused (required field) |
| Replace body | 14,034 | body only |

About 5,500 real `links`, `sources` and `provenance` edits lost nothing:
unknown sub-keys and legacy spellings parse back unchanged (the first draft's
data-loss case). Hooks, had they been allowed to write, would have changed 10
`scene` files (3 `actor_reference` links each).

**The control: today's path, same oracle, every 20th file (1,427 files).**

| Today's path | Result |
|---|---|
| Echo of the read | 558 of 1,427 byte-identical; 171 rewrote lines outside any asked key; 200 changed the body |
| Undeclared key | 332 (23%) went under a `metadata:` block instead of the top level (#178); 343 real files already have a top-level `metadata:` |
| Any save | `id:` added to files with none; `participants:` renamed `actors:` |
| Update of a root-level file | moves it into the type's folder (#488): 6,071 files exposed, 1,854 moves in the sample |
| Update with `sources` or `links` as JSON objects | `AttributeError` on `to_dict`, 1,134 cases (#675; believed raised before anything is written) |
| Files invalid in another field | 34 of 1,427 (2.4%) refuse every update; 21 echoes refused for an enum value already on disk (#676) |

**The corpus found a hole in the first draft's reads (decisions 9 and 11):**
the type's reading is not the file's values, and echoing it wrote 1,295 of
2,826 files (46%).

**Which spike 2 conditions are met.** Of the nine things the first draft
required spike 2 to cover:

| Required | Status |
|---|---|
| 1. Typed KBs with `kb.yaml` | Met (declared keys, enums, required fields) |
| 2. Hooks | Met: what each writes, and what reads it (decision 4) |
| 3. Plugin types | Met for the types the 52 KBs use. One fixture per plugin class was not run |
| 4. Nested values | Met (about 5,500 edits) |
| 5. Migrations | Inert on this corpus (0 of 365 types declare a version); the "behind schema" report stays, acceptance does not wait on it |
| 6. Undeclared keys, `metadata:`, aliases | Met, with the alias-target finding (decision 6) |
| 7. Shapes the corpus lacks | **Remain.** CRLF, BOM, anchors, duplicate keys and Hugo shapes are not in the corpus. The first spike tried 26 hand-made files; they need fixtures in the doc's test |
| 8. Concurrency | **Measured on macOS APFS (spike 3, decision 10)**: several writers, an editor saving between read and replace, a move race, the claim race. **Not run:** Linux, Windows, NFS/SMB and synced folders, several OS users sharing a lock directory, a real `git checkout` (a rename-style saver stood in), a move race with two real processes |
| 9. Reads | Simulated in the harness; `get` reading the file is **not implemented**, so its cost per `get` was not measured on the real path |

`/` in an id was measured by spike 3 for storage, the index, REST, MCP and the
web route (decision 5); not in a browser, not behind a reverse proxy (Apache
refuses `%2F` unless `AllowEncodedSlashes` is set), not on Windows separators,
not in export or the Quartz renderer, not on the public demo KBs. The git
history of the real repos is still not measured (the 37 rollups rest on a
timestamp check).

**What remains is a pre-condition of the step it blocks, not of acceptance:**
the hand-made fixtures block step 2's doc. Concurrency (step 4) and `/` in ids
(step 7) are measured; they now block on implementation, with the criteria
below, and concurrency on Linux and Windows runs. Spike 2's answer is "mostly yes": the rule
holds as written for decisions 1, 3, 4, 6, 7, 10 and 13, with the amendments to
decisions 2, 5, 8, 9, 11 and 12 and the hooks table above. Acceptance is the
maintainer's.

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

**Added by spike 2.** Examples and checks the real corpus calls for:

- *An echo of a read, with a type that has defaults, writes nothing.* The
  `echo` flag runs on a typed entry whose type supplies defaults (a
  verification status, an importance): the diff is empty, and the defaults come
  back only under the derived key.
- *Adding a sub-key to a link in a file whose lists indent differently* inserts
  one line at the item's indent and leaves the sibling items' bytes alone (the
  52-of-376 case).
- *A file invalid in another field is edited with a warning* (an undeclared
  type): the write succeeds and the report names the warning.
- *A pin of an id-less file* is one added `id:` line, and no link in the index
  changes.
- *A save of a root-level file* does not move it (#488).
- *`task list` shows a parent's derived completion, and its open filter
  honours it.* A parent whose children are all resolved is not listed as open;
  the drain check and `task decompose` read the same value (decision 4).
- *The cascade derivation says whether it resolves actors across KBs*, and a
  test shows which.
- Fixtures for CRLF, BOM, anchors, duplicate keys and a Hugo post (none are in
  the corpus), since spike 2 had none.

### Acceptance criteria from spike 3 (condensed)

**Concurrency (decision 10; step 4), optimistic plus the momentary lock.**

1. New `pyrite/utils/file_lock.py`: `entry_lock(path)` locks
   `<per-user lock dir>/<sha256(realpath)>.lock` (the directory is not
   `default_data_dir()`); inside, a `threading.Lock` per real path, then
   `flock(LOCK_EX)` on POSIX or `msvcrt.locking` with retry on Windows; the
   lock file is never unlinked. `entry_locks(*paths)` takes several in sorted
   real-path order. It is held only for compare-and-replace.
2. `atomic_write_text(path, text, *, expect=None)`: with `expect`, compare the
   file's bytes after the temp file's fsync and immediately before
   `os.replace`; on mismatch remove the temp file and raise
   `FileChangedError`. Existing callers are unchanged. On Windows `os.replace`
   gets a bounded retry on `PermissionError`.
3. Every entry-file write (decision 12) carries its base, merges per key
   against the file now, and replaces inside `entry_lock`; on
   `FileChangedError` it merges again up to 5 times. A same-key or
   overlapping-body change, or a missing file, raises a conflict carrying
   base, theirs, yours and the resolving operation, and writes nothing; the
   CLI, MCP and REST map it to their conflict codes.
4. `claim_entry` is a conditional write on `status` and `assignee` in the
   file; the row is updated after.
5. Tests, process-spawning per `tests/test_task_claim_concurrency.py`:
   - 8 processes change 8 different keys concurrently: all 8 changes are
     present, no conflicts.
   - 8 processes change the same key: exactly one succeeds per round, the
     others get a conflict carrying base, theirs and yours, none lost
     silently.
   - A human edit to a different key between read and write is kept; to the
     same key it gives a conflict.
   - The claim race: exactly one winner, and an unindexed hand claim is not
     overwritten (`claimed: False`, file byte-identical).
   - A process killed (`SIGKILL`) during the momentary step leaves no lock
     that blocks the next writer (acquired within 1 s).
   - Two different configs (`PYRITE_DATA_DIR` or working directory) still
     exclude each other at the momentary step.
   - 8 threads in one process change 8 different keys: all present.
   - The move race leaves no file at the old path and a conflict for the
     setter.
   - `expect=` raises when the bytes change between read and replace (inject
     with a monkeypatched `os.fsync`), leaving the file untouched and no temp
     file.

**`/` in ids (decision 5; step 7).**

1. `_validate_entry_id` accepts `posts/intro` and `a/b/c`; refuses `""`, `/x`,
   `x/`, `a//b`, `./x`, `a/../b`, `a/.hidden`, `..`, `-` as a segment, `a\b`
   and a NUL byte.
2. Wikilinks: `[[posts/intro]]`, `[[kb:posts/intro]]`, with alias and with
   `#Heading` each index one link to `posts/intro`, and backlinks return the
   source. The test `test_cross_kb_links.py::test_path_like_targets_are_rejected`
   is reversed.
3. REST: GET, PUT, PATCH and DELETE work with `posts%2Fintro` and
   `posts/intro`; blocks and versions work at `<id>/-/blocks` and
   `<id>/-/versions`, registered before the entry route; `qa/validate`,
   `starred` and `tasks/claim` take the path converter; site pagination moves
   behind the same separator; the two structural route tests
   (`test_every_entry_point_passes_the_policy.py`,
   `test_read_scoping_is_structural.py`) are updated.
4. MCP: `kb_get`, `kb_update`, `kb_delete`, `kb_create` work with
   `posts/intro`; the `pyrite://entries/{+id}` resource resolves encoded and
   raw ids.
5. Web: an `entryHref(id, kb)` helper replaces the ten unencoded links
   (`EntryCard`, `BacklinksPanel`, `EntryMeta`, `Sidebar`, `StarredSidebar`,
   `AIPanel`, `ChatSidebar`, `TableView`, `GalleryView`, `KanbanView`); a unit
   test gives `entryHref("posts/intro","k")` = `/entries/posts%2Fintro?kb=k`;
   a Playwright step opens a slash-id entry from a card and from backlinks.
6. Site cache: `posts/intro` and `posts_intro` render two pages.
7. Create: `posts/new` writes `posts/new.md` from the KB root.
8. Rename to and from slash ids rewrites the same link forms #684 requires.
9. `index health` reports ids that differ only in case.

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
  concurrency guard" is amended by decision 10's last bullet (question 3, decided 2026-10-03).
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
- Concurrent writers and checkouts are handled by a merge against the base and a momentary lock; a conflict is reported, never lost.

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

**Phasing** (each one reviewable PR; none before the maintainer accepts):

0. One release ahead of step 7: `ids missing` and `ids pin` (decision 5).
1. Spike 2 (done, 2026-10-02; the concurrency, hand-made-shape and `/`-in-id
   checks it did not run belong to steps 2, 4 and 7).
2. The doc and its runner, as expected failures.
3. The operation function (pure), not wired.
4. Wire the update path: base, merge, momentary lock, no-op writes nothing.
5. Reads from the file; `content_hash`; the new hash and report on writes.
6. Hooks stop writing; derivations in the index. **Not without the derived
   completion** (`task list` shows it and its open filter honours it; the
   drain check and `task decompose` read it): see decision 4.
7. Identity from the path; the one-time report and rewrite.
8. Delete the load-time records; the other writers; explicit repairs.
9. Interfaces: `unset`, operations on CLI, MCP and REST, the web editor.

Spike 2 found two product bugs on today's path, outside this ADR's rule but
in its way: #675 (`update` with `sources` or `links` as JSON objects raises
`AttributeError`) and #676 (an echoed update is refused for an enum value
already on disk). #488 (root-file move) is fixed by decision 12.

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

Ranked by what they block. Questions 1 and 2 were answered on 2026-10-03 and
are recorded in decisions 7 and 10; question 3 is decided (2026-10-03): the claim is decided against the file, not the index row.

**Decided by the maintainer, 2026-10-02** (the items are kept below for
their reasoning): question 4, the id is the path without `.md`, with `/`
separators (`posts/intro`); question 5, old title-derived ids do not keep
resolving, files with no `id:` are warned about, and the upgrade notes give
the migration steps (decision 5). **Decided 2026-10-03** (spike 3's four
choices, taking its recommendations; recorded in decision 5): REST
sub-resources and site pagination go behind a `/-/` separator and a folder
named `-` is refused; create with a `/` id writes `<id>.md` from the KB root;
`index health` reports ids that differ only in case.

1. **`updated_at`. DECIDED 2026-10-03** (recorded in decision 7). Keep stamping it where the file already has the key and
   something was written, never stamp it, or stamp only on request?
   *Recommended: keep, as stated.* A write "does what was asked"; a stamp is
   not asked for. Blocks the doc's examples (step 2).
2. **A guard on a field set or body replace. DECIDED 2026-10-03** (answered by decision 10, recorded there). Field operations need no
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
