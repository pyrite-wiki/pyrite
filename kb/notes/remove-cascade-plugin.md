---
id: remove-cascade-plugin
title: "Delete the cascade plugin after deprecation cycle"
type: backlog_item
tags: [cascade, deprecation, cleanup]
links:
- target: epic-normalization-and-data-cleanup
  relation: subtask_of
  kb: pyrite
- target: ji-absorb-cascade-types
  relation: depends_on
  kb: pyrite
- target: ji-absorb-cascade-cli
  relation: depends_on
  kb: pyrite
- target: ji-absorb-cascade-mcp
  relation: depends_on
  kb: pyrite
- target: migrate-cascade-kbs-to-investigation
  relation: depends_on
  kb: pyrite
importance: 5
kind: chore
status: done
priority: medium
effort: S
rank: 700
---

## Scope

Final step of the cascade deprecation. Delete `extensions/cascade/`
entirely after:

1. JI has absorbed all cascade types, CLI, MCP tools, hooks, validators,
   and relationships ([[ji-absorb-cascade-types]],
   [[ji-absorb-cascade-cli]], [[ji-absorb-cascade-mcp]])
2. All three cascade KBs are migrated
   ([[migrate-cascade-kbs-to-investigation]])
3. At least one release has shipped with cascade as a deprecation shim
   (re-exports from JI + `DeprecationWarning` on import / CLI invocation
   / MCP tool call)
4. Release notes and `/docs/migration-from-cascade.md` document the path

## Changes

- Delete `extensions/cascade/` entirely
- Remove the plugin entry point from the monorepo's extension
  registration (check `pyproject.toml`, any extension discovery config)
- Search for and remove any imports of `pyrite_cascade` outside
  `extensions/cascade/` (there shouldn't be any after the earlier
  subtasks; this is a grep-and-verify pass)
- Add a note to `CHANGELOG.md` / release notes naming the removal

## Done when

- `rg 'pyrite_cascade'` returns no hits in `pyrite/` or
  `extensions/journalism-investigation/`
- `pyrite plugins list` does not show cascade
- `pytest tests/ -v` and JI extension tests still pass
- `capturecascade.org` deploy still works from cascade-timeline via
  the JI-hosted export command

## Depends on

[[ji-absorb-cascade-types]], [[ji-absorb-cascade-cli]],
[[ji-absorb-cascade-mcp]], [[migrate-cascade-kbs-to-investigation]]

## Unblocks

[[rename-investigation-event-to-timeline-event]] — no more cross-plugin
coordination needed, so the internal JI rename is safe.
