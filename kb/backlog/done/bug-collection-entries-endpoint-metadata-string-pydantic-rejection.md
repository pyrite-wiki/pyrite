---
id: bug-collection-entries-endpoint-metadata-string-pydantic-rejection
title: "BUG: /collections/{id}/entries crashes with Pydantic ValidationError — metadata returned as JSON string instead of dict"
type: backlog_item
tags: [bug, rest-api, collections, metadata, schema, pydantic, regression]
importance: 5
kind: bug
status: done
priority: high
effort: S
rank: 0
---

## Problem

Two REST tests have been failing in `tests/test_collections.py::TestCollectionAPI`:

- `test_get_collection_entries_endpoint`
- `test_get_collection_entries_pagination`

Both fail with the same Pydantic validation error inside the FastAPI handler:

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for EntryResponse
metadata
  Input should be a valid dictionary [type=dict_type, input_value='{}', input_type=str]
```

The endpoint `GET /collections/{collection_id}/entries`
(`pyrite/server/endpoints/collections.py:219`) constructs `EntryResponse(**r)`
for each row returned by `svc.get_collection_entries(...)`. The `r["metadata"]`
field arrives as a JSON-encoded **string** (the raw column value from SQLite),
but `EntryResponse.metadata` is typed `dict = {}` (Pydantic) and rejects the
string.

## Root cause

`EntryResponse.metadata: dict = {}` was added in commit `d0e2677` ("Fix
metadata clobbering on partial update; wire metadata through REST") so REST
clients could read/write metadata. That commit fixed the single-entry
`GET /entries/{id}` path (which goes through `KBService.get_entry` → backend
`_entry_to_dict` → `_parse_metadata` → dict) but did not exercise any of the
**raw-SQL list paths**.

The collection-entries fetch goes through a different code path:

1. `KBService.get_collection_entries` calls `db.list_entries_in_folder(...)`
2. `list_entries_in_folder` (`pyrite/storage/backends/base_backend.py:939`)
   uses `self._exec(sql, params)` — raw SQL returning dicts straight from
   the DB. **No metadata parsing.**
3. The endpoint passes the unparsed rows to `EntryResponse(**r)` → bang.

The two backends each have ~10+ raw `_exec` list call sites that share this
shape (`base_backend.py` lines 576, 579, 810, 827, 909, 916, 926, 961, 1001,
1014; `postgres_backend.py` lines 219, 236, 252, 269). Most of them have not
been exercised against the new `EntryResponse.metadata: dict` contract yet,
so additional latent regressions are likely under the same root cause.

## Impact

- `/collections/{id}/entries` returns 500 instead of the entry list. The web
  UI's folder/collection views silently fail with no actionable error.
- Pre-existed since `d0e2677` (~last session). The full-suite regression
  surfaced now because the prior session arc relied on targeted-suite runs
  and never exercised `test_collections.py`.
- The class of bug is broader: any endpoint consuming a list-path return
  value will hit it once it relies on `EntryResponse.metadata` being a dict.

## Fix (this ticket — narrow)

Parse `metadata` (and `extra_data`) at the service-layer boundary in
`KBService.get_collection_entries` and `_get_query_collection_entries`:
each returned row gets its `metadata` field run through
`pyrite.utils.metadata.parse_metadata(...)` before being handed to callers.
That closes the two failing tests and aligns the collection-entries path
with the single-entry contract.

Add a regression test asserting `EntryResponse(**r)` succeeds for a row
returned by `get_collection_entries` so the next time this bug class
reappears at a sibling call site it gets caught.

## Future work (separate ticket)

The principled fix is *at `_exec`* — post-process every row returned by raw
SQL to parse `metadata`/`extra_data` once, so every list path inherits the
same contract as `_entry_to_dict` already provides for single-entry reads.
That removes the divergence between ORM and raw-SQL paths and prevents the
remaining ~13 latent regressions from being discovered one bug report at a
time.

That fix interacts with [[split-backend-protocol-entitystore-searchengine-embeddingstore]]
(the Backend Protocol split) — when `EntityStore` is extracted, the
"all reads through this layer return parsed metadata" contract belongs in
the EntityStore spec. Best done as part of that work rather than as a
standalone change to `_exec`. Tracked in the EntityStore ticket's
acceptance criteria as a side benefit.

## Related

- Commit `d0e2677` — origin of the regression (typed `metadata: dict` in
  `EntryResponse` without parsing on every read path).
- [[bug-update-entry-clobbers-existing-metadata-on-partial-update]] —
  companion fix from the same review-flow integration pass.
- [[split-backend-protocol-entitystore-searchengine-embeddingstore]] —
  carries the broad "fix `_exec` once" cleanup as a side benefit.
- [[postgres-backend-swallows-query-errors]] — adjacent bug in the same
  raw-SQL path (`_exec` swallows errors as `[]`).
