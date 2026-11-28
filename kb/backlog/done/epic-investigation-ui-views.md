---
id: epic-investigation-ui-views
title: 'Epic: Investigation UI views (web frontend)'
type: backlog_item
tags:
- journalism
- investigation
- web
- frontend
- epic
links:
- target: design-journalism-investigation-plugin
  relation: informed_by
  kb: pyrite
- target: epic-core-journalism-investigation-plugin
  relation: depends_on
  kb: pyrite
- target: ji-ui-timeline-visualization
  relation: has_subtask
  kb: pyrite
- target: ji-ui-network-graph
  relation: has_subtask
  kb: pyrite
- target: ji-ui-investigation-dashboard
  relation: has_subtask
  kb: pyrite
- target: ji-ui-entity-profile
  relation: has_subtask
  kb: pyrite
- target: ji-ui-source-panel
  relation: has_subtask
  kb: pyrite
importance: 5
kind: epic
status: retired
priority: high
effort: XL
rank: 2500
lifecycle: archived
---

## Overview

The journalist persona works primarily through the web UI and Claude Desktop/Cowork MCP sessions — not the terminal. The investigation workflow demands visual tools for timeline navigation, relationship mapping, evidence tracking, and narrative construction. The current SvelteKit app needs investigation-specific views.

## User Context

The primary user is an investigative journalist who:
- Creates investigations and iteratively builds them through cycles of research → build → verify → restructure
- Needs to see the big picture (timeline, network) while drilling into specifics (entity profiles, evidence chains)
- Returns to investigations after days/weeks away and needs fast context rebuild
- Works across multiple KBs simultaneously (investigation KB, shared reference KB, prior investigations)
- Uses Claude Desktop/Cowork as the primary agent interface alongside the web UI

## Subtasks

1. **Timeline visualization** — interactive timeline of events, filterable and zoomable
2. **Network graph** — entity relationship map with ownership/funding/membership edges
3. **Investigation dashboard** — investigation status, claims coverage, evidence gaps, activity feed
4. **Entity profile view** — everything known about a person/org in one place
5. **Source management panel** — source reliability, URL status, evidence chains

## Design Principles

- **Context rebuilding is cheap** — every view answers "where was I?" quickly
- **Everything is provisional** — UI must support status changes, re-linking, restructuring without feeling destructive
- **Cross-KB awareness** — views should surface relevant entities from other KBs
- **Conversation-ready** — views should complement MCP interactions (agent creates entry → UI reflects it immediately)

## Success Criteria

- Journalist can navigate an investigation visually without using the terminal
- Timeline view handles 4,000+ events with responsive filtering
- Network graph shows ownership/funding chains through shell company layers
- Dashboard surfaces unverified claims and evidence gaps at a glance
- Entity profile aggregates all linked events, connections, claims, and sources
