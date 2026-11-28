---
id: search-relevance-boost-by-entry-type
title: Search Relevance Boost by Entry Type
type: backlog_item
tags:
- enhancement
- search
kind: feature
priority: low
effort: M
status: proposed
---

## Problem

Search results treat all entry types equally. A completed backlog item and a current component doc have the same ranking weight. KB operators should be able to tune which types surface first based on their KB's purpose.

## Solution

Operator-controlled search relevance tuning in `kb.yaml`. Several complementary mechanisms:

### 1. Static type boost multipliers

```yaml
search_boost:
  component: 1.5
  adr: 1.2
  standard: 1.3
  backlog_item: 0.8
  note: 0.7
```

Applied as a multiplier on relevance scores in FTS5 and semantic search. The Pyrite project KB would boost components and ADRs. A journalism KB would boost timeline events and actors.

### 2. Temporal relevance curves per type

Different types age differently. The operator configures how recency affects ranking:

```yaml
search_temporal:
  timeline_event:
    curve: none          # events don't lose relevance with age
  component:
    curve: decay
    half_life_days: 180  # component docs lose relevance if not updated
  backlog_item:
    curve: decay
    half_life_days: 90   # active backlog items matter, old ones less so
  adr:
    curve: none          # ADRs are historical records, age is irrelevant
```

This makes "what's the current architecture?" surface fresh component docs, while "why did we choose PostgresBackend?" still finds the ADR from months ago.

### 3. Type-grouped search results

Search results can be grouped by entry type and listed in a KB-defined order:

```yaml
search_display:
  group_by_type: true
  type_order: [component, adr, standard, backlog_item, note]
```

Rather than a flat ranked list, results appear as: "Components (3 results) → ADRs (2 results) → Backlog items (5 results)." Agents and the web UI can both consume this grouping. Flat ranking remains the default.

### 4. QA-derived search importance

QA assessments already produce per-entry quality scores. Feed these back into search ranking:

- Entries with recent passing QA assessments get a boost
- Entries with open QA failures get demoted (stale content shouldn't rank highly)
- Entries that have never been QA-assessed are neutral

This creates a virtuous cycle: maintaining entry quality (responding to QA warnings, verifying freshness) directly improves that entry's discoverability.

### Intent layer integration

Connect lifecycle signals ([[kb-compaction-and-entry-lifecycle]]) to intent layer evaluation rubrics ([[intent-layer]]). A component doc that hasn't been verified recently can't score well on accuracy. Freshness becomes a factor in quality assessment, not just a QA warning.

## The unifying principle

**The schema should inform the search, not just the validation.** The operator already defines what types exist, what fields they have, and what "good" looks like. Search relevance should be another expression of that domain knowledge — not a separate, disconnected system.

## Prerequisites

- [[entry-lifecycle-field-and-search-filtering]] — lifecycle field
- [[kb-compaction-command-and-freshness-qa-rules]] — freshness tracking
- [[intent-layer]] Phase 1 — evaluation rubrics
- [[qa-agent-workflows]] Phase 2 — QA assessments (for QA-derived importance)

## Related

- [[kb-compaction-and-entry-lifecycle]] — parent design (Phase 3)
