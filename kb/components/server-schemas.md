---
id: server-schemas
title: REST API Schemas
type: component
kind: module
path: pyrite/server/schemas.py
owner: core
dependencies:
- pydantic
tags:
- core
- api
---

Single shared contract layer between all REST endpoint handlers and their callers. Defines Pydantic request/response models for every API domain — entries, search, KBs, collections, tags, graph, timeline, AI, settings, auth, repos, versions, blocks, clipper, and starred entries — ensuring consistent serialization, validation, and OpenAPI documentation across the full API surface (30+ models total).

## Key Methods / Classes

- `KBInfo`, `SearchResponse`, `EntryResponse`, `CreateEntryRequest` — KB and entry lifecycle models
- `GraphResponse` — graph visualization payload
- `AIAutoTagResponse` — AI-assisted tagging result
- `CollectionResponse` — collection membership and metadata
- `SettingsResponse` — user/system settings payload
- `ErrorResponse` — standardized error envelope

## Consumers

- All 17 endpoint modules under `pyrite/server/endpoints/`

## Related

- [[rest-api]] — FastAPI application that mounts these endpoints
