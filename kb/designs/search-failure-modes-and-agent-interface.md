---
type: design
id: search-failure-modes-and-agent-interface
title: "Search failure modes and what an agent-optimal search interface would look like"
status: draft
date: '2026-09-18'
author: 'claude-opus-5 (agent user), from a live corpus-exploration session'
tags: [search, semantic, hybrid, mcp, agent-ux, design]
---

Written from the outside: notes from an agent that spent a session using `pyrite search` for
open-ended exploration of a 3,576-entry corpus, plus a traced research task (the Powell Memorandum
lineage). Empirical claims are from that session and are dated; design proposals are opinion.

Related issues: #53 (type filter ignored in semantic/hybrid), #54 (search UX).

## 1. Failure modes

### 1.1 Semantic

**Vocabulary-register mismatch — the one that actually bit.** The corpus indexes *analytic* prose
("corporate capture blueprint", "accountability-bypass architecture"). A researcher querying the
Powell Memo naturally uses the *source's period* vocabulary ("free enterprise system", "business must
organize politically"). These are the same idea in two registers, and embeddings did not bridge them:

| query (`-m semantic -n 10`) | returned | canonical Powell entry? |
|---|---|---|
| business must organize politically against critics of free enterprise | 3 | **no** |
| corporations should fund think tanks to shift public opinion | 3 | **no** |
| long-term coordinated business counterattack on universities and media | 7 | **no** |

`-m keyword "Powell memorandum"` returns it at rank 1. Semantic search failed on a near-paraphrase
of the target document's own thesis. **This is the failure mode to design against**, because it is
invisible: the tester gets plausible on-topic results and no signal that the best entry was missed.

**Silent under-return.** Limit 10, three results back, no explanation. If a similarity threshold
exists it is undocumented and undiscoverable from the CLI. The user cannot distinguish "the corpus
has only 3 relevant entries" from "7 were cut by a threshold you can't see."

**Long-document dilution.** The Powell entry is 34.6 KB. If embedded as one vector, its specific
thesis is averaged with ~30 KB of surrounding context. This is a plausible mechanical explanation for
the recall failure above and is worth checking directly: is there chunking? At what granularity?

**Off-domain drift with uninterpretable scores.** `"accountability disappears through procedural
bypass"` ranked a **Windows 10/11 privacy-hardening note** 4th, above on-topic governance themes.
Tolerable in itself — but `rank` and `rrf_score` appear in every result and neither is actionable.
There is no threshold flag and no documented rule of thumb, so a caller cannot tell where the real
results stopped.

**Proper-noun and identifier blindness.** Embeddings are weakest exactly where keyword is strongest:
docket numbers, PIIDs, statute cites, surnames. The corpus is full of these. The tool offers no
guidance on which mode to reach for.

**Negation is not representable.** "detention facilities *not* operated by GEO" embeds close to
"detention facilities operated by GEO." Keyword `NOT` handles this; semantic cannot, and nothing
says so.

### 1.2 Hybrid

**Fusion can be worse than either input.** RRF blends rankings without knowing which input deserves
trust for *this* query. On a proper-noun query, semantic noise displaces good keyword hits; on a
conceptual query, keyword's literal matches crowd out the concept matches. Measured: `"accountability
bypass"` in keyword led with a **task**; semantic led with six themes/mechanisms. Hybrid interleaved
them — arguably worse for thematic exploration than pure semantic.

