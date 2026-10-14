---
id: adr-0028
title: "Backend-Agnostic Query DSL"
type: adr
tags: [search, dsl, backend, architecture, agents]
importance: 5
adr_number: 28
status: proposed
deciders: ["markr"]
date: "2026-06-23"
---

# ADR-0028: Backend-Agnostic Query DSL

> **Deferred by the maintainer, 2026-10-03.** The query parser was accepted but never built: no parser, AST or
> compiler exists, and the legacy FTS5 sanitizer is the only path. The status
> moves from `accepted` to `proposed`, because `deferred` is not an allowed ADR
> status and `proposed` is the nearest value (the commitment is not current).
> The decision text below is unchanged. Revisit when a backend needs it.

## Context

Pyrite's search interface leaks the storage backend's query language to the caller. A
query string handed to `SearchService.search()` is interpreted as **raw SQLite FTS5
syntax**: hyphens become `NOT`, colons become column filters, dots become column
prefixes, and `@ # / ! ~ =` are syntax errors. The string is passed almost directly to
`entry_fts MATCH ?` (`pyrite/storage/backends/sqlite_backend.py:167`).

The only buffer is `SearchService.sanitize_fts_query()`
(`pyrite/services/search_service.py:68`) — a single regex that quotes special-char tokens,
but otherwise passes any query containing ` AND `/` OR `/` NOT `/`"` straight through to
FTS5 untouched (line 90). This is a patch *over* FTS5, not an abstraction *above* it.

Three problems follow, all observed in a live daily-capture run on 2026-06-23:

1. **Callers must know the backend to write a query.** Agents are the primary consumers of
   search; an agent that writes `actor:"Jane Doe"` or `status:unprocessed` or a hyphenated
   name gets FTS5-specific behavior or a raw `sqlite3.OperationalError`, not a clean
   result. This is the load-bearing complaint: agent (and human) search should "just work"
   without backend knowledge.
2. **Common metadata filters are not first-class in the query.** Filtering by `status` is
   routine, but there is no in-query form and no CLI `--status` on search at all (the
   `find_by_status()` query exists but is unwired — see backlog `search-status-filter-cli`).
3. **Backends diverge.** `PostgresBackend` (`pyrite/storage/backends/postgres_backend.py`)
   uses `tsquery`, an entirely different syntax. The same query string is not portable, so
   any "query language" we expose is implicitly SQLite-only.

Two adjacent reliability defects share the same root and are cheapest to fix here:
brittle implicit-AND recall with no relaxation/fallback (backlog
`search-keyword-and-no-fallback`), and raw backend exceptions surfacing on malformed input.

This is the right time to act: **no major users are on Postgres yet**, so the
`BaseBackend` search seam can change without a data migration. The seam itself is already
clean — `BaseBackend` is an ABC with `search()`/`search_semantic()` and a
`capabilities.py` dispatch map (the same seam ADR-0017 and ADR-0026 threaded filter params
through), which is exactly where a query compiler and, later, an alternate engine plug in.

## Decision

Introduce a **backend-agnostic query layer**: one neutral query DSL that callers learn
once, parsed into a backend-independent **Abstract Syntax Tree (AST)**, then **compiled per
backend** into that backend's native query.

### Surface syntax (one language for agents and humans)

- Boolean + grouping: `ICE AND (detention OR deportation)`, leading `-` to exclude,
  `"exact phrase"` for phrases, `term*` for prefix.
- Fielded filters in-query:
  `status:unprocessed`, `tag:surveillance`, `type:event`, `kb:cascade-timeline`,
  `actor:"Jane Doe"`, `date:>2026-01-01`, `date:2026-01-01..2026-03-31`.
- Bare terms are full-text. Field names are a fixed, documented vocabulary so an agent can
  enumerate them.

### Literal escaping and the lexical boundary

The corpus is full of operator-shaped literal content — hyphenated names (`alex-jones`),
case captions (`Louisiana v. Callais`), docket and bill IDs (`24-109`, `S.10642`), section
marks (`§2`), versioned slugs (`0.6-milestone`). A grammar that owns `- : * " ( ) ..` as
operators must have an unambiguous rule for when those characters are *content*, not syntax.
The rule is **operator status is positional, and any token can be forced literal**:

