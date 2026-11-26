---
id: rename-investigation-event-to-timeline-event
title: "Rename investigation_event → timeline_event inside JI plugin"
type: backlog_item
tags: [schema, journalism-investigation, cleanup]
links:
- target: epic-normalization-and-data-cleanup
  relation: subtask_of
  kb: pyrite
importance: 5
kind: feature
status: retired
priority: medium
effort: S
rank: 400
lifecycle: archived
---

## Scope

After the cascade plugin is gone, JI is the sole owner of the timeline
event type. Drop the awkward `investigation_event` name in favor of
`timeline_event` — what users actually call these entries.

## Changes

- `extensions/journalism-investigation/src/pyrite_journalism_investigation/entry_types.py`
  - `InvestigationEventEntry.entry_type` returns `"timeline_event"`
  - Rename the class to `TimelineEventEntry` (keep `InvestigationEventEntry`
    as an alias for one release if any external code imports it)
  - Update `to_frontmatter()`'s hardcoded `"investigation_event"` string
- All JI internal references (validators, queries, export, plugin
  registrations, tests, presets, hooks) — update `investigation_event`
  → `timeline_event`
- Ship via `pyrite kb migrate --rename-type investigation_event=timeline_event`
  (add the flag if not already present from
  [[migrate-cascade-kbs-to-investigation]]) for any in-the-wild entries

## TDD

1. `test_timeline_event_is_the_canonical_type_string` —
   `TimelineEventEntry().entry_type == "timeline_event"`
2. `test_investigation_event_alias_still_imports` — old name imports
   without error, maps to `TimelineEventEntry`, emits `DeprecationWarning`
3. `test_migrate_renames_investigation_event_entries` — fixture entry
   with `type: investigation_event` becomes `type: timeline_event`

## Done when

- All 30 files currently referencing `investigation_event` are updated
  (or the reference is an intentional deprecation alias)
- JI tests pass
- At least one out-of-tree investigation KB used for testing
  (e.g., detention-pipeline-research) migrates cleanly

## Depends on

[[remove-cascade-plugin]] — no cross-plugin coordination blocks this
once cascade is gone.

## Replaces

The JI side of the retired [[consolidate-event-type-to-timeline-event]]
item. Migration-of-data lives in
[[migrate-cascade-kbs-to-investigation]].
