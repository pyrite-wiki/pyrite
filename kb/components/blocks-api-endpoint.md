---
id: blocks-api-endpoint
title: Blocks API Endpoint
type: component
kind: endpoint
path: pyrite/server/endpoints/blocks.py
owner: core
dependencies: [storage-layer, block-model]
tags:
- api
- block-refs
---

REST endpoint for block-level access to entry content.

- `GET /api/entries/{entry_id}/blocks` — returns blocks for an entry
  - Query params: `heading` (filter by heading), `block_type` (filter by type)
  - Returns `BlockListResponse` with `blocks[]` and `total` count
  - Ordered by position within the entry

Supports heading-level linking: <code>&#91;&#91;entry#heading&#93;&#93;</code> links resolve through this endpoint.