1. **`-` (exclude) and `*` (prefix) are operators only at a token boundary.** A leading `-`
   excludes (`-detention`); a trailing `*` is prefix (`deten*`). Inside a token they are
   literal: `alex-jones`, `24-109`, and `0.6` parse as single full-text terms, never as
   `alex NOT jones` or a column filter. `.` is **never** an operator except in the explicit
   `..` range form inside a `date:`/numeric field value.
2. **`:` is a field separator only when the left side is a member of the fixed field
   vocabulary.** `status:unprocessed` is a fielded filter; `ratio:2` or `note:foo` (unknown
   left side) parses as the literal full-text term `ratio:2`. This makes the field set a
   closed, enumerable namespace and lets every other colon be content — an agent never has to
   escape a stray `:`.
3. **Quoting forces literal, unconditionally.** `"Louisiana v. Callais"` is a phrase;
   `tag:"end-to-end"` is a fielded filter whose *value* is the literal `end-to-end` (the
   value side of a fielded term is parsed as a bare term or a quoted literal, never as
   nested operators). A quoted token is passed to the compiler as data, never re-lexed.
4. **An explicit literal escape exists for the residual cases.** A backslash escapes the next
   character (`\-`, `\:`, `\*`), and `raw:"…"` (or the `--literal` CLI flag / `mode:"literal"`
   AST option) submits the entire string as one full-text phrase with no operator parsing at
   all. This is the guaranteed escape hatch when a caller does not want to reason about the
   grammar; the legacy `sanitize_fts_query` auto-quoting behavior is preserved here as the
   `raw`/`--literal` path rather than lost.

Consequence: the common agent and human cases — a bare hyphenated name, a docket number, a
quoted case caption — **just work with no escaping**, because operators are recognized only at
token boundaries and only against the closed field vocabulary. Escaping is required only to
*invoke* an operator on a token that would otherwise be literal, which is the rare direction.
The parser's precedence/escaping/injection test matrix (see Consequences) must cover each of
these four rules against the operator-shaped literals above as fixtures.

### Pipeline

```
query string ──parse──▶ AST ──compile──▶ backend query
                         │
                         ├─ full-text terms  → backend text-match dialect (FTS5 MATCH / tsquery / engine)
                         └─ fielded terms     → structured filter params on BaseBackend.search()
                                                 (kb / entry_type / tags / date_from / date_to /
                                                  status / lifecycle / fips / state — the ADR-0026 pattern)
```

Fielded terms compile to the **existing structured filter parameters**, not to FTS column
syntax. This reuses the indexed columns established by ADR-0017 and ADR-0026 rather than
inventing a parallel filtering path. Adding `status` (and any future promoted column) to
that param set is the same mechanical thread ADR-0026 described for `fips`/`state`.

### Compiler contract

A backend advertises a `query_compiler` (via `capabilities.py`) that turns an AST into its
native form. SQLite ships first (AST → FTS5 `MATCH` string + structured params). Postgres
(`tsquery`) and any future engine implement the same interface. Callers — CLI, MCP
`kb_search`, REST, the web UI — only ever construct the DSL string or the AST; they never
emit backend syntax.

### Folded-in robustness

- **0-result fallback** lives in the AST executor: on empty keyword hits, relax AND→OR, or
  fall back to semantic search (resolves `search-keyword-and-no-fallback`).
- **Clean syntax errors**: parse failures and backend `MATCH` errors are caught and
  returned as a structured "invalid query" message, never a raw `sqlite3.OperationalError`.

### Phasing

