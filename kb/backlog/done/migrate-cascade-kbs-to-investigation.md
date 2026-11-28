---
id: migrate-cascade-kbs-to-investigation
title: "Migrate cascade KBs to investigation kb_type; normalize layout and schema"
type: backlog_item
tags: [schema, migration, cascade, journalism-investigation, data-quality]
links:
- target: epic-normalization-and-data-cleanup
  relation: subtask_of
  kb: pyrite
importance: 5
kind: feature
status: retired
priority: high
effort: M
rank: 600
lifecycle: archived
---

## Scope

Migrate these KBs from cascade-plugin-owned to plain investigation KBs:

- `~/cascade-kb/cascade-timeline/`
- `~/cascade-kb/cascade-research/`
- `~/cascade-kb/cascade-solidarity/`

Plus: repo hygiene for cascade-timeline, and a migration CLI for any
out-of-tree cascade KBs.

### kb.yaml migration

For each KB:

- Set `kb_type: investigation`
- Remove the ambiguous `event` type declaration; declare the consolidated
  `timeline_event` type with full field schema copied from JI's
  `InvestigationEventEntry` (or `TimelineEventEntry` after the rename in
  [[rename-investigation-event-to-timeline-event]])
- Declare `required:` lists so [[schema-required-field-validation]]
  catches missing fields
- Update `subdirectory:` declarations per [[clarify-subdirectory-handling]]

### Entry `type:` normalization

- Any entries with `type: event`, `type: political`, `type: legislative`,
  `type: cascade_event`, or missing `type:` that represent timeline
  events → rewrite to `type: timeline_event`
- Any entries with `type: cascade_org` → rewrite to `type: organization`,
  and split the cascade-specific fields per the resolved decisions in
  [[ji-absorb-cascade-types]]:
  - `tier` → kept as a first-class top-level field on `organization`
    (cross-investigation concept; JI adds it as an optional field on the
    canonical org type in Phase 1).
  - `capture_lanes` → moved into `metadata.capture_lanes` (project-
    specific taxonomy; not promoted to top-level schema).
  - `chapters` → moved into `metadata.chapters` (same rationale).

### Funding-link rewrite

Cascade KBs use `funded_by` / `funds` relationship links, dropped from
the merged registry in [[ji-absorb-cascade-types]] (semantically
duplicates JI's `received_transaction_from` / `transacted_with`).

For every link with `relation: funded_by` or `relation: funds` in the
cascade KBs:

- `relation: funded_by` → `relation: received_transaction_from`
- `relation: funds` → `relation: transacted_with`
- Add `amount: "unknown"` to the link's properties — semantic marker
  preserving "we know this funding relation exists but have no
  transaction amount." Distinct from `null` (which would read as
  "explicitly absent / not applicable") and from omitting the field
  (which on a real transaction means "amount unrecorded" — different
  state). The string `"unknown"` lets downstream queries and
  re-investigation passes find these and fill them in.

This rewrite happens in the same migrator pass as the type rewrites;
it must not run before `ji-absorb-cascade-types` lands or the migrated
KB will index against a registry that doesn't have the new relation
names yet.

### cascade-timeline repo hygiene

From the retired `normalize-cascade-timeline-files` item:

1. Remove all `.md.bak` files (15 as of the last audit)
2. Reconcile `cascade-timeline/events/` subdirectory (25 entries) —
   merge into the root with date-prefix filenames (the dominant pattern).
   Update `kb.yaml` to reflect a single root layout.
3. Canonicalize frontmatter field order:
   `type → id → date → title → importance → status → tags → actors →
   sources → body`
4. Check in `scripts/normalize.py` (the one-shot used during today's
   fix) so drift can be caught repeatably.

### Migration CLI for out-of-tree KBs

Add `pyrite kb migrate --from-cascade -k <kb-name>` that:

- Rewrites `type:` frontmatter per the mapping above
- Moves cascade-specific fields into the new schema locations
- Prints a summary of changes (dry-run by default; `--apply` executes)
- Emits a report compatible with `pyrite kb validate` output

This is the path for any user with a cascade-using KB outside the
monorepo (detention-pipeline-research, journalists.pyrite.wiki samples,
community KBs).

## TDD

1. `test_migrate_from_cascade_rewrites_event_types` — fixture KB with
   `type: event` entry, run migrator, assert `type: timeline_event` in
   the rewritten file.
2. `test_migrate_cascade_org_splits_fields` — a `cascade_org` entry
   with `tier: 2`, `capture_lanes: ["finance"]`, `chapters: [3]`
   migrates to `type: organization` with **`tier: 2` at the top level**
   and **`metadata.capture_lanes: ["finance"]`** + **`metadata.chapters:
   [3]`**. Asserts the field-split decision, not just "preserved
   somewhere."
3. `test_migrate_rewrites_funded_by_to_received_transaction_from` — a
   link `{target: X, relation: funded_by}` becomes
   `{target: X, relation: received_transaction_from, amount: "unknown"}`.
   Same for `funds` → `transacted_with`.
4. `test_cascade_timeline_kb_passes_kb_validate` — after migration,
   `pyrite kb validate -k cascade-timeline` exits 0.

## Changes

- `pyrite/cli/kb_commands.py` (or equivalent) — `pyrite kb migrate` command
- `pyrite/services/migration_service.py` (new) — migration logic, type-map, field-remap
- `~/cascade-kb/cascade-timeline/kb.yaml` — rewritten
- `~/cascade-kb/cascade-timeline/scripts/normalize.py` — checked in
- Same treatment for cascade-research and cascade-solidarity kb.yaml

## Done when

- `pyrite kb validate -k cascade-timeline` exits 0 (no undeclared types,
  no missing required fields, no orphan entries)
- Same for cascade-research and cascade-solidarity
- No entries remain with `type: cascade_org` or `type: cascade_event`;
  surviving `cascade_org` data has `tier` at the top level and
  `capture_lanes`/`chapters` under `metadata`.
- No links remain with `relation: funded_by` or `relation: funds`;
  rewritten links carry `amount: "unknown"`.
- `capturecascade.org` deploys cleanly from the migrated
  cascade-timeline (no entries dropped from the viewer)
- `pyrite kb migrate --from-cascade` passes tests

## Depends on

[[warn-on-undeclared-entry-type]], [[schema-required-field-validation]],
[[pyrite-kb-validate-command]] (all Phase 0 — used as the
done-criterion), [[ji-absorb-cascade-types]] (types must already be
available in JI)

## Unblocks

[[remove-cascade-plugin]]

## Replaces

Absorbs [[normalize-cascade-timeline-files]] and
[[consolidate-event-type-to-timeline-event]].
