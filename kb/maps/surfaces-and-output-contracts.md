---
id: map-surfaces-and-output-contracts
title: "What do the CLI, MCP, REST and web return, and how large? Topic map: surfaces and output contracts"
type: note
tags:
- map
- design
- cli
- mcp
- rest
- web
- output
- contracts
- errors
- surfaces
---

# What do the CLI, MCP, REST and web return, and how large?

Answers "what does this command, tool or endpoint return, and how large?",
"what does a refusal look like?", "which surfaces are supported?". Layer 2
under [[design]] (P8, P9).

## The design today

1. One backend serves the CLI, the MCP server, REST and the web UI; AI and
   other features are services, not per-surface code. **decided** [[adr-0007]].
2. MCP tools come in read, write and admin tiers; plugins register tools per
   tier; MCP also offers prompts and resources. **decided** [[adr-0006]],
   [[adr-0007]]. For the local process a tier is a tool filter, not
   authorization. **decided** ADR-0043.
3. Every agent-reachable read has a bound and a continuation. MCP caps bodies
   (8,000 default, 20,000 ceiling, 40,000 per response; environment-tunable);
   the CLI is complete by default with `--body-limit` or `PYRITE_BODY_LIMIT`;
   REST is opt-in. Truncation always carries `body_truncated`, `body_length`,
   `body_offset`, `body_chunk_size`. **decided** [[adr-0034]].
4. A truncated body is never valid input to a write. **decided** [[adr-0034]].
5. Output format follows `Accept` or `--format` (json, markdown, yaml, csv,
   toon); formats are an output layer, never the file's format. **decided**
   [[adr-0010]].
6. Refusals share one contract: a code on the exception class, a public
   message, one mapping per transport. REST answers `{"detail": {"code",
   "message", ...}}`; MCP and CLI answer a canonical shape; for one release MCP
   also sends `legacy_error_code`. **decided, partly built** [[adr-0037]].
7. A live-update socket closes with its credential. **decided** [[adr-0036]].
8. `/site/` serves rendered static HTML, rebuilt on index sync. **decided**
   [[adr-0023]]; anonymous surfaces show only what the policy lets the
   anonymous principal read. **decided** ADR-0043 decision 8.
9. The API is the only security boundary; frontends are scoped clients that
   enforce nothing. **withdrawn** [[adr-0031]] (replaced by ADR-0043).
10. A small surface is supported (files, index, search, CLI, a core of MCP
    tools); the rest is marked experimental; a doc that teaches a command runs
    as its test. **proposed** `kb/designs/alpha-supported-surface.md`; [[design]]
    P9. The CLI contract is alpha (#303).

## Invariants a test could check

- Every REST operation and MCP tool is dispatched through the policy once;
  tool descriptions state the effective body bounds ([[adr-0037]],
  [[adr-0034]] decision 4).
- A parameter that reduces output (`fields`, `limit`) never disables another
  bound; `kb_batch_read` stays inside its per-response budget ([[adr-0034]]).
- A `body_truncated` input to `update` or `create` is refused on every surface
  ([[adr-0034]] decision 2).
- The same exception maps to the same code on REST, MCP and CLI, and a
  transport emits only `public_message` ([[adr-0037]] section 3).
- Concealment answers are byte-identical to not-found on every surface
  ([[adr-0037]] section 4).

## ADRs in reading order

[[adr-0007]], [[adr-0034]], [[adr-0037]] (sections 3 and 4), [[adr-0006]],
[[adr-0010]], [[adr-0036]], [[adr-0023]]; the alpha supported-surface entry
(proposed). Withdrawn: [[adr-0031]]. History only: [[adr-0018]].

## Where the code starts

Components [[rest-api]], [[mcp-server]], [[mcp-tool-schemas]], [[cli-system]],
[[web-frontend]], [[websocket-server]], [[sitecacheservice]],
[[static-file-server]], [[format-system]], [[llm-service]]. Paths:
`pyrite/server/mcp_server.py`, `pyrite/server/tool_schemas.py`,
`pyrite/server/errors.py`, `pyrite/services/body_bounds.py`, `pyrite/cli/`,
`docs/json-contracts.md`, `docs/agent-write-path.md`.

## Tests that pin it

`tests/test_body_bounds.py`, `tests/test_read_shaping_parity.py`,
`tests/test_truncated_body_refused_on_write.py`,
`tests/test_truncated_body_refused_on_cli_write.py`, `tests/test_mcp_tiers.py`,
`tests/test_api_tiers.py`, `tests/test_error_bodies.py`,
`tests/test_mcp_refusal_error_codes.py`, `tests/test_cli_errors.py`,
`tests/test_readme_mcp_tool_table.py`, `tests/test_mcp_help_tool_counts.py`
(the last two pin README counts; no ADR states them).

## Known gaps

- REST emits several error body shapes until the endpoint conversions of
  [[adr-0037]] themes 3b and 3c land. Theme 2 was to correct
  `docs/json-contracts.md` and the `api-design` standard; not verified.
- [[adr-0006]]'s tier contents are stale (72 / 103 / 112 tools); [[adr-0007]]
  lists eight AI endpoints and four exist (`/generate`, `/assist` and
  `/expand-query` were not built). The CLI starts the server at the write tier
  while the class defaults to read.
- Plugins cannot contribute to the frontend ([[adr-0031]], withdrawn); a public
  reader surface beyond `/site/` is open there.
- The alpha entry marks REST, the web UI, `/site/`, `/ws` and AI endpoints
  experimental; the web UI needs a build step and has open defects.
- Not decided: where `--force` applies in commands that already prompt
  (ADR-0041 question 1); whether the web UI should request bounded bodies for
  read-only views ([[adr-0034]]).