1. **Phase 1 — DSL + FTS5 compiler + fielded filters + fallback + baseline column
   weighting.** Replaces `sanitize_fts_query` as the chokepoint; compiles fielded terms to
   existing params; adds the 0-result fallback and clean errors. **Includes FTS5 BM25
   per-column weighting (title ≫ summary > body) as a first-class part of the FTS5 compiler,
   not a later add-on.** Rationale: in this corpus the title is by far the highest-signal
   field — ~5,400 timeline events whose titles front-load the who/what/when, while bodies are
   long and, on the semantic path, clipped to ~500 chars before embedding
   (`embedding_service.py:34`). Un-weighted `bm25()` ranks a body mention equal to a title
   match, so for the queries agents actually run (named actor, event, date), column weighting
   moves real recall/precision more than the fallback does. It is cheap — a weight vector in
   the `bm25()` call behind the compiler — and shipping it in Phase 1 means the DSL is
   benchmarked against *weighted* ranking from the start, rather than tuning relevance twice.
   Ships behind a flag with the legacy path as fallback during rollout.
2. **Phase 2 — Relevance tuning + benchmark harness behind the seam.** Calibrate the Phase 1
   weight vector against a labeled query set (named-actor, event-title, date-range, fielded),
   add per-`entry_type` weighting if the benchmark warrants, and lock in a regression
   benchmark so future changes are measured. No caller changes; the DSL is unchanged.
3. **Phase 3 — Alternate engine backend.** If typo-tolerance / Lucene-grade relevance is
   required, implement an embedded `TantivyBackend(BaseBackend)` + AST→Tantivy compiler
   (preserves the local-first, no-daemon property). A server-side engine
   (Meilisearch/Typesense) becomes an additional backend once the multiuser server is the
   primary deployment. In every case the DSL is unchanged — callers don't move.

## Consequences

### Positive

- **Agents and humans write one query language**, independent of the configured backend —
  the primary goal. No more "you must know FTS5."
- **Fielded filtering (`status:`, `tag:`, `type:`, …) becomes first-class** and uniform
  across CLI / MCP / REST / web, reusing the ADR-0017/0026 indexed columns.
- **Backend portability**: SQLite and Postgres return equivalent results for the same query
  for the first time; swapping in a search engine later is a backend change, not an API
  change.
- **Brittle recall and raw exceptions are fixed at the source** — implicit-AND zero-results
  via the 0-result fallback, low ranking quality via Phase 1 title-weighted BM25, and raw
  `sqlite3.OperationalError` via structured error handling — rather than as one-off patches.
- The change lands while the seam is free to move (no Postgres migration cost today).

### Negative

- New surface area: a parser, an AST, and one compiler per backend, each needing a
  precedence/escaping/injection test matrix. That matrix is non-trivial: it must exercise
  the four lexical-boundary rules (positional `-`/`*`, closed-vocabulary `:`, unconditional
  quoting, explicit `\`/`raw:` escape) against the operator-shaped literals that pervade the
  corpus — hyphenated names (`alex-jones`), case captions (`Louisiana v. Callais`), docket
  and bill IDs (`24-109`, `S.10642`), section marks (`§2`), versioned slugs
  (`0.6-milestone`) — as fixtures, plus precedence/grouping and FTS5/`tsquery` injection
  safety. The parser is small (~200 lines); this matrix and per-backend parity are where the
  cost concentrates.
- A second query language to document and version. Mitigated by a fixed field vocabulary
  and by keeping bare-term queries behaving as users already expect.
- During Phase 1 rollout, two code paths (DSL and legacy `sanitize_fts_query`) coexist
  behind a flag until the DSL is proven.

### Migration

- No data migration: the AST compiles to the existing `MATCH` + structured-param interface,
  so the index schema is unchanged. Adding `status` as a search-filter param follows the
  ADR-0026 thread (backend protocol → SQLite/Postgres/Overlay → service → MCP → CLI).
- Backward compatibility: bare-term and existing operator queries continue to work; the DSL
  is a superset. The plain `--status` CLI flag can ship independently and earlier
  (`search-status-filter-cli`) and is later subsumed by the `status:` DSL term.
- Rollout is flag-gated; the legacy keyword path remains the fallback until Phase 1 is
  validated against the test matrix and real agent traffic.

## References

- ADR-0017 (entry protocol mixins) and ADR-0026 (FIPS/state promoted columns) — the
  indexed-column + threaded-filter-param pattern this DSL compiles fielded terms onto.
- Backlog: `backend-agnostic-query-dsl` (implementation roadmap),
  `search-status-filter-cli`, `search-keyword-and-no-fallback`, `search-stale-index-silent`.
