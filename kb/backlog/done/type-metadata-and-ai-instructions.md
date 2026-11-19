---
id: type-metadata-and-ai-instructions
type: backlog_item
title: "Type Metadata and AI Instructions"
kind: feature
status: done
priority: high
effort: M
tags: [core, ai, types, schema]
---

# Type Metadata and AI Instructions

Add `ai_instructions`, field `description`, and `display` hints to type definitions. Types defined in kb.yaml or via plugins become self-documenting for AI agents, UI rendering, and API consumers.

## Scope

- Extend `TypeSchema` with `ai_instructions: str`, `field_descriptions: dict[str, str]`, `display: dict`
- Parse these from kb.yaml type definitions
- Add `get_type_metadata()` to plugin protocol (optional method)
- Implement 4-layer metadata resolution: kb.yaml → plugin metadata → dataclass introspection → core defaults
- Add core type defaults (descriptions + basic AI instructions for all 8 core types)
- Expose metadata in `kb_schema` MCP tool response
- Expose metadata in `GET /api/kbs/{name}/schema` API endpoint

## Key Files

- `pyrite/schema.py` — TypeSchema extension, metadata resolution
- `pyrite/plugins/protocol.py` — new `get_type_metadata()` method
- `pyrite/plugins/registry.py` — aggregate type metadata from plugins
- `pyrite/server/mcp_server.py` — enrich kb_schema and kb_create tool descriptions
- `pyrite/server/endpoints/kbs.py` — schema endpoint returns metadata
- `pyrite/models/core_types.py` — default descriptions for core types

## Acceptance Criteria

- [ ] kb.yaml types can declare `ai_instructions`, field `description`, and `display` hints
- [ ] Plugin types can provide metadata via `get_type_metadata()`
- [ ] KB-level metadata overrides plugin-provided metadata
- [ ] MCP `kb_schema` response includes type metadata
- [ ] Core types have default descriptions
- [ ] All existing tests pass (metadata is additive, not breaking)

## References

- [ADR-0009](../../adrs/0009-type-metadata-and-plugin-documentation.md)
- [ADR-0008](../../adrs/0008-structured-data-and-schema.md)
