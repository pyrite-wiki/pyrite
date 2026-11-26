---
id: bug-pyrite-get-omits-rank-field-in-json-output
title: "BUG: `pyrite get` JSON output silently omits the `rank` field even when set in source and index"
type: backlog_item
tags: [bug, cli, get, rank, backlog-grooming, projection, silent-data-loss]
importance: 5
kind: bug
status: done
priority: high
effort: XS
rank: 1200
---

## Problem

`pyrite get <id> -k <kb>` returns JSON in which the `rank` field is always
absent (or `None`), even when the source markdown declares `rank: <int>` in
frontmatter and the index has correctly ingested it.

Concrete repro from the 2026-06-05 backlog grooming pass:

```
$ grep '^rank:' kb/notes/ji-absorb-cascade-mcp.md
rank: 300

$ .venv/bin/pyrite get ji-absorb-cascade-mcp -k pyrite | python3 -c \
    'import json,sys; d=json.load(sys.stdin); print(d.get("rank"))'
None

$ .venv/bin/pyrite sw backlog | python3 -c \
    'import json,sys; [print(d["id"], d.get("rank")) \
     for d in json.load(sys.stdin) if "absorb-cascade-mcp" in d["id"]]'
ji-absorb-cascade-mcp 300
```

Source has the field. Index has the field. **`pyrite get`'s JSON projection
drops it.**

## Impact

- **Grooming hygiene is broken.** Any script that uses `pyrite get` to inspect
  ticket ordering (rank-aware queries, dependency-graph walks, conductor
  prioritization) sees every ticket as unranked. During the 2026-06-05
  grooming pass this misled the review into thinking the cascade-cluster
  sequencing had regressed — it hadn't, the CLI was lying.
- **Same family as the recent metadata-merge / metadata-string regressions:**
  a read-path projection that silently drops a load-bearing field. Same
  silent-data-loss class as
  [[bug-rest-update-endpoint-silently-dropped-metadata]] and
  [[bug-collection-entries-endpoint-metadata-string-pydantic-rejection]].

## Root cause (hypothesis to confirm during the fix)

`pyrite get`'s entry-to-dict projection (likely the same path as the REST
`/entries/{id}` endpoint, given last loop's regression touched both
together) does not include `rank` in its output schema. Most likely the
projection is enumerating a hard-coded field list that pre-dates `rank` (or
that treats `rank` as a software-kb-extension-only field even though it's
load-bearing in the core backlog protocol).

Worth checking whether the same omission applies to:

- `priority` and `effort` (might be fine — they show up in current
  `pyrite get` output)
- `assignee` (almost certainly affected by the same projection list)
- `due_date`, `start_date`, `end_date` (Temporal protocol fields)
- Anything else the `Prioritizable` / `Assignable` / `Temporal` protocols
  declare

## Fix

Audit the `pyrite get` projection against:

1. The `backlog_item` schema in `kb/kb.yaml` (or wherever the type is declared).
2. The protocol mixins (`Prioritizable`, `Assignable`, `Temporal`,
   `Parentable`) — fields they declare must appear in projections.
3. The behavior of `pyrite sw backlog` listing (which clearly does surface
   `rank`) — the get path should be a strict superset of what the listing
   shows for a single entry.

Adds a regression test: `pyrite get` on an entry with `rank: N` returns
JSON containing `rank: N`. Add sibling tests for the other protocol fields
identified in the audit so the next time a projection drops a field it
gets caught.

## Acceptance criteria

- `pyrite get <id> -k <kb>` JSON output includes `rank` when present in
  source/index.
- Sibling protocol fields (assignee, due_date, etc.) audited and included
  if they're also missing.
- Regression tests assert the projection contract — both for the bug-shape
  field (rank) and for at least one other previously-affected one.

## Related

- [[bug-rest-update-endpoint-silently-dropped-metadata]] — same family
  (read-path field silently dropped from projection).
- [[bug-update-entry-clobbers-existing-metadata-on-partial-update]] —
  companion fix that wired metadata through one projection; this is the
  parallel rank gap.
- [[cli-error-shape-consistency]] — adjacent CLI-output-contract work.
- Discovered during the 2026-06-05 backlog grooming pass — fixing this
  unblocks reliable rank-based grooming queries.

## Empirical reproduction confirmed (2026-06-11)

Reproduced cleanly during the Tier A audit loop pass:

- `pyrite get bug-create-silently-accepts-undeclared-types-...` JSON
  output contains no `rank` field at all (not even `null`) — the field is
  fully omitted from the projection.
- `pyrite sw backlog` shows `updated_at: None` for **all 525 backlog
  items**, including tickets touched seconds ago by `pyrite update`.
  Whatever indexing happens on update does not propagate to
  `updated_at`.

High-confidence bug; the fix is unblocked.
