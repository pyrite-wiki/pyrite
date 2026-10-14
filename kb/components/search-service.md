---
id: search-service
type: component
title: "Search Service"
kind: service
path: "pyrite/services/search_service.py"
owner: "markr"
dependencies: ["pyrite.storage", "pyrite.services.embedding_service"]
tags: [core, search, fts5, semantic]
---

# Search Service

Unified search interface supporting keyword (FTS5), semantic (vector), and hybrid modes across all knowledge bases.

## API

```python
class SearchService:
    def __init__(self, db: PyriteDB, settings: Any | None = None)

    def search(
        self, query: str,
        kb_name: str | None = None,
        entry_type: str | None = None,
        tags: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
        sanitize: bool = True,
        mode: str = "keyword",
        expand: bool = False,
        kb_names: set[str] | None = None,   # the caller's readable KBs
        warnings: list[str] | None = None,  # out-parameter
        ...
    ) -> list[dict]

    def search_response(self, query: str, **same_arguments) -> dict
        # {"results": [...], "match": ..., "match_basis": ..., "legs_run": [...],
        #  "keyword_match": ... (keyword leg ran), "warnings": [...] (any)}

def match_verdict(mode, *, keyword_match, similarities, model=None,
                  keyword_reason=None, semantic_skip=None,
                  capped=False) -> tuple[str, str]
```

`search` returns rows, for callers that gather context (AI chat, link
suggestions, the UI). `search_response` is what a surface that shows a page
returns: the CLI, MCP `kb_search` and REST `GET /api/search` all call it and
add only `query`, `count` and `has_more`.

## What a search response owes its caller

Stated once, in `SearchService.search`'s docstring; the tool description, the
REST schema and `pyrite search --help` point there. In short:

