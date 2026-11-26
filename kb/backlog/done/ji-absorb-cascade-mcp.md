---
id: ji-absorb-cascade-mcp
title: "Move cascade MCP tools into the JI plugin"
type: backlog_item
tags: [mcp, journalism-investigation, cascade, deprecation]
links:
- target: epic-normalization-and-data-cleanup
  relation: subtask_of
  kb: pyrite
importance: 5
kind: feature
status: retired
priority: medium
effort: S
rank: 300
lifecycle: archived
---

## Scope

Move cascade MCP tools to JI. Strip the `cascade_` prefix. Generalize
metadata-field filters so nothing in the tool is cascade-specific.

| Current | New | Changes |
|---------|-----|---------|
| `cascade_actors` | `ji_actors` | Filter by arbitrary metadata field (not just `capture_lane`, `era`) |
| `cascade_timeline` | `ji_timeline` | Arbitrary metadata-field filter |
| `cascade_network` | drop — already a duplicate delegating to JI's `query_network` |
| `solidarity_timeline` | `ji_solidarity_timeline` | Already investigation-shaped; just rename |
| `solidarity_infrastructure_types` | `ji_solidarity_infrastructure_types` | Rename |
| `cascade_capture_lanes` | generalize to `ji_metadata_facets` | Take a `field:` arg so you can count any list-valued metadata field (capture_lanes, infrastructure_types, chapters, etc.) |

## TDD

1. `test_ji_actors_tool_filters_by_metadata` — register a KB with
   actors tagged `capture_lane=media`, call `ji_actors` with
   `filter: {capture_lane: media}`, assert only matching actors
   returned.
2. `test_ji_metadata_facets_counts_lanes` — call with
   `field=capture_lanes`, assert counts match.
3. `test_cascade_network_removed_or_aliased` — confirm no `cascade_network`
   tool in JI's registered tools (it's still reachable through JI's
   own `ji_network` or equivalent).

## Changes

- `extensions/journalism-investigation/src/pyrite_journalism_investigation/plugin.py` — new `get_mcp_tools()` entries
- `extensions/cascade/src/pyrite_cascade/plugin.py` — remove moved handlers, keep tool registrations that emit deprecation warnings on call for one release

## Done when

- All JI MCP tools registered and callable via the MCP server
- Cascade MCP tools still respond (deprecation shim) but log a warning
- `cascade_network` removed entirely (zero callers — it was a pure
  duplicate)

## Depends on

[[ji-absorb-cascade-types]]

## Unblocks

[[remove-cascade-plugin]]
