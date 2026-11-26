---
id: ji-absorb-cascade-types
title: "Move cascade entry types, relationships, validators, and hooks into JI plugin"
type: backlog_item
tags: [schema, journalism-investigation, cascade, deprecation]
links:
- target: epic-normalization-and-data-cleanup
  relation: subtask_of
  kb: pyrite
importance: 5
kind: feature
status: retired
priority: low
effort: M
rank: 100
---

> **Retired 2026-07-04** — written from the product reading of JI
> (make JI the canonical schema home for journalism types). The
> spike reframe inverts the direction: cascade owns the production
> types; JI keeps only the load-bearing base class until its fields
> migrate to cascade or core EventEntry. See [[ji-spike-findings]].

## Scope

Move these from `extensions/cascade/` into
`extensions/journalism-investigation/`:

### Entry types (7 kept, 2 dropped)

Keep and move. Class names stay un-prefixed (decision resolved in epic:
no `Ji…` prefix — JI is the canonical home, the package path
disambiguates):

- `actor` (`ActorEntry`) — extends `PersonEntry`, adds `tier`, `era`,
  `capture_lanes`, `chapters`
- `theme` (`ThemeEntry`) — extends `TopicEntry`
- `victim` (`VictimEntry`) — extends `Entry` + `Locatable`
- `statistic` (`StatisticEntry`)
- `mechanism` (`MechanismEntry`)
- `scene` (`SceneEntry`)
- `solidarity_event` (`SolidarityEventEntry`) — extends `EventEntry`

The cascade plugin's `TimelineEventEntry` already inherits from JI's
`InvestigationEventEntry`. Once cascade is gone, JI's class *is* the
canonical timeline event. The type-string rename lands in a later ticket
([[rename-investigation-event-to-timeline-event]]).

Drop (redundant):

- `cascade_org` — just `OrganizationEntry` with `tier`, `capture_lanes`,
  `chapters`. **Resolved (see Resolved decisions §1):** JI today has no
  registered `organization` *type* (it uses an `entity_type` discriminator
  on a generic entity model). Phase 1 adds `tier` as a first-class optional
  field on the canonical `organization` type (cross-investigation rank
  concept, parallels `tier` on `actor`); `capture_lanes` and `chapters`
  go into the `metadata` bag (project-specific). Existing `cascade_org`
  entries get rewritten to `type: organization` in Phase 2.
- `cascade_event` — thin wrapper over `EventEntry`, unused (all real
  events are `timeline_event`). Delete; migrate any existing
  entries to `timeline_event` during Phase 2.

### Relationship types (6 pairs moved, 1 pair dropped)

Move these from `CascadePlugin.get_relationship_types()` to
`JournalismInvestigationPlugin.get_relationship_types()`:

`member_of` / `has_member`, `investigated` / `investigated_by`,
`capture_mechanism` / `enabled_capture`,
`built_on` / `enabled`, `responded_to` / `provoked_response`,
`actor_reference` / `has_actor`.

**Verified:** JI's 17 existing relationship types and cascade's 14 have
**zero name collisions** (computed against source — JI uses FTM/financial-
graph vocabulary like `transacted_with` / `received_transaction_from`;
cascade has none of those names). The collision-check listed in the
original ticket was based on a wrong guess.

**Dropped — `funded_by` / `funds`:** this duplicates JI's existing
`received_transaction_from` / `transacted_with` semantically, just
without the transaction-detail layer. Resolved decision: keep one
canonical funding vocabulary; cascade-KB entries with `funded_by` /
`funds` links get rewritten in Phase 2 to
`received_transaction_from` / `transacted_with` with `amount: "unknown"`
(the marker preserves "this is a funding relation we know about but
have no amount for" — distinct from `null` which would read as
"explicitly absent"). See Resolved decisions §2.

Net: JI's relationship-type registry grows from 17 → **29** after the
merge (17 JI + 14 cascade − 2 dropped funding entries).

### Validators

Move `_validate_cascade_entry` from
`extensions/cascade/src/pyrite_cascade/plugin.py` to JI's validators
module. Rename to reflect the new home (e.g., `_validate_actor_entry`,
`_validate_solidarity_event`), or fold the checks into existing JI
validators where the types already overlap.

**Signature reconciliation (resolved):** cascade's validator has the
old 1-arg shape `_validate_cascade_entry(entry)`; JI's convention
(invoked by `kb_schema.py:388` and the index-health status check) is
the 3-arg `validator(entry_type, data, context) -> list[dict]`. Port
to the 3-arg shape on move rather than relying on the `TypeError`
fallback path — keeps the validator surface uniform and avoids the
fallback's silent-skip-on-other-errors behavior. Returned error dicts
follow the `{"field", "rule", "expected", "got"}` shape so the
existing `invalid_statuses` health check picks them up automatically.

### Hooks

Move `resolve_actor_links` (before_save) and `_on_actor_saved`
(after_save) from `extensions/cascade/src/pyrite_cascade/hooks.py` to
JI's hooks module. Also move the actor lookup cache
(`_actor_cache`, `invalidate_actor_cache`) and alias-loading helper.

