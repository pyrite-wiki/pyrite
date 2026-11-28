---
id: ji-absorb-cascade-cli
title: "Move cascade CLI commands into the JI plugin"
type: backlog_item
tags: [cli, journalism-investigation, cascade, deprecation]
links:
- target: epic-normalization-and-data-cleanup
  relation: subtask_of
  kb: pyrite
- target: epic-pyrite-publication-strategy
  relation: related_to
  kb: pyrite
importance: 5
kind: feature
status: retired
priority: high
effort: S
rank: 200
lifecycle: archived
---

## Scope

Move `pyrite cascade …` commands to `pyrite ji …`.

| Current | New | Notes |
|---------|-----|-------|
| `pyrite cascade suggest-aliases` | `pyrite ji suggest-aliases` | Generic — works on any investigation KB |
| `pyrite cascade extract-actors` | `pyrite ji extract-actors` | Generic |
| `pyrite cascade export` | `pyrite ji export-timeline` | Generic JSON shape — no site-specific formats (see below) |
| `pyrite cascade audit-compat` | drop after Phase 2 | Migration-only command |
| `pyrite cascade backfill-ji` | drop after Phase 2 | Migration-only command |

## Static-export implementation

**Revised decision** (supersedes the earlier `--format=capturecascade`
recommendation; see [[epic-pyrite-publication-strategy]]): JI exposes a
**generic timeline-export JSON shape** as a documented public data
contract. Site-specific adapters (capturecascade's React viewer shape,
future Hugo adapters, etc.) live in the **site repos**, not in pyrite.

This matches how `detention-pipeline` already works: pyrite publishes
data, the site repo owns presentation.

Concretely:

- `pyrite ji export-timeline -k <kb> -o <dir>/` writes
  `timeline.json`, `actors.json`, `tags.json`, `stats.json` in the
  **generic JI schema** (documented in JI's README + a JSON schema file)
- Field names and shape match what today's cascade export emits — so
  existing consumers keep working — but the schema is now an intentional
  public contract rather than a capturecascade-specific artifact
- **No `--format` flag.** One canonical output. Site-specific
  massaging happens in the site repo's build pipeline.
- The capturecascade site repo gains a small adapter step if any
  reshaping is needed (today: none — the current output *is* the
  generic shape).

Implementation:

- Copy `extensions/cascade/src/pyrite_cascade/static_export.py` to
  `extensions/journalism-investigation/src/pyrite_journalism_investigation/export_timeline.py`
- Write `docs/ji-timeline-export-schema.md` (or ship a JSON Schema file
  alongside) documenting the output as a versioned public contract
- Leave the cascade plugin's `export` command importing from the new
  location for one release (deprecation shim)

## TDD

1. `test_ji_export_timeline_produces_generic_shape` — run against a
   fixture KB, assert output matches the documented schema field-by-field.
2. `test_ji_export_timeline_byte_identical_to_current_cascade` — run
   against the cascade-timeline KB, assert output is byte-identical to
   today's `pyrite cascade export`. Guards the public-contract promise
   during the move.
3. `test_ji_cli_extract_actors_available` — `pyrite ji extract-actors --help` exits 0.

## Changes

- `extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py` — add new commands
- `extensions/journalism-investigation/src/pyrite_journalism_investigation/export_timeline.py` — new file, copy of cascade's `static_export.py`
- `extensions/journalism-investigation/docs/timeline-export-schema.md` — new public-contract doc
- `extensions/cascade/src/pyrite_cascade/cli.py` — keep commands as shims that call the JI versions + emit deprecation warning
- `extensions/cascade/src/pyrite_cascade/static_export.py` — becomes a re-export from JI

## Done when

- `pyrite ji export-timeline -k cascade-timeline -o /tmp/out/` produces
  byte-identical JSON to today's `pyrite cascade export`
- `pyrite cascade export` still works but logs a `DeprecationWarning`
  pointing to the JI command
- Timeline-export JSON schema doc committed and linked from JI README
- Documentation (kb/runbooks or deploy docs) updated to use the new
  command

## Depends on

[[ji-absorb-cascade-types]]

## Unblocks

[[remove-cascade-plugin]], site-repo work under
[[epic-pyrite-publication-strategy]]
