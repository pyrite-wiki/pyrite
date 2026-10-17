---
id: kb-compaction-and-entry-lifecycle
title: KB Compaction and Entry Lifecycle
type: backlog_item
tags:
- enhancement
- qa
- search
metadata:
  priority: medium
kind: feature
status: done
assignee: claude
effort: M
---

## Problem

KBs only grow. There's no systematic way to reduce the active search surface as entries age, get superseded, or complete their purpose. This creates two problems:

1. **Search noise**: Completed backlog items, superseded design sketches, and one-time notes rank alongside current component docs and active ADRs. Agents get stale context mixed with current truth.

2. **Type-blind aging**: Different entry types age differently, but QA and search treat them uniformly. ADRs are *designed* to be historical — they gain value with age because they explain past reasoning. Component docs should reflect current architecture — a stale component doc is a defect. Design docs may be superseded by ADRs. Completed backlog items served their purpose. The system doesn't encode these distinctions.

## Context

Several pieces of infrastructure already exist or are partially scaffolded:

- **Type-level AI instructions** in `CORE_TYPE_METADATA` — already provide per-type QA guidance
- **Plugin validator hooks** via `get_validators()` — custom validation per entry type
- **QA assessment entries** — structured results from validation runs
- **`done/` directory convention** — manual lifecycle signal for completed backlog items
- **Intent layer** (partially scaffolded, [[intent-layer]]) — guidelines, goals, and evaluation rubrics per type
- **`kb.yaml` editorial guidelines** — KB-level QA configuration

The gap is wiring lifecycle semantics into these existing pieces.

## Design

### 1. Entry lifecycle field

Add an optional `lifecycle` field to entry frontmatter:

```yaml
lifecycle: active | archived | superseded
superseded_by: <entry-id>  # when lifecycle: superseded
```

- `active` (default, implicit if absent) — normal search ranking, full QA
- `archived` — excluded from default search, included with `--include-archived` flag. QA skips. Still in git, still queryable.
- `superseded` — like archived, but links to the replacement entry. Useful for design docs that became ADRs.

Search, MCP tools, and CLI filter out non-active entries by default. Add `include_archived: true` parameter to search tools.

### 2. Type-aware freshness

Types that represent current truth get a `freshness_matters: true` flag in type metadata:

- `component`: freshness matters — stale component docs mislead agents
- `design_doc`: freshness matters — should reflect current design or be marked superseded
- `adr`: freshness does NOT matter — historical record by design
- `backlog_item`: freshness partially matters — active items yes, done items no
- `standard`: freshness matters — standards should be current

For types where freshness matters, QA checks `last_verified` or falls back to file modification time. Entries not updated in a configurable window (e.g., 90 days) get a QA warning: "component X hasn't been verified since [date]."

### 3. Search relevance by type (kb.yaml knob)

```yaml
search_boost:
  component: 1.5
  adr: 1.2
  standard: 1.3
  backlog_item: 0.8
  note: 0.7
```

Operator-controlled, KB-specific. The Pyrite project KB would boost components and ADRs. A journalism KB would boost timeline events and actors. Applied as a multiplier on search relevance scores.

### 4. Compaction command

```bash
# Show archival candidates (dry run)
pyrite kb compact --kb pyrite --dry-run

# Archive candidates that match rules
pyrite kb compact --kb pyrite

# Rules for candidate detection:
# - backlog_item with status: done, no inbound links from active entries
# - design_doc where a corresponding ADR exists (detected via links)
# - note with importance <= 3 and no inbound links
# - any entry with 0 inbound links and last modified > 6 months ago
```

Compaction suggests, human confirms. The command sets `lifecycle: archived` on confirmed entries — it doesn't delete. Git history preserves everything.

### 5. QA integration

New Tier 1 rules (no LLM needed):

- **Staleness check**: For `freshness_matters` types, warn if not updated in configured window
- **Superseded check**: Warn if a design doc's wikilinks all point to an ADR that covers the same topic
- **Orphan detection**: Flag entries with zero inbound links and low importance (archival candidates)
- **Done-but-referenced**: Warn if a `done` backlog item is still linked from active entries (the referencing entry may need updating)

These integrate with the existing `QAService.validate_entry()` path and plugin validator hooks.

## Relationship to intent layer

The intent layer ([[intent-layer]]) defines *what good looks like* per type. This feature defines *what stale looks like* per type. They're complementary:

- Intent layer: "A component doc should have path, owner, dependencies, and accurately describe current architecture"
- Lifecycle: "A component doc that hasn't been verified in 90 days may not accurately describe current architecture"

The intent layer's evaluation rubrics could eventually incorporate lifecycle signals — a component doc can't score well on "accuracy" if it hasn't been verified recently.

## Phases

### Phase 1: Lifecycle field + search filtering (effort: S)

- Add `lifecycle` field support to entry model
- Filter archived/superseded entries from default search
- Add `--include-archived` flag to CLI search and MCP tools
- `pyrite kb archive <entry-id>` command to set lifecycle

### Phase 2: Compaction command + QA rules (effort: S)

- `pyrite kb compact --dry-run` with configurable detection rules
- Staleness and orphan QA rules in Tier 1 validation
- `freshness_matters` flag in type metadata

### Phase 3: Search boost + intent integration (effort: M)

- `search_boost` configuration in kb.yaml
- Apply multipliers in FTS5 and semantic search ranking
- Connect lifecycle signals to intent layer evaluation rubrics

## Success criteria

- `pyrite kb compact --dry-run` identifies reasonable archival candidates
- Archived entries excluded from default search, recoverable with flag
- Component docs get staleness warnings after configurable window
- ADRs do not get staleness warnings
- Search results feel more relevant after compaction
- KB entry count in active search surface decreases without losing history

## Files likely affected

- `pyrite/models/base.py` — lifecycle field on Entry
- `pyrite/storage/index.py` — lifecycle-aware search filtering
- `pyrite/services/kb_service.py` — archive/compact operations
- `pyrite/services/qa_service.py` — staleness and orphan rules
- `pyrite/schema.py` — freshness_matters in type metadata, search_boost in KBSchema
- `pyrite/cli/__init__.py` — `kb archive`, `kb compact` commands
- `pyrite/server/mcp_server.py` — include_archived parameter on search tools
