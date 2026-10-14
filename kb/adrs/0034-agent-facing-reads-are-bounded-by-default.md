---
id: adr-0034
type: adr
title: "Agent-facing reads are bounded by default"
adr_number: 34
status: accepted
date: 2026-09-18
---

# ADR-0034: Agent-facing reads are bounded by default

## Context

A read that returns more than its caller can hold is a failed read. For an
agent the limit is the context window and the client's tool-output ceiling;
for a chat UI it is what the interface can render without becoming unusable.
Pyrite's KBs routinely hold bodies past both.

Measured on the maintainer's index, 2026-09-18 (18,911 entries with a body,
`length(body)` in characters): p50 2,736 · p90 16,629 · p95 27,793 · p99
52,822 · max 1,266,476. 3,916 entries (21%) exceed 8,000 characters, 1,523
(8%) exceed 20,000, 239 (1.3%) exceed 50,000.

The rule this ADR records already exists in the code, unwritten, in two
halves:

- **Lists are paginated.** `mcp-large-result-handling` (done) gave
  `kb_search`, `kb_timeline`, `kb_backlinks` and `kb_tags` a `limit` and
  `offset` pushed down to SQL and a `has_more` flag, because oversized
  results were being spilled to JSON files the agent then had to parse with
  shell pipelines.
- **Bodies are chunked.** `kb_get` and `kb_batch_read` pass every body
  through `_chunk_body` (`DEFAULT_BODY_CHUNK = 8000`, `MAX_BODY_CHUNK =
  50_000`); a truncated body carries `body_truncated`, `body_length`,
  `body_offset`, `body_chunk_size`, and `kb_read_body` continues it with
  `has_more`.

Because it was never written down, it was not applied uniformly, and a
reviewer could argue against it in good faith:

