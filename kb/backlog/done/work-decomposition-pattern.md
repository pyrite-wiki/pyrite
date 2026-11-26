---
id: work-decomposition-pattern
title: "Unit of Work decomposition pattern for large backlog items"
type: backlog_item
tags:
- workflow
- agents
- software
- kanban
- extension:software-kb
links:
- target: adr-0020
  relation: tracks
  note: "Extends kanban flow with decomposition rules"
kind: feature
effort: L
status: retired
lifecycle: archived
---

## Problem

Large backlog items (XL and above) are too big for a single agent dev session. We need a formal decomposition pattern so that:

1. Items over a size threshold (configurable, default XL) **must** be decomposed into smaller sub-items before work begins.
2. Sub-items become the actual units of work — each completable in one agent session.
3. The parent item tracks overall progress via its children.

## Design considerations

- Could be enforced via `sw_validate` or as a workflow gate (e.g., `sw_claim` refuses XL+ items that haven't been decomposed).
- Parent/child relationship type needed (or reuse `tracks`/`tracked_by`).
- `sw_pull_next` should skip undecomposed XL+ items and instead recommend their children.
- Board view should show rollup progress for parent items.
- Threshold should be configurable in board.yaml or KB config.
