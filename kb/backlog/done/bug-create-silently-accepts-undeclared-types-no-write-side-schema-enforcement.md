---
id: bug-create-silently-accepts-undeclared-types-no-write-side-schema-enforcement
title: "BUG: `pyrite create` and MCP `kb_create` silently accept undeclared entry types — schema validator runs but its result is discarded"
type: backlog_item
tags: [bug, cli, mcp, schema, write-validation, silent-data-loss, conductor-friction, backlog-grooming]
importance: 5
kind: bug
status: done
priority: high
effort: S
rank: 1090
---

## Problem

The pyrite repo's `kb/kb.yaml` declares `kb_type: software` with exactly
four entry types: `adr`, `backlog_item`, `component`, `standard`. Trying
to write any other type via `pyrite create -k pyrite -t <bogus>` succeeds
silently with no warning, no error, and no hint that the type isn't part
of this KB's schema.

Concrete reproduction:

```
$ .venv/bin/pyrite create -k pyrite -t bogus_type --title "test" -b "body"
Created: test
Type: bogus_type
```

Same behavior on the MCP side (`_kb_create` at
`pyrite/server/mcp_server.py:731`). The handler **does** call
`schema.validate_entry(entry_type, args, ...)` on line 750 and collects
the result into a `warnings` list — but the warnings are then **discarded**
and the create proceeds. The validation runs; the enforcement doesn't fire.

## Impact

- **Off-schema data accumulates silently.** Until something downstream
  (the `invalid_statuses` health check, or a typed read consumer) hits
  the off-schema entry and surfaces it, the bad write is invisible.
- **External agents file into the wrong type.** The 2026-06-10 conductor
  filing pass put 6 tickets into pyrite using `type: task` instead of
  `type: backlog_item`, with off-spec `status: open` and numeric
  `priority: 4-7`. The conductor was filing from its native task-vocabulary
  rather than reading `kb/kb.yaml` first. If `kb_create` had refused the
  off-schema type (or surfaced the validator's warnings in the response),
  the conductor would have either retried with `backlog_item` or called
  the MCP `kb_schema` tool to discover the right type. Instead the
  bad writes succeeded and required a manual PO-triage normalization
  pass to clean up. See commit `b2e7ae8`.
- **Same family as the rest of Tier A.** Silent acceptance of bad data
  at write time; consumers find out later. Compounds with
  [[bug-pyrite-update-doesnt-keep-index-in-sync-with-source]],
  [[bug-pyrite-sw-prioritize-renumbers-globally-clobbering-existing-ranks]],
  [[bug-pyrite-get-omits-rank-field-in-json-output]] — the grooming-tools
  family.

## Root cause

`pyrite/server/mcp_server.py:748-751`:

```python
# Validate against schema
schema = kb_config.kb_schema
validation = schema.validate_entry(entry_type, args, context={"kb_type": kb_config.kb_type})
warnings = validation.get("warnings", [])
```

The `warnings` variable is collected and then never used. Whether
`validation` contained errors or warnings, the code proceeds to
`create_entry` unconditionally. The CLI `pyrite create` likely shares the
same shape (didn't audit yet — confirm at fix time).

## Fix

Two modes worth supporting; pick a default:

### Option A — Refuse undeclared types by default; allow `--allow-undeclared`

If the entry's `type` is not in `kb_config.kb_schema.types` AND not a
core type (note, person, organization, event, etc.), refuse the create
with a clear error:

```
Error: type 'task' is not declared in KB 'pyrite' (kb_type: software).
Declared types: adr, backlog_item, component, standard.
Use `pyrite kb schema show pyrite` to inspect the full schema.
Pass --allow-undeclared to override (the entry will be flagged by
`pyrite index health`).
```

MCP side returns:

```json
{"error_code": "UNDECLARED_TYPE", "message": "...", "suggestion": "...",
 "declared_types": ["adr","backlog_item","component","standard"]}
```

This is the principled fix — write-side enforcement matches what the
read-side `invalid_statuses` / `undeclared_types` health checks already
report.

### Option B — Always create, but surface the warnings in the response

If the create-and-warn semantics are deliberate (e.g. ephemeral KBs
where schema is fluid), at minimum **return the warnings to the
caller** instead of discarding them. MCP response gains a `warnings`
array; CLI prints them at stderr. The conductor would then see the
warnings and self-correct on the next file.

**Recommendation: Option A as default, with `--allow-undeclared`
escape hatch.** Option B alone leaves the silent-write hole — agents
that don't inspect warnings will keep filing wrong.

Either way, add a regression test that:
1. Creates a KB with a `kb.yaml` declaring one type (`foo`).
2. Calls `pyrite create -k <kb> -t bogus_type --title "..."`.
3. Asserts the create FAILS (Option A) or returns a `warnings`
   array containing the undeclared-type warning (Option B).
4. Calls the same with `--allow-undeclared` and asserts it succeeds
   (Option A only).

## Acceptance criteria

- `pyrite create` (CLI) and `_kb_create` (MCP) refuse undeclared types
  by default, or at minimum surface the schema validator's warnings to
  the caller. Silent-accept is gone.
- The error/warning names the declared types so the caller can self-
  correct without round-tripping through `kb schema show`.
- A regression test pins the contract.
- ADR or docs note clarifies what "undeclared" means against the
  three-layer schema (core types, kb.yaml types, plugin types) — `task`
  is a *core* type but it's not in this KB's `kb.yaml`, which is the
  shape of mistake we want to catch.

## Out of scope (for separate filing if desired)

- **Conductor-side fix** (tcp-skills): the conductor should read
  `kb_schema` before filing into a foreign KB. That's a TCP-skill change,
  not a pyrite change. Worth flagging in the conductor's filing skill
  so it stops shipping off-schema entries to peer KBs. Filed externally
  if at all.
- **CLI message polish** — the bigger
  [[cli-error-shape-consistency]] ticket covers the "error_code +
  suggestion" envelope; this ticket should use that envelope when it
  lands but doesn't depend on it.

## Related

- [[bug-pyrite-update-doesnt-keep-index-in-sync-with-source]] —
  sibling Tier A meta-bug. Both are "write commands silently produce
  off-spec data that consumers discover later."
- [[bug-pyrite-sw-prioritize-renumbers-globally-clobbering-existing-ranks]]
  — same family.
- [[warn-on-undeclared-entry-type]] (done) — the read-side warning. This
  ticket is the write-side enforcement that pairs with it.
- [[warn-on-missing-type-fallback]] (done) — adjacent read-side check.
- [[enforce-backlog-status-enum-at-index-time]] (done) — the model for
  how to do this: enforce schema constraints at the boundary that
  produces data, not after the fact.
- ADR-0008 (Structured Data and Schema-as-Config) — the schema-layer
  ADR this enforces.

## Discovery context

Filed during the 2026-06-10 PO-triage of 6 conductor-filed tickets that
arrived with off-schema `type: task`, `status: open`, numeric priority.
The conductor was filing from its native task vocabulary because
`pyrite create` / `kb_create` accepted everything. Probe in commit
`b2e7ae8`'s scratchwork confirmed the bug (`pyrite create -k pyrite
-t bogus_type` succeeded silently). MCP-side validator runs but its
result is discarded (`mcp_server.py:748-751`).