- The `fields` projection skipped chunking entirely, by documented contract.
  In the read-tier hallway test (#58) `kb_batch_read(fields=[..., "body"],
  body_limit=6000)` returned 171,189 characters — 28× the explicit cap —
  and blew the client's tool-output ceiling. When a contributor closed that
  hole (#166), the conductor's review asked them to reopen it to preserve
  the old contract; the maintainer corrected it.
- The per-body ceiling is 50,000 characters (~12k tokens), and
  `kb_batch_read` applies it per entry, not per response: twenty entries at
  the ceiling is a million characters. The maintainer's experience is that
  50,000 is already enough to make many UIs unusable.
- The CLI has no body bound at all, and most of its callers today are
  agents (Claude Code and similar) for whom the context window matters as
  much as it does over MCP. But the CLI **also serves scripts and, at a
  terminal, the maintainer**: a script that pipes `pyrite get --format json`
  onward and silently receives a truncated body has been handed corrupt
  data.

## Decision

1. **The principle.** Every read surface an agent can reach has a bound on
   what one call returns, and a way to continue. A parameter that reduces
   output (`fields`, `limit`) never disables another bound.

2. **Truncation is never silent and never writable.** A truncated body
   always carries the marker (`body_truncated: true`, `body_length`,
   `body_offset`, `body_chunk_size`) — projection through `fields` keeps
   those keys — and the response or message names the continuation
   (`kb_read_body`, `--body-offset`). A truncated body is never valid input
   to a write: a write path that receives `body_truncated` refuses.

3. **MCP: bounded by default.** Bodies are capped at the default chunk
   whether or not the caller passed `body_limit`, on every path including
   `fields`. Lists keep `limit`/`offset`/`has_more`.

4. **The numbers** (proposed; the maintainer sets them):
   - default chunk stays **8,000** characters — 79% of measured bodies
     arrive whole;
   - per-body ceiling drops from 50,000 to **20,000** — 92% of bodies still
     fit in one call at the ceiling, and the remainder continue with
     `kb_read_body`;
   - multi-entry reads (`kb_batch_read`, and any future tool returning
     several bodies) gain a **per-response budget of 40,000 body
     characters**: bodies are filled in request order, the ones that do not
     fit come back truncated to what remains (possibly zero) with the
     marker, so the response size is bounded no matter how many entries
     were asked for.

   **All three are configuration, not constants** (maintainer, 2026-09-18):
   each reads an environment variable at server start and falls back to the
   default above — `PYRITE_BODY_CHUNK_DEFAULT` (8000),
   `PYRITE_BODY_CHUNK_MAX` (20000), `PYRITE_BODY_RESPONSE_BUDGET` (40000) —
   so a deployment whose clients have smaller or larger windows tunes them
   without a code change. Invalid values (non-integer, ≤ 0, default above
   max) fail loudly at start rather than silently falling back. Tool
   descriptions report the effective values, not the compiled-in ones.

5. **CLI: one switch, off by default.** The CLI's default output stays
   complete, because scripts depend on it and a human at a terminal expects
   it. Agents get the bound with one switch rather than a flag per command:
   `--body-limit N` / `--body-offset N` on every command that prints a body
   (`get`, `search --include-body`, exports to stdout), and the environment
   variable **`PYRITE_BODY_LIMIT`**, which applies the same limit to every
   such command in that environment. Agent-facing setup (the MCP/CLI docs,
   `CLAUDE.md` templates, the skills) sets it; scripts and terminals do not.
   When a limit truncates, `--format json` carries the same `body_*` keys
   as MCP and a one-line notice goes to stderr, so stdout stays parseable.

6. **REST: opt-in, same parameters.** `GET /api/entries/{id}` and batch
   reads accept `body_limit`/`body_offset` with the same marker keys, and
   default to the full body: the web editor round-trips whole bodies, and a
   default bound there is the data-loss shape rule 2 forbids. List endpoints
   keep `limit`/`offset`.

7. **Contracts.** The `body_*` keys and the `has_more` flag join
   `docs/json-contracts.md`; tool descriptions state the default and the
   ceiling in numbers.

## Alternatives considered

- **CLI bounded by default, `--full` to opt out.** Safest for agents that
  were never configured, and what MCP does. Rejected as the proposal because
  the failure is silent corruption for every existing script, where the
  failure of the chosen design is a loud one for an unconfigured agent (an
  oversized result it can see). The maintainer may still prefer it; if so,
  rule 2's stderr notice and the `body_*` keys are what make it tolerable.
- **Detect the caller** (TTY vs pipe, `--format json` vs rich). Agents and
  scripts both pipe and both ask for JSON; there is no signal that separates
  them.
- **Token-based rather than character-based bounds.** More honest about the
  real constraint, but needs a tokenizer per client; characters are a stable
  proxy (÷4) and what the code already counts.

## Consequences

- #58's fix in #166 is correct as written; the old "`fields` skips
  chunking" contract is retired, which is a behaviour change for callers
  using `fields=[..., "body"]` and goes in the changelog as one.
- Lowering the ceiling to 20,000 and adding the per-response budget are
  behaviour changes for MCP clients that pass large `body_limit`s today;
  they continue with `kb_read_body` instead.
- New work, each a small theme: the ceiling and the batch budget in
  `mcp_server.py`; the write-path refusal of truncated bodies;
  `--body-limit`/`PYRITE_BODY_LIMIT` in the CLI; `body_limit` on REST entry
  reads; the contracts doc.
- Reviewers — human or agent — have a rule to cite, and "it is documented
  that the bound is skipped" stops being a defence of an unbounded read.

## Maintainer's read, 2026-09-18

Agreed in direction: the defaults in rule 4 "perhaps make sense" and must be
configurable by environment variable (now in rule 4); the CLI serves agents,
scripts and the maintainer at a terminal, so its default stays complete with
the bound one switch away (rule 5).

**Accepted by the maintainer, 2026-09-18** ("I have accepted 170"), with the
shipped defaults 8,000 / 20,000 / 40,000 tunable by the environment variables
in rule 4. The five implementation themes (`adr-0034-i` … `adr-0034-v` in the
backlog) are unblocked, sequenced as the serial queue says.

## Amendment, proposed 2026-10-14 (the maintainer's to accept)

Issue #54, decided by the maintainer on 2026-09-30: the CLI's `search` command
(and `pyrite-read search`) now returns a **lean row per result** by default
(`id`, `kb_name`, `entry_type`, `title`, `snippet` and the score key), with
`--fields '*'` or `PYRITE_SEARCH_FIELDS='*'` for the whole row. Rule 5's
statement that the CLI's default output stays complete "because scripts depend
on it" no longer holds for the *field set* of search results; it still holds
for bodies (`--body-limit` stays off by default). The reasoning that kept the
body whole does not carry over: a dropped body can look complete when it was
cut, while a dropped field is absent from the row, so reading it fails loudly
instead of answering wrongly. Page-level verdict keys are unaffected. MCP and
REST search keep the whole row.

## Open question

1. Whether the web UI should itself request bounded bodies for read-only
   views (entry cards, search results) while the editor asks for the whole.
