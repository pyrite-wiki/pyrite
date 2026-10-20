---
id: agent-oriented-error-responses-across-cli-and-mcp
title: Agent-oriented error responses across CLI and MCP
type: backlog_item
tags:
- cli
- mcp
- agents
- dx
- architecture
kind: enhancement
status: done
priority: high
effort: l
milestone: "0.13"
---

## Problem

Current error responses are designed for human developers — Python tracebacks, natural language messages, unstructured strings. For agent personas (CLI+AI and MCP), these errors impose unnecessary thinking token costs. The agent must interpret the error, reason about what went wrong, and decide whether to retry, change approach, or give up. Every ambiguous error message converts directly to thinking tokens spent on interpretation rather than action.

Three token costs are at play for agents:

- **Input tokens** (context): the error message itself, plus any docs/schema the agent re-reads to understand the failure
- **Thinking tokens** (deliberation): reasoning about what the error means, what caused it, what to try differently
- **Output tokens** (action): the retry attempt or workaround

A well-structured error minimizes all three. A raw traceback maximizes thinking cost and often triggers unnecessary re-reads (input cost) and blind retries (output cost).

## Proposal

Define a structured error format used consistently across CLI (`--format json`) and MCP tool responses:

```json
{
  "error": true,
  "code": "validation_failed",
  "message": "Field 'status' is not a recognized top-level parameter",
  "detail": "For type 'backlog_item', 'status' should be passed in the metadata dict",
  "suggestion": "Retry with metadata={\"status\": \"proposed\"}",
  "retryable": true
}
```

Key fields:
- **code**: Machine-parseable error category (validation_failed, not_found, serialization_error, permission_denied, schema_violation, etc.)
- **message**: One-line human-readable description
- **detail**: Context about why this specific call failed
- **suggestion**: What to do differently (the most valuable field for agents — directly reduces thinking tokens)
- **retryable**: Whether the same call might succeed on retry (transient vs. permanent failure)

For CLI human-readable output, these render as a formatted error message. For `--format json` and MCP, they return as structured JSON.

## Error categories to define

- `validation_failed` — entry didn't pass schema validation (include which field, which rule)
- `not_found` — entry/KB doesn't exist (include what was looked for)
- `permission_denied` — tier too low for this operation (include required tier)
- `serialization_error` — response couldn't be serialized (the PosixPath bug class)
- `schema_violation` — type-specific field constraints violated (include the constraint)
- `conflict` — concurrent modification (include current state)
- `limit_exceeded` — rate limit or size limit hit (include the limit and current value)

## Impact

Reduces agent token costs on every failed interaction. The `suggestion` field alone can eliminate entire retry cycles — instead of the agent reasoning about what to try differently, Pyrite tells it directly. This is measurable: fewer thinking tokens, fewer round-trips, lower cost per agent session.

Also improves human CLI experience — structured errors with suggestions are better for everyone.
