---
id: api-design
title: "API & MCP Tool Design"
type: standard
tags: [api, mcp]
importance: 5
category: api
---

## MCP Tool Naming
- Plugin tools prefixed with short name: `sw_`, `zettel_`, `wiki_`
- Read tools: list/query operations
- Write tools: create/update operations
- Admin tools: management operations

## Tool Schema
Every MCP tool must have:
- `description` — clear, actionable description
- `inputSchema` — JSON Schema with type, properties, required
- `handler` — method reference on the plugin class

## Error Handling

Codes live on the exception class (ADR-0037 theme 2): every `PyriteError`
subclass (`pyrite/exceptions.py`) carries an `error_code`, and every surface
derives its code from the class, with no per-transport lookup table. The
wire shape per surface (CLI/MCP flat object, REST `detail` wrapper), the
`legacy_error_code` transition and every code with its remedy are in
`docs/json-contracts.md`, the one home for them; this standard does not
restate them. For an extension author the rule is:

Build errors via the shared helper (`pyrite/utils/errors.py`'s
`build_error`/`cli_error`/`cli_error_from` for CLI, MCP's `_error()`/
`_refusal()` in `server/mcp_server.py`, or REST's `server/errors.py`) —
never hand-roll `{"error": "message"}`. The full canonical contract is `docs/json-contracts.md`.

Return guidance for file creation (create tools don't write files
directly, they return instructions).

## Provenance

The error-shape convention above was landed and swept CLI-wide in
[[cli-error-shape-consistency]] (done) — see that ticket for the
mechanical conversion history. This standard, not the closed ticket,
is the canonical reference going forward.
