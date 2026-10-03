---
id: adr-0045
type: adr
title: "Types and protocols are the extension interface; serialisation is not"
adr_number: 45
status: accepted
date: 2026-10-02
tags: [architecture, plugins, types, protocols, extensions, contract]
links:
- target: adr-0040
  relation: amends
  kb: pyrite
- target: adr-0014
  relation: amends
  kb: pyrite
- target: adr-0017
  relation: related
  kb: pyrite
- target: adr-0002
  relation: related
  kb: pyrite
- target: adr-0041
  relation: related
  kb: pyrite
- target: adr-0042
  relation: related
  kb: pyrite
- target: adr-0043
  relation: related
  kb: pyrite
- target: protocols-module
  relation: related
  kb: pyrite
- target: pyrite-plugin-protocol
  relation: related
  kb: pyrite
---

# ADR-0045: Types and protocols are the extension interface; serialisation is not

> **Accepted by the maintainer, 2026-10-03.**
>
> **Written as proposed** (2026-10-02). The maintainer accepts or rejects it. It is
> directional and short: it says what ADR-0042 changes in ADR-0040's alpha
> contract, what has to be learned before that contract is frozen, and asks
> the questions that decide the shape. The maintainer's words (2026-10-02):
> "kb entry types are like types. They provide structure to data, and allow
> for an extensible protocol interface." Nothing here removes types or
> protocols or their behaviour. It moves one thing: who writes the bytes of an
> existing file.
>
> Spike 2 of ADR-0042 (2026-10-02, 28,041 files across 52 real KBs) bears on
> decisions 3, 7 and 8 and question 3; its findings are recorded there.

## Context

### What is already decided

**ADR-0014** (accepted, 2026-03-01) decides that entry types interoperate
through protocols:

- "**Structural, not nominal.** A type satisfies a protocol if it has the
  required fields and behaviors. No inheritance or explicit declaration
  needed."
- "**Small primitives that compose.**" Larger bundles are compositions.
- "**Platform-backed behavior.** ... A protocol without platform support is
  just documentation. A protocol with platform support is an extension
  point."
- "**Schema-as-implementation.** When a type's schema defines the right fields
  and transitions, the platform can provide default behavior -- no plugin code
  needed. ... The schema is the program."

Its Python analogy maps a Pyrite entry type to a class ("data + behavior"), a
protocol to `typing.Protocol`, and platform operations (`before_save`,
`validate`, `claim`) to dunder methods. Its primitives are `has_status`,
`has_assignee`, `has_evidence`, `has_parent`, `workflow`, `atomic_claim`,
`rollup` and `source_chain`, composed into `claimable`, `decomposable` and
`verifiable`.

**ADR-0017** (accepted, 2026-03-03, "refines" 0014) implements protocols as
Python dataclass mixins in `pyrite/models/protocols.py`, composed by multiple
inheritance, each carrying `_<protocol>_to_frontmatter()` and
`_<protocol>_from_frontmatter()`; adds `get_protocols() -> dict[str, type]` to
the plugin protocol; and lets a `kb.yaml` type declare `protocols: [...]` so
that a generic entry gains the fields.

### What exists in code

Verified by running `pyrite protocol list` and `pyrite protocol check` and
reading `pyrite/models/protocols.py`, `pyrite/plugins/protocol.py` and the
CLI (at `origin/dev` `e5d26431`).

| ADR-0014 phase | In code |
|---|---|
| 1. A `protocol` entry type; primitives and bundles as KB entries; `pyrite protocol list/show` | No `protocol` entry type and no protocol entries in any KB. `pyrite protocol` has `list` and `check`, and no `show`. Protocols are six Python mixins in `PROTOCOL_REGISTRY`: `assignable`, `temporal`, `locatable`, `statusable`, `prioritizable`, `parentable` (ADR-0017 lists five; `parentable` was added). None of ADR-0014's primitive or bundle names is a registered protocol |
| 2. `pyrite protocol check`; `requires_protocols` and `provides_protocols`; install-time validation | `check` exists (and `pyrite schema` calls it). No `requires_protocols` or `provides_protocols` appears in `pyrite/` or `extensions/`. With no KB named, `check` tests six core type and protocol pairs and all six pass by the **nominal** tier |
| 3. Schema-defined fields get platform behaviour with no plugin code | Partly. A `state_machine` in a type's schema opts the type into the transition check (`_task_validate_transition`, `task_service.py:932`). A `kb.yaml` type is a generic entry and gains the fields of the protocols it declares (ADR-0017). `claim_entry` (`kb_service.py:2334`) is "protocol-level" for Assignable plus Statusable; `_parent_rollup` fires for any entry with a `parent` and a resolved `status`. Not verified: whether a `kb.yaml`-only type with `status` and `assignee`, and no declaration, gets claim support |

