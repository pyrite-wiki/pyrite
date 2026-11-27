---
id: epic-cross-kb-investigation-search
title: 'Epic: Cross-KB investigation search and entity correlation'
type: backlog_item
tags:
- journalism
- investigation
- search
- cross-kb
- epic
links:
- target: design-journalism-investigation-plugin
  relation: informed_by
  kb: pyrite
- target: epic-core-journalism-investigation-plugin
  relation: depends_on
  kb: pyrite
- target: ji-unified-cross-kb-search
  relation: has_subtask
  kb: pyrite
- target: ji-cross-kb-entity-dedup
  relation: has_subtask
  kb: pyrite
- target: ji-known-entities-kb-pattern
  relation: has_subtask
  kb: pyrite
kind: epic
status: done
priority: high
effort: L
---

## Overview

Investigative journalists work across multiple KBs simultaneously — their current investigation, prior investigations, a shared reference KB of known entities, and external MCP data sources. The research phase involves searching everywhere at once and correlating results: "Does this person appear in any of our other investigations? Is this company in the Panama Papers?"

This epic makes cross-KB search a first-class operation, with entity deduplication and a "known entities" reference KB pattern.

## User Workflow

```
Journalist asks: "What do we know about Company X?"
                          ↓
        ┌─────────────────┼──────────────────┐
        ↓                 ↓                  ↓
  Current KB        Prior KBs          External MCP
  (investigation)   (old investigations)  (Panama Papers,
                                          documents, web)
        ↓                 ↓                  ↓
        └─────────────────┼──────────────────┘
                          ↓
              Correlated results:
              - Entity profile from current KB
              - 3 mentions in prior investigations
              - 2 Panama Papers matches
              - 5 corporate registry hits
```

## Subtasks

1. **Unified cross-KB search** — single query across all configured KBs with result correlation
2. **Cross-KB entity deduplication** — detect same entity across KBs, suggest merges
3. **Known entities KB pattern** — shared reference KB of established entities reusable across investigations

## Success Criteria

- Single search query returns correlated results from all KBs
- Same entity in multiple KBs is identified and linked
- Known entities KB provides shared baseline for all investigations
- External MCP sources integrated into search results
