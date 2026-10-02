---
id: orient-is-true-as-the-first-call-66-declared-types-of-232
title: 'Orient is true as the first call (#66, declared types of #232)'
type: backlog_item
tags:
- 0.25.7
importance: 5
kind: bug
status: done
priority: high
assignee: agent:pyrite-worker-opus
effort: S
rank: 0
---

Theme: **orient is true as the first call** (milestone 0.25.7). Closes #66, and the declared-types part of #232 (plugin field lists stay out: decided docs-only for 0.25.7). The spec was the groom (`## Groom 2026-10-01` on #66) and the maintainer's decision under it.

**Done (PR #591):**

- `kb_orient` and `pyrite orient` answer with no name: the KBs the caller may read, the operational contracts, and what to call next (one shape for zero, one or many KBs).
- A bad KB name is `KB_NOT_FOUND` on MCP, the CLI and REST, with `did_you_mean` (readable KBs only) on orient. A scoped caller's refusal, for any MCP tool, is `KB_NOT_FOUND` too, with one static hint and no names; the old codes ride in `legacy_error_code` for one release.
- `detail` (`brief` or `full`; `full` stays the default until 0.25.8). `brief` omits the write-side blocks and names the commands that return them; it keeps `relationship_types`. `null` is accepted for `kb_name` and `detail`; a non-string `kb_name` is `VALIDATION_FAILED`.
- One rule for a KB's types: `KBSchema.declared_types()`, shared by the write refusal, orient, `kb_schema` and REST.

Tests: `tests/test_orient_is_true_as_the_first_call.py`. Docs: `docs/agent-write-path.md`, `docs/json-contracts.md`, `kb/components/mcp-server.md`.

**Not done:** `qa_analytics_service.py` still unions every core type into "declared"; the no-name listing runs one count per KB; REST has no no-name orient; unscoped callers still see three spellings of "no such KB".