### Where the two accepted ADRs disagree

1. **Structural or nominal.** ADR-0014 says structural, with no inheritance.
   ADR-0017 composes mixins by inheritance. `check_protocol_satisfaction`
   (`protocols.py:244`) tries three tiers in order: nominal (the class
   inherits the mixin), structural (the dataclass has the fields), schema (the
   `TypeSchema` declares the fields). Today only the first is exercised.
2. **Protocols as KB entries or as Python classes.** ADR-0014: "Protocols are
   KB entries ... searchable, documented". Code: classes.
3. **What satisfaction means.** ADR-0014: fields and behaviour. Code: field
   names only. Behaviour (workflow, claim, rollup) is implemented in services
   and hooks and is not tied to the check.
4. **Names.** ADR-0014's primitives (`has_status`) against the code's
   (`statusable`).
5. **Serialisation.** ADR-0017 puts `_x_to_frontmatter` on each mixin. That is
   the part decision B (ADR-0042) changes.

### What the 37 extension entry classes do (preliminary)

By an AST walk of `extensions/*/src` (journalism-investigation 11, cascade 10,
software-kb 10, encyclopedia 2, social 2, zettelkasten 2):

- All 37 define `entry_type`, `to_frontmatter` and `from_frontmatter`.
- 6 define `FRONTMATTER_ALIASES` (core defines 2 more). It is a `frozenset` of
  alias names with no target, and it is in neither ADR-0040's inventory nor
  its contract table. Spike 2 (2026-10-02, 28,041 files across 52 real KBs)
  ran into it: alias resolution (ADR-0042 decision 6) needed a hand-written
  alias-to-target map, because no class says where `participants` goes.
- 35 contain branches inside a `to_frontmatter` or `from_frontmatter`. The
  branches in `to_frontmatter` are presumed to be "emit only when not the
  default"; those in `from_frontmatter` are `try` blocks around coercions. Not
  yet read.
- One class (journalism-investigation `ClaimEntry`) defines methods beyond
  fields: `valid_transitions`, `can_transition_to` and `auto_confidence`.

So far the classes look like structure plus serialisation. What they do beyond
that is not yet known (section "What must be learned first").

## The model: what a protocol is

A protocol has up to four parts:

1. **A data contract:** fields in the file (`status`, `assignee`; `location`,
   `coordinates`).
2. **Derived information and derived operations** that the platform computes
   from those fields through the index and exposes on reads and queries. They
   extend what the file says, and they never write to it.
3. **Explicit operations** a caller asks for (claim).
4. A protocol may also **validate**: refuse a write (`workflow`).

**A protocol operation never writes a file as a side effect of another write.**
This is ADR-0014's "platform-backed behavior", with the operations sorted into
derived, requested and refusing.

Worked examples. The first four are the pattern already working in code.

