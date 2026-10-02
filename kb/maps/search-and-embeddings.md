---
id: map-search-and-embeddings
title: "What does search accept, and does a write wait for embedding? Topic map: search and embeddings"
type: note
tags:
- map
- design
- search
- embeddings
- fts5
- semantic
- query
- auto-embed
---

# What does search accept, and does a write wait for embedding?

Answers "what does search accept?", "why is semantic search empty?", "does a
write wait for embedding?". Layer 2 under [[design]] (P4).

## The design today

1. Keyword search is FTS5 on SQLite and `tsvector` on Postgres; semantic
   search uses embeddings (sqlite-vec, pgvector); hybrid combines them.
   **decided** [[adr-0015]], [[adr-0016]].
2. Every engine sits behind `SearchBackend` with one conformance suite;
   promoted filters (`fips`, `state`, status, dates) are threaded through
   backend, service, MCP and CLI. **decided** [[adr-0015]], [[adr-0026]].
3. A write with `auto_embed: true` enqueues the entry and returns; it never
   loads the model and never blocks on the network. The queue drains on
   `index embed`, `index sync`, `index build`, server prewarm and the end of
   `POST /api/index/sync`. `auto_embed: false` touches no embedding stack.
   **decided** [[adr-0035]].
4. Semantic search over a KB with no embeddings returns a warning that names
   `pyrite index embed`, never a silent empty list. **decided** [[adr-0035]].
5. Every agent-reachable list has `limit`, `offset` and `has_more`, pushed down
   to SQL; bodies are bounded and continuable. **decided** [[adr-0034]].
6. Search results are scoped to what the principal may read; a cross-KB service
   takes a `ReadScope` only the policy constructs. **decided** [[adr-0037]].
7. One query language for callers, parsed to an AST and compiled per backend;
   operators are positional and fielded terms use a closed vocabulary; a
   zero-hit query relaxes or falls back to semantic; syntax errors are
   structured. **accepted, not built** [[adr-0028]]. Callers still pass FTS5
   syntax through `sanitize_fts_query`.
8. LLM query expansion is optional and provider-agnostic. **decided**
   [[adr-0007]]; the `/api/ai/expand-query` endpoint it lists was not built.
9. Search and list reads may lag the files and report when they were indexed.
   **proposed** ADR-0042 decision 9.

## Invariants a test could check

- Every backend passes the same conformance tests ([[adr-0016]]).
- With a worker attached and `auto_embed: true`, a write imports no embedding
  model, leaves the entry keyword-searchable, and records one pending row;
  `auto_embed: false` enqueues nothing ([[adr-0035]]).
- Semantic search with zero embeddings yields a `warnings` entry
  ([[adr-0035]] decision 5).
- No search result carries an entry from a KB the caller cannot read
  ([[adr-0037]]; `tests/test_read_scoping_is_structural.py`).
- Operator-shaped literals (`alex-jones`, `24-109`, `Louisiana v. Callais`)
  parse as terms; a malformed query returns a structured error, not a raw
  backend exception ([[adr-0028]], not yet testable).

## ADRs in reading order

[[adr-0015]], [[adr-0016]] (the benchmark and the seam), [[adr-0035]],
[[adr-0034]], [[adr-0026]], [[adr-0028]] (read its audit standing first: not
built). Background, not decisions: `search-failure-modes-and-agent-interface`
and `search-relevance-boost-by-entry-type` (designs, draft).

## Where the code starts

Components [[search-service]], [[embedding-service]],
[[background-embedding-worker]], [[search-backend-protocol]],
[[sqlite-backend]], [[postgres-backend]], [[query-expansion-service]]. Paths:
`pyrite/services/search_service.py` (`sanitize_fts_query`),
`pyrite/services/embedding_worker.py`, `pyrite/storage/backends/`.

## Tests that pin it

`tests/backends/test_backend_conformance.py`,
`tests/test_semantic_search_warns_when_nothing_is_embedded.py`,
`tests/test_auto_embed_setting.py`, `tests/test_writes_never_block_on_embedding.py`,
`tests/test_embedding_queue.py`, `tests/test_embed_queue_debt_is_honest.py`,
`tests/test_server_write_embedding_debt.py`, `tests/test_body_bounds.py`.

## Known gaps

- [[adr-0028]] has no parser, AST or compiler; hyphens and colons still reach
  FTS5; there is no in-query `status:` filter. The backlog item for it is
  `proposed`.
- Semantic search is eventually consistent after a write; the queue is per
  index database, so a rebuild from files loses pending rows ([[adr-0035]]).
  A `failed` row after `max_attempts` must be legible in `index health`.
- The alpha supported-surface entry (proposed) reports 3.5 seconds per CLI call
  to load the local model and a silent failure when the semantic extra is
  missing (#43).
- Not decided: whether bounded bodies apply to read-only web views
  ([[adr-0034]] open question).
