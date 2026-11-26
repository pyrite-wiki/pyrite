---
id: epic-normalization-and-data-cleanup
title: "Epic: KB normalization, data health, and cascade plugin deprecation"
type: backlog_item
tags: [data-quality, schema, cascade, journalism-investigation, robustness, deprecation]
links:
- target: warn-on-undeclared-entry-type
  relation: has_subtask
  kb: pyrite
- target: warn-on-missing-type-fallback
  relation: has_subtask
  kb: pyrite
- target: schema-required-field-validation
  relation: has_subtask
  kb: pyrite
- target: pyrite-kb-validate-command
  relation: has_subtask
  kb: pyrite
- target: ji-absorb-cascade-types
  relation: has_subtask
  kb: pyrite
- target: ji-absorb-cascade-cli
  relation: has_subtask
  kb: pyrite
- target: ji-absorb-cascade-mcp
  relation: has_subtask
  kb: pyrite
- target: migrate-cascade-kbs-to-investigation
  relation: has_subtask
  kb: pyrite
- target: remove-cascade-plugin
  relation: has_subtask
  kb: pyrite
- target: rename-investigation-event-to-timeline-event
  relation: has_subtask
  kb: pyrite
importance: 5
kind: epic
status: retired
priority: high
effort: XL
rank: 1600
lifecycle: archived
---

## Problem

Today's cascade-timeline deploy surfaced a pattern of silent data drift and
weak schema enforcement:

- 426 entries had drifted from the canonical type (missing `type:`, or using
  `event` / `political` / `legislative` instead of `timeline_event`). The
  viewer exporter hardcoded `timeline_event`, so all 426 were silently
  dropped from capturecascade.org.
- Pyrite defaults missing-type entries to `note` with no warning. The `note`
  type has no `date` field, so events disappeared from date-sorted views.
- `kb.yaml` in cascade-timeline declared `event` as the canonical type while
  everything else (migration script, exporter, 99% of files) assumed
  `timeline_event` — schema and reality diverged with no mechanism to detect.
- Two separate event types exist across the ecosystem: `timeline_event`
  (cascade plugin) and `investigation_event` (JI plugin). The cascade
  plugin's `TimelineEventEntry` already inherits from JI's
  `InvestigationEventEntry` — the split is ceremony without value.
- `pyrite index build` / `index health` never warned about any of this.

Beyond the timeline drift, the `cascade` plugin itself has become redundant.
Most of its entry types are thin wrappers over journalism-investigation (JI)
types. Its CLI, MCP tools, hooks, validators, and relationship types all
describe *investigative-journalism* concepts — not something cascade-specific.
Keeping a parallel plugin is how these silent-divergence bugs keep
appearing.

## Goals

1. **Harden** schema enforcement at index time so silent drift can't recur.
2. **Consolidate** cascade plugin functionality into the journalism-investigation
   plugin — one canonical home for investigation types, CLI, MCP, hooks,
   relationships.
3. **Migrate** the cascade KBs (cascade-timeline, cascade-research,
   cascade-solidarity) to plain investigation KBs with `kb_type: investigation`.
4. **Remove** the cascade plugin entirely after a deprecation cycle.
5. **Clean up** cascade-timeline repo hygiene (remove `.md.bak` files,
   consistent layout, checked-in normalization script).

## Phases

### Phase 0 — Pyrite robustness (diagnostic baseline)

Ship the health checks first. They produce the work list for later phases
and prevent silent mid-migration drift.

- [[warn-on-undeclared-entry-type]] — warning at index time when an entry's
  `type` is not declared in `kb.yaml`, surfaced via `pyrite index health`
- [[warn-on-missing-type-fallback]] — warning when `entry_from_frontmatter`
  falls back to `note` because `type:` was absent
- [[schema-required-field-validation]] — on index, verify entries have the
  `required:` fields declared in their type schema, and warn on
  subdirectory-placement mismatches (absorbs the retired
  `clarify-subdirectory-handling` scope; `subdirectory:` stays a writer
  hint, not a reader constraint)
- [[pyrite-kb-validate-command]] — `pyrite kb validate` CLI that runs all
  of the above on demand with JSON output

