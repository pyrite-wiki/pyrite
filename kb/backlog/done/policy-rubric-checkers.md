---
id: policy-rubric-checkers
title: "Policy Rubric Checkers: User-Configurable Constraint Rules"
type: backlog_item
tags:
- schema
- feature
- qa
- intent-engineering
- extension:software-kb
links:
- target: intent-layer
  relation: related
- target: intent-layer-guidelines-and-goals
  relation: related
- target: pyrite-ci-command
  relation: related
kind: feature
effort: M
status: retired
---

## Problem

The rubric evaluation system has two tiers: hardcoded deterministic checkers (Phase 2) and LLM judgment (Phase 3). There's a gap between them — **policy constraints** that are programmatically verifiable but user-configurable. Examples:

- "Entry has at least 2 valid source_url links" (journalism KB)
- "Entry references at least 1 actor" (investigation KB)
- "ADR has 2+ reviewer comments before moving out of proposed" (engineering KB)
- "Component entry has a non-empty dependencies list" (software KB)
- "Event entry links to at least one person" (events KB)

These currently fall through `match_rubric_item()` → `None` and get sent to the LLM, wasting tokens on something a SQL query or metadata check can answer definitively.

## Solution

Add a **policy rubric matcher** that recognizes structured constraint syntax in `evaluation_rubric` items and creates parameterized checkers at runtime. Runs at tier 1 alongside existing deterministic checks — no LLM cost.

### Constraint Syntax

Rubric items with structured patterns are recognized and handled programmatically:

```yaml
evaluation_rubric:
  # Count constraints on metadata fields
  - "metadata.source_url count >= 2"
  - "metadata.reviewers count >= 2"

  # Link/relation constraints
  - "links.actor count >= 1"
  - "outlinks count >= 3"

  # Tag constraints
  - "tags count >= 2"

  # Body section requirements
  - "body.section 'References' required"

  # Status transition gates (pairs with lifecycle/kanban)
  - "status proposed->accepted requires metadata.reviewers count >= 2"
```

### Architecture

1. **Policy matcher** in `rubric_checkers.py`: regex-based pattern recognition for structured syntax, returns parameterized checker functions
2. **Precedence**: `is_already_covered()` → `match_rubric_item()` → `match_policy_rule()` → falls through to LLM
3. **`_collect_judgment_items()`** updated to also filter out policy-matched items
4. Free-text rubric items continue to LLM evaluation unchanged

### Status Transition Gates

The `status X->Y requires <constraint>` pattern enables workflow enforcement without a separate gate system. Combined with ADR-19's kanban entity types, this gives teams configurable quality gates:

```yaml
# In kb.yaml type overrides
types:
  adr:
    evaluation_rubric:
      - "status proposed->accepted requires metadata.reviewers count >= 2"
      - "status proposed->accepted requires body.section 'Alternatives' required"
```

This runs during `pyrite ci` and QA validation — if an ADR is in `accepted` status but doesn't meet the gate criteria, it produces a `rubric_violation`.

## Prerequisites

- Intent Layer Phase 2 (deterministic rubric checkers) ✅
- Intent Layer Phase 3 (LLM rubric evaluation) ✅

## Success Criteria

- Structured constraint syntax recognized in `evaluation_rubric` items
- Policy rules run at tier 1 (no LLM cost)
- `pyrite ci` enforces policy constraints
- Status transition gates work with kanban workflows
- Free-text items still fall through to LLM at tier 2
- Pyrite's own kb.yaml uses policy constraints (dogfooding)