| Protocol | Data contract | Derived information and operations | Explicit operation | Refusal |
|---|---|---|---|---|
| **Locatable** (exists) | `location`, `coordinates` | `find_by_location` (`storage/queries.py:462`, `kb_find_by_location`); promoted index columns (ADR-0026; protocol columns, migration v9) | | |
| **Assignable** (exists) | `assignee`, `assigned_at` | `find_by_assignee` (`queries.py:352`) | claim (`claim_entry`, ADR-0014's `atomic_claim`) | |
| **Temporal** (exists) | `date`, `start_date`, `end_date`, `due_date` | `find_overdue` (`queries.py:387`) | | |
| **Statusable** (exists) | `status` | `find_by_status` (`queries.py:427`) | | transitions, from a schema `state_machine` (ADR-0014's `workflow`) |
| **Parentable** (exists) | `parent` | `children`, progress, "all children resolved". **Replaces `_parent_rollup`.** `sw_epics` already computes epic progress at query time | | |
| A dependency protocol (candidate, no name) | `dependencies` (a metadata key on tasks today) | blocked or ready, computed from the dependencies. **Replaces `unblock_dependents`** (no caller in `pyrite/`); `task_blocked_by` and `task_critical_path` are the read tools | | |
| An evidence protocol (candidate; ADR-0014's `has_evidence` and `source_chain`) | `evidence`, `confidence` | subtree evidence, computed from the children. **Replaces `aggregate_evidence_to_parent`** (no caller outside tests) | | links resolve (QA's `source_chain`) |
| References (fields the schema declares as references: `object-ref`, `actors`, connection fields) | the field | link rows, derived. **Replaces cascade's `resolve_actor_links` and journalism's `enrich_connection_links`** | | |

**Where derived values are surfaced, and how.** In read results (`get`, the
`task_*` tools, list and search rows), in queries (the `find_*` filters, and a
filter such as "ready"), and in `orient`. They are **labelled as derived**:
returned under their own key beside the file's fields (the spelling is
illustrative), never inside the file's frontmatter as the reading returns it,
so a caller does not echo them back as fields. An operation that names a
derived key is refused with a message saying so (ADR-0042 decision 11).

Spike 2 measured what happens without it: sending the type's reading
(`to_frontmatter()` with its defaults and normalised values) back through the
write path wrote 1,295 of 2,826 real files (46%), adding keys such as
`verification_status`, `importance` and `research_status`. Defaults, normalised
values and derived links are one rule: anything the type or the index computes
comes back under a derived key, or an echo writes it into the file.

## Decision

**1. An entry type is a type.** It gives structure to data (fields, required
and optional, enums and patterns, location template, version) and, through
the protocols it satisfies, an extensible behavioural interface. This is
ADR-0014, unchanged: structural protocols, small primitives that compose,
platform-backed operations, and schema-as-implementation.

**2. A protocol is the model above:** a data contract; derived information and
derived operations; explicit operations a caller asks for; and optionally
refusals. No protocol operation writes a file as a side effect of another
write.

**3. Derived values are computed through the index, exposed on reads, queries
and `orient`, and labelled as derived.** They are never written into a file
and are never accepted back as fields.

**4. Plugins contribute types, protocols, derived operations, explicit
operations, validators, tools and CLI commands.** A type may be defined by a
`kb.yaml` schema alone or by a plugin; either gets the platform behaviour its
schema and protocols entitle it to.

**5. Satisfaction of a protocol is structural and checked against the type's
schema.** The schema tier of `check_protocol_satisfaction` is the authority.
The nominal tier (inheriting a mixin) is a convenience for in-Python typing and
never required (**question 1**).

**Decided by the maintainer, 2026-10-03** (was question 2, names): the protocols keep the code's names
(`statusable`, `assignable`, ...), because they are already in `kb.yaml` files;
ADR-0014's bundle names (`claimable`) are bundles of them. A dependency
protocol and an evidence protocol are defined now, because they replace two
dead writers. References stays a candidate.

**Decided by the maintainer, 2026-10-03** (was question 4, `to_frontmatter`): it stays in the alpha
contract as a create and index helper, and is retired when schema-driven emit
matches for all 37 classes.

**6. Serialisation of an existing file is not part of a type's interface.**
No class's `to_frontmatter`, and no protocol mixin's `_x_to_frontmatter`,
decides a byte of an existing file (ADR-0042 decisions 1 and 2). A type's
reading of a file (`from_frontmatter`) validates the result of an operation and
supplies what the index derives. Create is the one place a type's emit writes a
whole file.

**7. Decided (maintainer, 2026-10-02): rollup, unblock and evidence
aggregation are derived.** These are the operations that wrote other entries as
a side effect of a write:

- `_parent_rollup` (`task_service.py:1016`; `rollup_parent`, :594): the core
  `after_save` hook, registered for every KB, deciding from the index, writing
  the parent's `status: done`, cascading to the grandparent, and swallowing
  failures as warnings. Replaced by the derived completion of Parentable.
- `unblock_dependents` (`task_service.py:643`): moves blocked tasks whose
  dependencies are resolved to `in_progress`. It has no caller in `pyrite/` or
  `extensions/`, only tests. Replaced by derived blocked and ready.
- `aggregate_evidence_to_parent` (`task_service.py:684`): copies a child's
  evidence into the parent's `evidence`. No caller outside tests. Replaced by
  derived subtree evidence.

A parent's `status` means what a person or an explicit operation set. Files
that already say `done` because the hook wrote it stay as they are.

**What spike 2 found, and what it makes required.** Of the writers in this
decision, only `_parent_rollup` left values in real files: 37 parent tasks in
the research KB (83 parents) are `done` with no `status_change_log` entry and
an `updated_at` within 5 s of their last child (a timestamp check, not git
history). `unblock_dependents` and `aggregate_evidence_to_parent` left 0. If the
rollup stops writing and nothing replaces it, decomposed research tasks stay
open after their children finish; a worker could claim one and the
investigation conductor's drain check would count it. So:

- **The derived completion of Parentable lands with the change that stops the
  rollup writing, not after.** Acceptance: *`task list` shows a parent's
  derived completion, and its open filter (`--status open --parent <epic>`)
  honours it.* Its consumers are `task list`, the investigation conductor's
  drain check and `task decompose`; all three read the derived value (ADR-0042
  decision 4 and its phasing, step 6).
- **The References derivation is a new capability.** The link hooks it
  replaces left no values on the reference corpus: 0 `actor_reference` links in
  the 3 cascade-typed KBs, 0 enriched connection links in the journalism KB.
  Nothing reads the `actor_reference` relation by name; generic backlinks and
  the graph are unaffected if the hooks stop writing.
- **Cross-KB actor resolution.** Cascade's lookup is same-KB only, and
  re-running it derives nothing for 6,484 events whose actors live in another
  KB (and 8 links for 3 scenes). The References derivation either resolves
  `actors` across KBs, using the one KB registry (ADR-0039), or states in its
  contract that it does not. **Decided by the maintainer, 2026-10-03** (was
  question 5): it resolves actors across the KBs the caller can read.

**8. Aliases and migrations are declared by the schema, not by class
attributes.** A type's schema names each alias and its target
(`participants: actors`) and each version step. The platform resolves an alias
to the key a file uses (ADR-0042 decision 6) and applies migrations only by
explicit command (decision 7). A plugin that is removed takes no migration
with it, because the migrations were never in its code.

**Aliases are required before the write path lands; migrations are not on its
critical path.** Spike 2 found `FRONTMATTER_ALIASES` is a set of names with no
target, so ADR-0042's alias rule (an edit of `actors` writes the file's own
`participants:` line; 95 such appends were written correctly in the spike, with
a hand-written map) cannot be implemented from the class attributes. The alias
declaration is therefore a precondition of ADR-0042 phase 4, not an
improvement. Migrations carry less evidence: 0 of 365 types on the maintainer's
52 KBs declare a `version`, no plugin registers a migration, no file carries
`_schema_version`. The "behind schema version" report and the explicit
migrate command stay, and nothing in acceptance waits on them; they can follow
the aliases.

