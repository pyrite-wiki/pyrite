---
id: content-negotiation-and-formats
type: backlog_item
title: "Content Negotiation and Multi-Format Support"
kind: feature
status: done
priority: high
effort: L
tags: [api, mcp, cli, formats, ai]
---

# Content Negotiation and Multi-Format Support

Add Accept header content negotiation to the API, format-aware MCP responses, and CLI `--format` flag. Entries can be returned as JSON, Markdown, TOON, CSV, YAML, or plain text depending on the consumer's needs.

## Scope

### Phase 1: Format Registry and API Content Negotiation
- Create `pyrite/formats/` module with serializer/deserializer registry
- Implement JSON serializer (wrap existing Pydantic behavior)
- Implement Markdown serializer (wrap existing `to_markdown()`)
- Implement YAML serializer (frontmatter only, via ruamel)
- Implement CSV serializer (for list endpoints — search, titles, tags, timeline)
- Add `negotiate_response()` helper that reads `Accept` header and selects format
- Wire into all API endpoints

### Phase 2: TOON Support
- Add `toon-python` as optional dependency in `[ai]` extra
- Implement TOON serializer/deserializer
- Graceful fallback when toon-python not installed
- Particularly effective for search results, entry listings, stats

### Phase 3: MCP and CLI
- MCP tool responses include `mimeType` in content blocks
- MCP can prefer TOON for list responses when available
- CLI gains `--format` flag (json, csv, toon, markdown, yaml)

## Key Files

- `pyrite/formats/__init__.py` — format registry, negotiate_response()
- `pyrite/formats/json.py` — JSON serializer (thin wrapper)
- `pyrite/formats/markdown.py` — Markdown serializer
- `pyrite/formats/csv.py` — CSV serializer for tabular data
- `pyrite/formats/toon.py` — TOON serializer (optional dep)
- `pyrite/formats/yaml.py` — YAML frontmatter serializer
- `pyrite/server/endpoints/` — wire content negotiation into endpoint modules
- `pyrite/server/mcp_server.py` — mimeType in content blocks
- `pyrite/cli/__init__.py` — --format flag
- `pyproject.toml` — toon-python in [ai] optional deps

## Acceptance Criteria

- [ ] API returns JSON by default (backwards compatible)
- [ ] `Accept: text/markdown` returns full markdown with frontmatter
- [ ] `Accept: text/csv` returns CSV for list endpoints
- [ ] `Accept: text/toon` returns TOON when toon-python installed, JSON otherwise
- [ ] Unsupported Accept types return 406 Not Acceptable
- [ ] MCP responses include mimeType field
- [ ] CLI `--format` flag works for all output commands
- [ ] Format registry is extensible (plugins could add formats later)

## References

- [ADR-0010](../../adrs/0010-content-negotiation-and-format-support.md)
- [TOON format](https://github.com/toon-format/toon)