**Filters silently dropped in fusion (#53).** `--type` works in keyword and is *silently ignored* in
semantic and hybrid — a bogus type name returns a full result set. This is the general hazard: in a
fused path, a constraint honored by one branch and dropped by the other yields plausible,
unconstrained results.

**Cost is invisible and large.** Keyword 49 ms, semantic 6,002 ms, hybrid 8,033 ms on identical
queries (peaks 11,144 / 13,337 ms) — **120-270×**. Hybrid is the natural default for exploration and
the most expensive mode, and nothing outside `--debug` says so.

### 1.3 Corpus-shape failures, not algorithm failures

- **One type is half the corpus.** 1,724 of 3,576 entries (48%) are `task` — workflow bookkeeping,
  not research. They dominate keyword results and there is no way to exclude a type.
- **Inconsistent type vocabulary.** `research-note` and `research_note` both appear. Any type-filtered
  search silently under-returns.
- **Ids exceed 100 characters** with no prefix or fuzzy resolution. A display-truncated id fails with
  `NOT_FOUND` — a real trap when ids move through notes and summaries.

## 2. What would make this feel natural to an agent

The core mismatch: **the tool is built for retrieving documents; an agent is trying to build a map.**
Everything below follows from that.

### 2.1 Tell me what you did — every call, not just under `--debug`

Echo `mode`, `results_returned` vs `limit`, whether any filter was applied or dropped, and elapsed
time. The single highest-value addition:

```json
{"query": "...", "mode": "semantic", "limit": 10, "returned": 3,
 "filters_applied": ["type=theme"], "filters_dropped": [],
 "truncated_by": "similarity_threshold@0.62", "latency_ms": 6002}
```

`filters_dropped` alone would have made #53 self-reporting instead of a silent wrong answer.

### 2.2 Let me express what I don't want

Exclusion matters more than inclusion in an unfamiliar corpus — you know what you don't want long
before you know the type vocabulary. `exclude_type` (repeatable) and multi-value `type` would each
have saved real time. `exclude_type=task` is the single most valuable filter for this corpus.

### 2.3 A lean default shape, with opt-in detail

Default search returns ~20 fields per result, including five timestamp/authorship fields that never
inform a search decision. At limit 20 that is ~400 lines to answer "what's in here about X."
`--fields id,title,entry_type` fixes it entirely and sits below the fold in `--help`.

**Exploration is the common case.** Default to `id`, `title`, `entry_type`, `snippet`, `score`; put
the full record behind `fields='*'`.

### 2.4 Excerpt-on-get

`get` on a 34.6 KB entry returns 34.6 KB. There is no summary or excerpt option, so the agent either
floods its context or leaves the tool for `sed`/`grep` — which it did. Wanted: `get(id,
mode='summary'|'outline'|'full')`, or `get(id, section='Research gaps')`. An `outline` mode returning
just the heading tree would be the cheapest possible orientation to a large entry.

### 2.5 Make the structural tools first-class

**The best call in the session was not a search.** `backlinks 1971-08-23-powell-memo` returned 24
entries across 7 types and traced the lineage directly — search gives *topical neighbours*, backlinks
gives *structural descendants*. Nothing in the search documentation points to it.

For map-building, the graph is often the right tool and it is currently the hidden one. A
`related(id)` that fuses backlinks + outbound links + tag-siblings + nearest-neighbours, returning a
typed adjacency list, would frequently beat any query I could write.

### 2.6 Route the mode, or at least advise

Callers should not have to know that proper nouns want keyword and paraphrases want semantic. Either
route automatically on query shape (quoted phrases, capitalised multiword tokens, and
identifier-shaped tokens → keyword-weighted), or return an advisory:

```json
{"mode_used": "semantic", "advice": "query contains a proper noun ('Powell'); keyword mode returns
 10 results for this query vs 3 here"}
```

That advisory would have turned the session's central failure into a one-line fix.

### 2.7 Search over the map, not only the documents

The questions an agent actually asks are aggregate: *what clusters exist*, *what connects A and B*,
*what is unusually well-sourced*. Three primitives would cover most of it:

- `facets(query)` — result counts by type/tag/date-bucket, for orienting before drilling.
- `path(id_a, id_b)` — shortest link path. "How does the Powell memo connect to Project 2025?" is a
  graph query being simulated with repeated searches.
- `neighbors(id)` — the fused `related()` above.

### 2.8 Make confidence legible

Entries here carry `research_status`, `importance`, tier-rated sources, and explicit "what this does
NOT claim" sections. **That metadata is the corpus's best feature and search barely surfaces it.**
`research_status` is not a returnable field by default and not filterable. An agent assembling a
report needs to weight a `synthesis` entry differently from a `confirmed` one — and the corpus
already knows the difference.

## 3. Suggested shape for the MCP tool

```
kb_search(
  query, kb,
  mode = "auto" | "keyword" | "semantic" | "hybrid",   # auto routes on query shape
  type = [...], exclude_type = [...],
  tag = [...], status = ..., research_status = ...,
  date_from, date_to,
  limit = 10, min_score = null,
  fields = ["id","title","entry_type","snippet","score"],   # lean default
  explain = false                                            # trace in the payload
) -> {
  query, mode_requested, mode_used, limit, returned,
  filters_applied: [...], filters_dropped: [...],
  truncated_by: null | "min_score" | "limit",
  latency_ms, advice: null | "...",
  results: [ {id, title, entry_type, snippet, score, score_basis} ]
}
```

Two non-obvious points. **`filters_dropped` is not an error channel** — it is the normal way a fused
search reports that one branch couldn't honor a constraint, and it should be present and empty on
every successful call so callers learn to read it. And **`mode="auto"` should be the default**: the
mode choice is the single decision most likely to be made wrong by a caller who doesn't know the
index, and the query text usually determines the right answer.

## 4. What already works and should not be traded away

- **The FTS5 auto-quote documentation in `search --help`.** It states the rule *and* names the exact
  trap (`miller -bannon` parses the hyphen as `NOT`, matching Bannon rather than excluding). Best
  documentation in the CLI.
- **`--status` help documents its own limits** — says outright that other metadata fields such as
  `readiness` are unreachable through it. That caveat prevents a silent wrong answer. More flags
  should do this.
- **`orient`** is a genuinely good first call: entry counts and type histogram let a caller plan
  before searching.
- **`--debug`** reports mode, actual mode, fallback reason, latency, relaxed flag. Everything in it
  is worth having — the argument of §2.1 is only that it should not be opt-in.
- **Mode differences are real and useful** once known. Keyword leads with tasks, semantic leads with
  themes. That is a genuine instrument, not noise.