**9. A before-save hook refuses; it does not change the entry.** Its return
value is ignored. A default that depends on the acting principal (the social
plugin's `author_id` on create) is declared in the schema as a create-time
default. Hook and plugin identity is injected by the dispatcher from the
session principal (ADR-0043 decision 7).

**10. This is a direction for the alpha, not a freeze.** ADR-0040's contract is
alpha (roadmap, 2026-10-02). These decisions are meant to settle before the
contract freezes, and the inventory below may revise them.

## What changes in ADR-0014

ADR-0014 is accepted. This ADR amends these cells and sentences, once accepted:

- **Primitive protocols**, `rollup`: "After-save sibling check + parent update"
  / "Platform provides auto-rollup" becomes "a derived value: completion
  computed from children, through the index" / "Platform computes it, and
  writes nothing". The `decomposable` bundle (`has_parent` + `rollup`) keeps its
  name and parts; `rollup` in it means the derived value.
- **Core principles**, "Platform-backed behavior": gains the sentence "A
  protocol operation is derived, requested or refusing; none writes a file as a
  side effect of another write."
- Nothing else in ADR-0014 changes. Its phases 1 and 2 are not amended;
  whether they are wanted is question 1 and the inventory's last item.

## What changes in ADR-0040

ADR-0040 is accepted. This ADR amends these sentences, once accepted:

- **Section 2, Models row**: "`Entry` with `entry_type`, `to_frontmatter`,
  `from_frontmatter`, and `base_kwargs` / `base_frontmatter` (renamed from
  `_base_kwargs` / `_base_frontmatter` ...)". `to_frontmatter` and
  `base_frontmatter` are described as create and index helpers that do not
  decide the bytes of an existing file, and `base_frontmatter` is not
  promoted to a public name until question 4 is answered. The inventory counts
  the private helpers at 37 call sites of `_base_kwargs` and 11 of
  `_base_frontmatter`.
- **Section 6, check 3**: "Every entry type survives the round trip
  `to_frontmatter -> from_frontmatter -> to_frontmatter` unchanged, and every
  preset names only registered types." The round trip is not the property
  writes need: it says nothing about the reading being lossless, and writes
  no longer depend on it. It is replaced by: every protocol a type declares is
  satisfied by its schema; every alias names a target; a type's reading does
  not depend on index state.
- **Section 6, check 2**, hooks bind `(entry, ctx)`: adds that the return value
  is ignored and a `before_save` hook may only raise.
- **Section 3, Types**: a type's schema carries its aliases, migrations,
  create-time defaults and the fields the index derives references from.
- **Section 3, Storage**: "Every entry it creates is created through
  `ctx.kb_service`" gains "and every entry it changes".
- **The inventory** gains `FRONTMATTER_ALIASES` (6 extension classes) and the
  private `Entry` helpers' replacement.
- **Sequencing**: the contract freeze is 0.28 in the roadmap's release line
  (ADR-0040's text still says 0.27). This ADR's inventory precedes it.

## What must be learned first

A spike, before the contract is frozen and before ADR-0040's conformance kit
is written. Its question: **for each of the 37 extension classes and the core
classes, what does the class do that a `kb.yaml` schema plus the protocols it
satisfies cannot?**

Classify every statement in `to_frontmatter`, `from_frontmatter` and every
method as: a field declaration; default suppression; a coercion or validation;
a value derived from other fields; an alias; behaviour across entries. Then:

1. Which classes reduce to a schema exactly (the same index rows and the same
   validation, shown by a parity test against a `kb.yaml` rebuild of the type)?
2. What cannot be expressed as schema (for example `ClaimEntry.auto_confidence`
   and its transitions), and what protocol or platform operation would carry it?
3. What does `pyrite protocol check` report for each type under each tier, and
   which declared behaviour has no operation behind it?
4. Which of ADR-0014's phases 1 and 2 are wanted (protocol entries,
   `requires_protocols`) now that the alpha supports a small surface (the
   supported-surface list).

## Acceptance

Doc-driven. The passage is `docs/adding-a-type.md`, and
`tests/test_doc_adding_a_type.py` runs it. Output spellings are illustrative
(the CLI contract is #303).

> **Adding a type.** A type is a schema in your KB's `kb.yaml`. Name the
> protocols it satisfies and Pyrite gives it their behaviour. Your files keep
> their bytes.

```yaml
types:
  lead:
    protocols: [assignable, statusable]
    fields:
      status: {type: select, values: [open, claimed, done]}
      assignee: {type: text}
      participants: {type: list, alias_of: actors}   # spelling: question 3
```
```
$ pyrite protocol check -k notes -t lead
  ok  lead satisfies 'statusable' (schema)
  ok  lead satisfies 'assignable' (schema)
```

An update to `status` on a `lead` that skips a transition is refused, and a
claim on an open `lead` succeeds exactly once when two agents race. Editing
`actors` on a file that carries `participants:` changes the `participants:`
line and no other.

Added by spike 2 (ADR-0042): the alias declaration above is what makes the last
sentence true, so its test runs on a file whose alias has no hand-written map
anywhere else. A parent whose children are all resolved shows derived
completion in `task list`, and `task list --status open --parent <epic>` does
not list it, with the parent's file unchanged. The References derivation's
contract states whether it resolves an actor in another KB, and a test shows it.

## Consequences

**Easier**
- A type needs no Python to be a type. The 37 classes become schemas where the
  inventory says they can.
- A plugin author reads one document (ADR-0014) and writes a schema and,
  where needed, an operation.
- Writes never depend on a class's serialiser (ADR-0042).

**Harder**
- Two accepted ADRs have to be reconciled (question 1), and the code has to
  move toward ADR-0014 where ADR-0017's mixins diverge from it.
- Schema grows: aliases, migrations, create-time defaults and reference fields.
  Those are new schema keys with their own validation.
- Some class behaviour may not fit a schema (`auto_confidence`). It needs a
  protocol operation or stays in the plugin as a tool.
- Workflows that relied on a parent being completed for them (`task decompose`,
  the conductors) read the derived value or set the status explicitly. The
  derived completion must exist and be honoured by `task list --status open`
  before the rollup stops writing (decision 7).

**Deleted, when the inventory allows**
- `FRONTMATTER_ALIASES` as a class attribute; per-class `to_frontmatter`
  default-suppression; the mixins' `_x_to_frontmatter` methods.

## Alternatives considered

- **Types are data, not classes** (the first phrasing of this decision).
  Rejected by the maintainer's correction: a type provides structure and an
  extensible protocol interface, which ADR-0014 already decides.
- **Keep per-class serialisation and make it correct.** Rejected: it is the
  source of the defects ADR-0042 lists (the model's after-value deleting what
  the model does not hold), and every extension would have to be correct.
- **Nominal protocols only (ADR-0017 as written).** Rejected: it contradicts
  ADR-0014 and cannot cover `kb.yaml`-only types without the schema tier.
- **Protocols as KB entries only (ADR-0014 phase 1 as written).** Not decided:
  question 1.

## Questions for the maintainer

None open. Question 1 was answered on 2026-10-02 and questions 2 to 5 on
2026-10-03; the answers are recorded in the decisions named after each. The
text is kept below for its reasoning.

**Decided by the maintainer, 2026-10-02** (the item is kept below for its
reasoning): question 1, ADR-0014 governs. Protocols are structural and the
schema is the authority, so a type defined only in `kb.yaml` is a full
citizen; the mixins stay as in-Python conveniences.

1. **Reconciling ADR-0014 and ADR-0017. DECIDED 2026-10-02.** Does ADR-0014 govern (structural;
   the schema tier is the authority; protocols defined as data and documented
   as `protocol` entries; mixins kept as in-Python conveniences), or ADR-0017
   (mixins by inheritance first)? *Recommended: ADR-0014 governs.* Blocks the
   conformance kit (check 3).
2. **Names, and which candidates to define. DECIDED 2026-10-03** (before decision 6). ADR-0014's primitives
   (`has_status`, `workflow`, `atomic_claim`) and bundles (`claimable`), or the
   code's (`statusable`, `assignable`)? And which candidate protocols are
   wanted now: a dependency protocol, an evidence protocol, references?
   *Recommended: the code's names, because they are already in `kb.yaml`
   files, with ADR-0014's bundle names as bundles of them; define dependency
   and evidence now, because they replace two dead writers.*
3. **Aliases and migrations in the schema before the freeze. DECIDED (aliases required before the write path lands; decision 8)**, with
   `FRONTMATTER_ALIASES` kept as a shim for one minor release. *Recommended:
   yes.* Blocks the alpha contract (0.28). Spike 2 splits it: **aliases block
   ADR-0042's write path** (decision 8), so they come first; migrations are
   inert on the maintainer's corpus and may follow. The shim has no targets to
   carry, so it can only warn.
4. **`to_frontmatter` in the contract. DECIDED 2026-10-03** (before decision 6). Keep it for the alpha as a create and
   index helper, and retire it when the inventory shows schema-driven emit
   matches for all 37 classes? *Recommended: yes.* Blocks the same.

5. **Does the References derivation resolve actors across KBs? DECIDED 2026-10-03** (decision 7, cross-KB paragraph). Cascade's
   hook was same-KB only and derives nothing for 6,484 real events. Resolve
   across the registry's KBs (ADR-0039), or state that it does not and say
   which relation a reader should use for an actor in another KB?
   *Recommended: state the scope in the derivation's contract; do not widen it
   until a consumer reads the relation* (none does today). Blocks ADR-0042
   step 6 only if a consumer is found.

Decided and recorded, not asked: rollup, unblock and evidence aggregation are
derived (decision 7); a protocol operation never writes as a side effect
(decision 2); derived values are labelled and not echoed back (decision 3).
