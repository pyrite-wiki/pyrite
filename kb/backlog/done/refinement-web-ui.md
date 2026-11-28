---
id: refinement-web-ui
title: "Refinement web UI for DoR readiness"
type: backlog_item
tags:
- software-kb
- web-ui
- workflow
kind: feature
priority: low
effort: M
status: retired
lifecycle: archived
---

## Problem

The `sw_check_ready` and `sw_refine` tools are available via CLI and MCP, but there's no web UI for browsing backlog readiness. A refinement view would let humans scan DoR gaps visually and edit items inline to fill them.

## Scope

- Add a refinement view to the web UI showing DoR readiness for backlog items
- Show gate criteria with pass/fail status per item
- Allow inline editing to fill gaps (effort, tags, acceptance criteria)
- Filter by status (proposed/accepted) and sort by priority

## Acceptance Criteria

- Web UI shows DoR readiness for each proposed/accepted item
- Failing criteria are highlighted with actionable hints
- User can edit item fields inline to resolve gaps
