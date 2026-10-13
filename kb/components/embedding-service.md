---
id: embedding-service
type: component
title: "Embedding Service"
kind: service
path: "pyrite/services/embedding_service.py"
owner: "markr"
dependencies: ["sentence-transformers", "sqlite-vec", "pyrite.storage.database"]
tags: [core, ai, search]
---

# Embedding Service

The embedding service provides local vector embeddings for semantic similarity search across knowledge base entries. It uses the `sentence-transformers` library with the `all-MiniLM-L6-v2` model to generate 384-dimensional embeddings, stored in `sqlite-vec` for efficient KNN queries. The entire pipeline runs locally with no external API calls.

## Key Files

| File | Purpose |
|------|---------|
| `pyrite/services/embedding_service.py` | Full implementation: model loading, embedding generation, batch indexing, KNN search |

## API / Key Classes

### `EmbeddingService`

Initialized with a `PyriteDB` instance and an optional model name (defaults to `all-MiniLM-L6-v2`).

| Method | Description |
|--------|-------------|
| `embed_text(text)` | Generate a float32 embedding vector for arbitrary text |
| `embed_entry(entry_id, kb_name)` | Embed a single entry (title + summary + first 500 chars of body) and upsert into `vec_entry` |
| `embed_all(kb_name, force, progress_callback)` | Batch embed all entries for a KB (or all KBs). Returns `{embedded, skipped, errors}` counts. Skips already-embedded entries unless `force=True` |
| `search_similar(query, kb_name, limit, min_similarity, kb_names, ...filters)` | KNN search: embeds the query and asks the backend's `search_semantic`, which filters by KB, readable set and the keyword leg's filters and cuts at a cosine similarity floor (default 0.155). Returns up to `limit` rows with `similarity` (cosine, 3 places, the same on every backend), `snippet`, and `distance` (backend-specific; leaves after one release) |
| `has_embeddings(kb_name=None, kb_names=None)` | Do any embeddings exist: in the index, or in the KBs named |
| `embedding_stats()` | Returns `{available, count, total_entries, coverage}` statistics |

### Module-Level Functions

| Function | Description |
|----------|-------------|
| `is_available()` | Check if `sentence-transformers` is installed (optional dependency) |
| `_entry_text(entry)` | Combine title + summary + body[:500] into embedding input text |
| `_generate_snippet(entry, max_len)` | Generate a display snippet from summary or first paragraph of body |
| `_embedding_to_blob(embedding)` / `_blob_to_embedding(blob)` | Serialize/deserialize float32 vectors to bytes for sqlite-vec storage |

## Design Notes

- **Lazy model loading.** The `SentenceTransformer` model is loaded on first use (`_get_model`) to avoid startup cost when semantic search is not needed.
- **Optional dependency.** The service requires `pip install pyrite[semantic]`. The `is_available()` function lets callers gracefully degrade when sentence-transformers is not installed.
- **sqlite-vec integration.** Embeddings are stored in a virtual table (`vec_entry`) keyed by the `entry` table's `rowid`. The KNN query uses `WHERE embedding MATCH ? AND k = ?`, which picks the `k` candidates by `vec0`'s own L2 `distance`; the rows are then ordered by `vec_distance_cosine` against the query, and that cosine is the row's `similarity`. For unit vectors (the default model's) the two orders agree.
- **Candidate filtering and the `k` ceiling (SQLite).** `search_semantic` passes the caller's filters (KB, type, tags, dates, `fips`, `state`, status) into the KNN as `rowid IN (SELECT ...)`, so unrelated nearer rows cannot use up the candidate budget; the archived exclusion rides with them. With no caller filter it stays out of the vector query, and joins it only when archived rows filled the nearest `k` (`k` at the 4096 cap, after escalation) and left the search short. The retry can also fire without helping, at the price of one extra query and a correct result: every live row already in the k-set, `limit` above the cap with no live rows beyond, every live row below `min_similarity`; an empty k-set at the cap adds a size probe. It asks for `limit * 2` neighbours (room for the `min_similarity` cutoff) and escalates `k` up to sqlite-vec's hard ceiling of 4096, which it clamps to and never exceeds. More than 4096 matching rows therefore still caps at 4096. A scoped caller's readable set (`kb_names`) is one of those filters and rides inside the KNN too (#668); `SearchService._restrict` after the legs is a backstop.
- **Embedding text composition.** Each entry is embedded as `title + summary + body[:500]`, giving weight to metadata while capping body length to keep embeddings focused.

## Consumers

- **Search service** — uses `EmbeddingService.search_similar()` for `--mode semantic` and `--mode hybrid` search
- **CLI `pyrite search`** — exposes semantic search to the command line
- **REST API search endpoint** — provides semantic search via the `/search` route
- **MCP server** — exposes semantic search as an MCP tool

## Related

- [[search-service]] — orchestrates keyword + semantic search modes
- [[storage-layer]] — `PyriteDB` manages the `vec_entry` virtual table and `vec_available` flag
- [[background-embedding-worker]] — async embed_queue processor
- [[background-embedding-pipeline]] — backlog item that delivered the pipeline