### Phase 1 — JI plugin absorbs cascade

Move cascade functionality into journalism-investigation. Keep class names
stable where it helps; rename where it removes ambiguity.

- [[ji-absorb-cascade-types]] — move `actor`, `theme`, `victim`,
  `statistic`, `mechanism`, `scene`, `solidarity_event` entry types, the
  9 cascade relationship types, validators, and hooks into the JI plugin.
  Drop `cascade_org` and `cascade_event` (duplicates of JI's base types).
- [[ji-absorb-cascade-cli]] — move `extract-actors`, `suggest-aliases`,
  and `export` commands under `pyrite ji …`. The timeline-JSON export
  stays functional but lives in JI.
- [[ji-absorb-cascade-mcp]] — move cascade MCP tools into JI, strip the
  `cascade_` prefix, generalize metadata filters, delete the
  `cascade_network` duplicate.

### Phase 2 — Migrate cascade KBs

- [[migrate-cascade-kbs-to-investigation]] — update `kb.yaml` in
  cascade-timeline / cascade-research / cascade-solidarity to
  `kb_type: investigation` with the consolidated schema. Normalize
  cascade-timeline repo layout (remove `.md.bak`, reconcile `events/`
  subdirectory, canonical field order, checked-in normalize script).
  Ship a `pyrite kb migrate --from-cascade` rewriter so out-of-tree KBs
  can upgrade.

### Phase 3 — Remove the cascade plugin

- [[remove-cascade-plugin]] — after one release cycle with deprecation
  warnings, delete `extensions/cascade/`. Release-note the plugin
  removal and the migration path.
- [[rename-investigation-event-to-timeline-event]] — inside JI, rename
  `investigation_event` → `timeline_event` now that there's no
  cross-plugin coordination required. Users' timelines are timeline
  events; JI owns the type.

## Parent strategy

This epic sits under [[epic-pyrite-publication-strategy]], which
establishes the two-surfaces model: **static publishing sites** (Hugo
consumers of pyrite data) and a **hosted Pyrite investigation
instance** (`investigate.transparencycascade.org`). Decisions below
reflect that framing.

## Resolved decisions

1. **Static-export home → JI, generic shape only.** `static_export.py`
   moves into JI as `pyrite ji export-timeline`, producing a **generic
   timeline-export JSON** documented as a public contract.
   Site-specific shaping (capturecascade React viewer, future Hugo
   templates) lives in **site repos**, not in pyrite. No `--format`
   flag — one canonical output. Sites adapt via their own build
   pipelines, the way `detention-pipeline` already does.
2. **Migration strategy → rewrite, no permanent shim.** Ship
   `pyrite kb migrate --from-cascade` for out-of-tree KBs. Inside the
   monorepo, cascade stays as a deprecation shim for exactly one
   release (imports re-exported from JI, `DeprecationWarning` on
   invocation) and is then deleted in Phase 3. Do not silently translate
   old `type:` strings at read time — the whole point of this epic is
   to surface drift, not hide it.
3. **Class naming inside JI → no prefix.** `ActorEntry`,
   `TimelineEventEntry`, `ThemeEntry`, etc. The package path
   (`pyrite_journalism_investigation.entry_types`) disambiguates.
   Prefixing with `Ji…` would suggest a non-JI version exists, which is
   exactly what we're deleting.

## Recommended order

Phase 0 → Phase 1 → Phase 2 → Phase 3.

Rationale: Phase 0's warnings produce the scope for everything after.
Phase 1 lands JI as the canonical home before any KB migrations so
migrated KBs land on stable types. Phase 2 migrates data. Phase 3
deletes cascade once no one depends on it and adds the final
`timeline_event` rename inside JI.

## Related work already shipped

- Frontmatter delimiter fix (`repository.py`): `---` no longer matches inside
  quoted values
- Wikilink extractor fix (`index.py`): code fences and path-like targets no
  longer count as broken links
- Cascade viewer header labels corrected; viewer `cache: 'no-cache'` for
  fresh data on reseed
- 426 cascade-timeline entries normalized to `type: timeline_event`
