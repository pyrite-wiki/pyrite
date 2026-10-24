---
id: kb-registry-service
type: component
title: "KB Registry Service"
kind: service
path: "pyrite/services/kb_registry_service.py"
owner: "core"
dependencies: ["pyrite.config", "pyrite.storage.database", "pyrite.storage.index"]
tags: [core, service, kb]
---

DB-first unified KB lifecycle management. All surfaces (CLI, REST API, MCP, Web UI) delegate KB CRUD here. Config.yaml KBs are seeded as `source="config"`; user-added KBs are `source="user"`.

## Methods

| Method | Description |
|--------|-------------|
| `seed_from_config()` | Upsert config.yaml KBs into DB with source='config'. Idempotent |
| `list_kbs(type_filter)` | List all KBs from DB, enriched with config metadata (read_only, shortname) |
| `get_kb(name)` | Get a single KB from DB with config enrichment |
| `add_kb(name, path, kb_type, description)` | Register a new user KB in DB. Raises ValueError if exists |
| `remove_kb(name)` | Remove a user-added KB. Raises `KBProtectedError` for config KBs |
| `update_kb(name, **updates)` | Update KB metadata (description, kb_type, default_role) |
| `reindex_kb(name)` | Reindex a specific KB via IndexManager |
| `health_kb(name)` | Check KB health: path exists, file count vs index count, staleness |
| `get_kb_config(name)` | Build a `KBConfig` from a DB row (for DB-only KBs) |

## Architecture

Constructor takes `PyriteConfig`, `PyriteDB`, and `IndexManager`. Config KBs cannot be removed via the registry (raises `KBProtectedError`). User-added KBs can be fully managed.

The `list_kbs()` method enriches DB rows with config metadata (`read_only`, `shortname`) by looking up the `KBConfig` for each KB. This merges the two sources (config + DB) into a unified view.

## Consumers

- `GET /api/kbs` and KB CRUD endpoints in `kbs.py`
- KB management web UI at `/settings/kbs`
- Admin endpoints for KB creation/deletion

## Related

- [[kb-service]] — domain operations on KB entries (CRUD, search, export)
- [[storage-layer]] — PyriteDB stores KB registry rows
- [[rest-api]] — KB endpoints delegate to KBRegistryService

## Decided, not yet built

- [[adr-0039]] decisions 1 and 3, with [[adr-0041]]'s file-owner boundary: `KBRegistryService` still seeds and mutates the DB registry; it is not yet a read of the operator's registry file.