- Every filter applies on every leg; a leg that cannot honour one is dropped
  and named in `warnings` (#56).
- Every response carries a verdict, `match` (`strong`, `weak`, `none`) with
  `match_basis`, and the rows are returned whatever it says (#668).
- Every row the vector leg returned carries `similarity` (cosine, 3 places,
  the same on every backend); hybrid rows carry `legs`.

### How the verdict is computed

One function, `match_verdict`, from two signals that `search` records while it
runs (in `trace["match_signal"]`):

- `keyword_match`: `all_terms`, `some_terms` or `none`. FTS5 ANDs bare terms,
  so a hit is every term unless the query that ran has an `OR`: the OR-relaxed
  retry (keyword mode only), an `OR` the caller wrote, or `expand`'s terms. For
  `expand`, one extra one-row query asks whether the caller's own terms match.
  Hybrid's keyword leg does not relax.
- `similarities`: the top hit's and the next nine's, from the vector leg,
  before `offset` is applied. The leg always fetches at least ten rows, so the
  verdict does not depend on `limit` or `offset`.

The thresholds are per embedding model: `_MODEL_MATCH_THRESHOLDS` in
`embedding_service.py`, beside `_MODEL_MAX_BODY_CHARS`. It has one row
(`all-MiniLM-L6-v2`: floor 0.35, strong 0.50, margin 0.10), measured on 90
labelled queries (#668). A model with no row is not judged: `match_basis` says
`uncalibrated model <name>`. Add a row only by re-running the labelled set for
that model; a borrowed row is wrong more often than right (13 of 38 absent
topics flagged on `bge-base-en-v1.5` with the MiniLM numbers).

Two things the rule depends on:

- **The model.** The query is embedded by `settings.embedding_model`, the model
  `pyrite index embed` and the embed queue use. Every `EmbeddingService` in
  `pyrite/` is built with `model_name=`; a test walks the package for one that
  is not. Nothing records which model produced the vectors already in an
  index, so changing the setting needs `pyrite index embed --force`.
- **A crowd.** The margin test tells an adjacent entry from an answer by how
  far the top hit stands above the next nine. In a KB of a few dozen entries
  there is no crowd, and an absent but adjacent topic reads `weak`, not `none`
  (`tests/test_search_verdict_fixture_kb.py` pins the case).

### `none` is a claim

`none` tells an agent the KB does not contain an answer, so every path to it
has to carry that evidence: the mode searched as it promises and nothing
answered. A vector leg that did not run (`semantic_skip`: extra missing, vector
index unavailable, nothing embedded in the KBs searched, backend cannot
filter), a model with no thresholds, and a result the backend marked `capped`
are each `weak` at worst, whatever the keyword leg found, and the basis says
which. `nothing_indexed` is the one skip that is an absence. The paths that
may say `none` are listed in
`tests/test_search_match_verdict.py::TestEveryPathToNone`; a new one fails
there until it is added with its evidence.

A hybrid search whose vector leg did not run is a keyword search, and relaxes
to OR as keyword mode does. `legs_run` at the top of the response says which legs
ran.

Not accounted for: entries in the embed queue are invisible to the vector leg,
and nothing compares embedded entries with indexed ones (an entry with no text
is never embedded, so a count would always differ).

### Read scoping

Every input to the verdict is asked of the KBs the caller may read:

| Input | Where it is scoped |
|---|---|
| keyword hits, and the one-row probes (`_hits`, the `expand` probe) | `kb_names` in the FTS `WHERE` |
| vector rows and their similarities | `kb_names` in the backend's `search_semantic`: the `WHERE` on Postgres, the KNN's candidate filter on SQLite |
| "is anything embedded?" | `has_embeddings(kb_name, kb_names)`; it asked the whole index |
| the embed warning | `count_entries(kb_name, kb_names)`; it counted every KB |
| model, thresholds, why a leg was unavailable | configuration and install state, not rows |

`_restrict` after the legs is a backstop, not the mechanism.
`tests/test_search_match_verdict.py::TestReadScoping` holds it.

**SQLite's ceiling.** sqlite-vec considers at most the 4096 nearest vectors.
The readable set is one of the candidate filters `search_semantic` puts
inside the KNN (`rowid IN (SELECT ...)`, #716), so those are the 4096 nearest
*readable* vectors and the verdict is computed over scoped candidates, never
over a global k-set filtered afterwards.
`TestReadScoping::test_more_unreadable_neighbours_than_the_knn_can_hold` and
the conformance test with 4,100 nearer unreadable rows hold it.
`SemanticRows(capped=True)` remains as a backstop for a predicate applied
after the k-set: the verdict reads a capped result as `weak`, not `none`.

## Search Modes

| Mode | How it works | Performance | Quality |
|------|-------------|-------------|---------|
| `keyword` | SQLite FTS5 full-text search | Fast | Good for exact matches |
| `semantic` | Sentence-transformer embeddings + cosine similarity | Slower (first query loads model) | Good for conceptual queries |
| `hybrid` | Combines keyword + semantic by reciprocal rank fusion (`rrf_score` orders; it is not a score) | Slowest | Best overall |

## Components

- **FTS5 index**: Built by `IndexManager.sync_incremental()`, stored in SQLite
- **EmbeddingService**: Local sentence-transformers (`all-MiniLM-L6-v2`), 384-dimension vectors
- **QueryExpansionService**: Optional AI-powered query term expansion via LLMService (`expand=True`)

## REST API

`GET /api/search?q=...&mode=keyword&kb=...&type=...&tags=...&limit=20&expand=false`

Parameters:
- `q` (required): Search query
- `mode`: `keyword` (default), `semantic`, `hybrid`
- `kb`: Limit to specific knowledge base
- `type`: Filter by entry type
- `tags`: Comma-separated tag filter
- `expand`: Use AI query expansion (requires configured LLM)

## Used By

- **Search endpoint** (`search.py`): Direct user search
- **AI chat** (`ai_ep.py`): RAG context retrieval — searches KB for relevant entries to include as LLM context
- **AI suggest-links** (`ai_ep.py`): Finds related entries for link suggestions
- **MCP tools**: `search_kb` tool uses SearchService
- **CLI**: `pyrite search` command

## Related

- [[storage-layer]] — PyriteDB with FTS5 tables, SearchBackend protocol
- [[embedding-service]] — Provides vector embeddings for semantic/hybrid search
- [[background-embedding-worker]] — Async embedding queue
- [LLM Service](llm-service.md) — Powers query expansion and AI-driven search features
- [REST API Server](rest-api.md) — Hosts the search endpoint
