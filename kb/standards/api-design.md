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

`pyrite.exceptions.public_text(exc)` is the one place that selects public
exception text and logs a hidden cause when a fixed public message replaces
it. Other modules do not read `.public_message` directly. This is enforced by
[test_public_message_logged_once.py](../../tests/test_public_message_logged_once.py).

A response to a network call carries only text Pyrite wrote for a caller
(ADR-0052 N3). An endpoint lets a `PyriteError` propagate, or raises one
with its code; it never builds a body, a result dict or a new exception's
message from a caught exception's text. Pass the exception through
`public_text(exc)` for a whole message, or `outside_text(exc)` for a cause
named inside a Pyrite message (`f"Invalid YAML frontmatter: {outside_text(e)}"`):
the local process (CLI, stdio MCP) gets the real text, a network caller
`public_text` for a `PyriteError` and "the server log has the details" for
anything else. A refusal of Pyrite's own that subclasses a built-in raises
`RefusedValueError` (a `ValidationError` and a `ValueError`), so its text is
still shown. Enforced by
[test_response_text_is_pyrites.py](../../tests/test_response_text_is_pyrites.py)
(an AST ratchet with an allow-list that only shrinks) and
[test_rest_bodies_carry_no_fault_text.py](../../tests/test_rest_bodies_carry_no_fault_text.py)
(every `/api` route and MCP tool, with a `KBService` fault injected).

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