The hook currently triggers on `timeline_event`, `solidarity_event`,
`scene` — keep that trigger set.

**Ordering with JI's existing `before_save` hook:** JI already registers
`before_save: [enrich_connection_links]`. Post-merge it becomes
`[enrich_connection_links, resolve_actor_links]` — actor link resolution
runs **after** connection enrichment. This is the right order:
`enrich_connection_links` reconciles relationship links from frontmatter,
and `resolve_actor_links` then adds `actor_reference` links based on the
`actors` field. Reversing the order would mean actor refs land before
connection enrichment sees them, missing potential
relationship-resolution wins. Document the ordering in the registration
site so a future hook addition doesn't reshuffle silently.

## Resolved decisions

These were left open in the original ticket and resolved during
deep-dive review (decisions locked; no need to re-litigate at
implementation time).

1. **`cascade_org` landing.** JI has no registered `organization` entry
   type today — it models entities with an `entity_type` discriminator.
   Phase 1 adds `tier` as a first-class optional field on the canonical
   `organization` type (cross-investigation concept; parallels `tier`
   on `actor`). `capture_lanes` and `chapters` go into the `metadata`
   bag — project-specific enough not to deserve top-level schema.
2. **Funding vocabulary.** Drop cascade's `funded_by` / `funds` rather
   than carry two ways to express funding. Phase 2 rewrites existing
   links to `received_transaction_from` / `transacted_with` with
   `amount: "unknown"` (semantic marker — distinct from `null`).
3. **Validator signature.** Port `_validate_cascade_entry(entry)` to
   JI's 3-arg `validator(entry_type, data, context)` shape on move.
   No reliance on the `TypeError` fallback.
4. **Hook ordering.** `before_save` becomes
   `[enrich_connection_links, resolve_actor_links]` — connection
   enrichment first, actor-reference resolution second.
5. **Dual event-type window.** During the one-release shim, JI
   registers both `investigation_event` (its own) and `timeline_event`
   (cascade's `TimelineEventEntry`, which subclasses it). Pyrite's
   `_resolve_entry_type` already prefers the subclass for shared core
   types, so this is safe — just don't *remove* either registration
   until Phase 3's rename ticket lands.

## TDD

Failing tests first, in JI's test suite:

1. `test_ji_provides_actor_entry_type` — `JournalismInvestigationPlugin().get_entry_types()["actor"] is ActorEntry`
2. `test_ji_provides_cascade_relationship_types` — `"capture_mechanism" in JournalismInvestigationPlugin().get_relationship_types()`
3. `test_resolve_actor_links_runs_on_timeline_event` — save a
   `timeline_event` with `actors: ["Donald Trump"]` in a KB that has a
   matching actor entry, assert `actor_reference` link is added
4. `test_funded_by_relationship_not_registered` — assert
   `"funded_by" not in JournalismInvestigationPlugin().get_relationship_types()`
   (locks the funding-vocabulary decision in code, not just docs).
5. `test_before_save_hook_order_is_enrich_then_actor_links` — assert
   `JournalismInvestigationPlugin().get_hooks()["before_save"]` is
   `[enrich_connection_links, resolve_actor_links]` in that order.
6. `test_organization_tier_field_round_trips` — create an
   `organization` entry with `tier: 1` and `metadata: {"capture_lanes":
   ["finance"], "chapters": [3]}`, save+reload, assert all three values
   survive the round-trip in their resolved homes.

## Changes

- `extensions/journalism-investigation/src/pyrite_journalism_investigation/entry_types.py` — import or move cascade classes
- `extensions/journalism-investigation/src/pyrite_journalism_investigation/plugin.py` — register new types, relationships, hooks, validators
- `extensions/journalism-investigation/src/pyrite_journalism_investigation/validators.py` — add cascade validators
- `extensions/journalism-investigation/src/pyrite_journalism_investigation/hooks.py` — add actor-resolution hooks
- `extensions/cascade/src/pyrite_cascade/plugin.py` — remove moved bits, keep the plugin as a thin shim importing from JI for one release (see [[remove-cascade-plugin]])

## Done when

- All 7 kept entry types, **6 kept relationship pairs** (12 directions),
  both hooks (in the documented order), and the ported validator land
  in JI.
- `tier` lives as a first-class optional field on the canonical
  `organization` type; `capture_lanes` and `chapters` slot into
  `metadata`.
- `funded_by` / `funds` are NOT registered on the JI side (locked by
  TDD case 4).
- JI tests covering the moved surface pass.
- Cascade plugin still loads and exposes the same types via re-export
  shim (full removal in Phase 3).
- Migration plans land in [[migrate-cascade-kbs-to-investigation]] for:
  `cascade_org` → `organization` (with field-folding per §1),
  `cascade_event` → `timeline_event`, and the funding-link rewrite
  with `amount: "unknown"` (per §2).

## Depends on

Phase 0 complete ([[warn-on-undeclared-entry-type]],
[[schema-required-field-validation]]) so any during-migration drift
surfaces in `index health`.

## Unblocks

[[ji-absorb-cascade-cli]], [[ji-absorb-cascade-mcp]],
[[migrate-cascade-kbs-to-investigation]]
