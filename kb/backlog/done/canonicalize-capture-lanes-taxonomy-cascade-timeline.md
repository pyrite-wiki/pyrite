---
id: canonicalize-capture-lanes-taxonomy-cascade-timeline
title: "Canonicalize `capture_lanes` taxonomy in cascade-timeline (17 canonical lanes, fix drift, enforce enum)"
type: backlog_item
tags: [cascade-timeline, schema, taxonomy, data-quality, enum-enforcement]
importance: 5
kind: cleanup
status: retired
priority: medium
effort: M
rank: 500
lifecycle: archived
---

## Problem

The `capture_lanes` field on cascade-timeline events is **load-bearing** (populated
on 4,863 of 4,888 entries, 99.5%) but has no canonical taxonomy. The field is
declared in `cascade-timeline/kb.yaml` as an open list with no enum. Result: 118
unique values in use across the timeline, with serious drift under an otherwise
coherent 17-value core.

The timeline is the primary cross-cutting index for investigation work — and
capture_lanes is the primary slicing axis. Without canonicalization, queries like
"all Detention Industrial Complex events 2024-2026" return incomplete results
because entries use "Detention Industrial Complex" (9), nothing, or a related
but distinct value.

## Taxonomy-drift inventory

### The canonical 17 (should be the enum)

Values with >100 uses, clearly the intended set:

1. Regulatory Capture (852)
2. Systematic Corruption (380)
3. Judicial Capture (338)
4. Financial Capture (301)
5. Media Capture & Control (258)
6. Electoral Manipulation (238)
7. Corporate Capture (237)
8. Intelligence Penetration (229)
9. Democratic Erosion (219)
10. Labor Suppression (188)
11. Legislative Capture (186)
12. Digital & Tech Capture (180)
13. Executive Power Expansion (176)
14. Civil Rights Suppression (157)
15. Surveillance Infrastructure (152)
16. Military-Industrial Complex (141)
17. International Kleptocracy (127)

### Medium-tier (decide whether to canonicalize)

- Institutional Capture (50) — likely keep
- Intelligence Privatization (48) — likely merge into Intelligence Penetration
- Environmental Capture (41) — keep
- Military Capture (37) — merge into Military-Industrial Complex
- Federal Workforce Capture (31) — keep
- Immigration System Capture (26) — keep (Detention Industrial Complex is a child)

### Drift to fix mechanically

- **Quote-leakage** (broken YAML, trivially fixable): `'Media Capture & Control` (76 → Media Capture & Control), `'Digital & Tech Capture` (16 → Digital & Tech Capture). These are entries where an earlier batch generator dropped a trailing quote.
- **Case/phrasing drift**: `Military-Industrial Capture` (13), `Military Capture` (37) → Military-Industrial Complex
- **Lowercase one-word drift**: `political` (15), `economic` (9), `judicial` (2), `regulatory` (1), `media` (1), `military` (1) — different generator; map to the Title Case canonical form
- **Kebab-case drift**: `intelligence-penetration` (2), `financial-capture` (2) → Title Case canonical forms
- **Field-name drift**: 29 entries use `capture_lane` (singular) instead of `capture_lanes` (plural). Rename.

### Relocate to `tags`

Event-specific strings that are claims, not lanes — move to `tags`:

- Gaza Reconstruction Profit Architecture
- Peace-Envoy Family-Business Conflicts
- Hidden-Sovereign-Adjacent-Capital Behind Trump-Family Deal
- Foreign State Payments to Trump Family
- Trump-Family Business Operational Expansion
- Navy-Procurement-Bypass (WEXMAC-TITUS)
- Gulf-State Licensing
- Crypto Capture (arguably canonical — debatable)

### One-offs to evaluate

~40 values with 1-2 uses each. Most should map to the canonical 17 or move to
tags. A handful may be genuine lane candidates worth promoting (e.g.,
`Health Data Consolidation`, `DoD-DHS Resource Diversion`).

## Proposed change

### Schema (cascade-timeline/kb.yaml)

```yaml
types:
  event:
    optional:
      - capture_lanes
      # ... existing fields
    fields:
      capture_lanes:
        type: list
        item_type: select
        values:
          - Regulatory Capture
          - Systematic Corruption
          - Judicial Capture
          - Financial Capture
          - Media Capture & Control
          - Electoral Manipulation
          - Corporate Capture
          - Intelligence Penetration
          - Democratic Erosion
          - Labor Suppression
          - Legislative Capture
          - Digital & Tech Capture
          - Executive Power Expansion
          - Civil Rights Suppression
          - Surveillance Infrastructure
          - Military-Industrial Complex
          - International Kleptocracy
          - Institutional Capture
          - Environmental Capture
          - Federal Workforce Capture
          - Immigration System Capture
```

(Match the same enum into cascade-research kb.yaml capture_lanes fields so
cross-KB joins work.)

### Migration

A one-shot script that:

1. Rewrites `capture_lane:` → `capture_lanes:` on the 29 affected files
2. Collapses the quote-leakage variants (76 + 16 files)
3. Maps the drift table (case, kebab, one-word) to canonical forms
4. Moves the event-specific strings from `capture_lanes` to `tags`
5. Leaves unmapped one-offs in place with a warning report for manual review

### Validation

Add a `pyrite kb validate cascade-timeline` rule that rejects any `capture_lanes`
value not in the enum. Run it before commit in pre-commit hook.

## Acceptance criteria

- [ ] cascade-timeline/kb.yaml declares `capture_lanes` as a list-of-enum with the
      canonical 17-21 values
- [ ] cascade-research/kb.yaml `capture_lanes` fields (on actors, organizations,
      events, capture-lanes entry-type, scenes, statistics) match the same enum
- [ ] Migration script committed in `pyrite/scripts/` or similar
- [ ] All 4,863 populated entries migrated to canonical values with zero drift
- [ ] `capture_lane` singular field eliminated (29 entries renamed)
- [ ] Event-specific strings relocated to `tags`
- [ ] `pyrite kb validate` rejects non-enum values
- [ ] Migration report documents any entries that could not be auto-mapped

## Out of scope

- Building a new `capture-lane` entry type for each canonical lane (the 8
  existing dedicated capture-lane entries in cascade-research are thematic
  essays, not 1:1 with the event classifier; keep them as-is for now)
- Retroactively adding capture_lanes to the 25 unpopulated entries
- UI surfacing of the enum (e.g., a capture-lanes heatmap view)

These can follow as separate tickets.

## Rationale

The field is already doing the work (99.5% population rate is not ambient use).
The question is not whether to have a taxonomy, but whether to enforce the one
that has already emerged. Enforcement costs one migration + one validation rule,
and unlocks reliable cross-cutting queries that are currently silently wrong.

## Related

- cascade-timeline contains 4,888 events (April 2026 count)
- Primary affected files: anything under `~/tcp-kb-internal/cascade-timeline/*.md`
- Downstream consumers: cascade-research investigation-map, RAMM publication
  pipeline, any capture-lanes-based analytical query
