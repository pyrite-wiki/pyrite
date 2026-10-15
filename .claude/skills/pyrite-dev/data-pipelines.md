# Data Pipelines

## Entry Lifecycle

Every entry flows through this pipeline. Understanding it prevents the most common bugs.

```
Create request (CLI/MCP/API)
    │
    ▼
Schema Validation (schema.py)
    │  KBSchema.validate_entry() checks field types, options, required fields
    │  allow_other=true fields produce warnings instead of errors
    │
    ▼
Type Resolution (_resolve_entry_type)
    │  Maps core types to plugin subtypes:
    │    note     → article              (software-kb)
    │    event    → investigation_event  (journalism-investigation)
    │    person   → user_profile         (social)
    │  SILENT — no error if no plugin subtype exists, falls through to core
    │
    ▼
build_entry (factory.py)
    │  Two paths:
    │  1. Plugin path: resolved class != GenericEntry
    │     - Introspects dataclass fields to separate known kwargs from unknown
    │     - Unknown kwargs go into metadata dict (not top-level frontmatter)
    │     - Calls resolved_cls.from_frontmatter(fm, body)
    │  2. Core path: uses GenericEntry
    │     - Direct construction, metadata passed as-is
    │
    ▼
Save (repository.py)
    │  _infer_subdir() determines file location:
    │    - Checks CORE_TYPES dict first
    │    - For plugin subtypes, walks MRO to find parent core type's subdirectory
    │  Writes frontmatter + body to markdown file
    │
    ▼
Index (database.py → PyriteDB.upsert_entry)
    │  Inserts/updates SQLite row, updates FTS5 index, syncs tags
    │
    ▼
Embed (embedding_service.py → EmbeddingWorker)
    Background process: generates vector embedding, stores in sqlite-vec
    Triggered by post-save hook or batch reindex
```

## Type Resolution Details

`_resolve_entry_type()` in `kb_service.py` consults the plugin registry to find if any plugin provides a subtype for a core type. This mapping is implicit — the plugin's `get_entry_types()` returns classes whose parent is a core type.

**Why this matters:** When you call `kb_create(type="event")`, the entry you get back might be an `InvestigationEventEntry`, not an `Event`. The frontmatter will say `type: event`, but the Python class and its validation rules come from the plugin. If your code does `isinstance(entry, Event)`, it works (inheritance). If it does `type(entry).__name__ == "Event"`, it fails.

## build_entry Factory

The factory (`pyrite/models/factory.py`) is the single point where entries are constructed. It handles two tricky problems:

1. **Plugin subtype dispatch**: Uses `get_entry_class()` from the registry to find the right class, then calls `from_frontmatter()`.

2. **Known vs unknown kwargs**: Plugin entry classes have specific dataclass fields. Extra kwargs (like `source_url`, `linkedin`) that aren't dataclass fields must go into the `metadata` dict, not as top-level frontmatter keys. The factory introspects `dataclasses.fields()` to make this separation.

**Common failure mode**: If a new field is added to a plugin entry class but `from_frontmatter()` doesn't handle it, the field silently ends up in metadata instead of where it belongs. Always check both the dataclass definition and `from_frontmatter()` when adding fields to entry types.

## Search Pipeline

```
Query (MCP/CLI/API)
    │
    ▼
SearchService.search()
    │  Routes by mode: keyword, semantic, hybrid
    │
    ├─ Keyword: FTS5 query → db.search() → results (no body field)
    │
    ├─ Semantic: EmbeddingService.search_similar() → vector KNN
    │   sqlite-vec doesn't support SQL OFFSET, so fetches limit+offset and slices
    │
    └─ Hybrid: RRF fusion of keyword + semantic
       Both legs fetch max(limit*2, offset+limit) candidates
       Reciprocal Rank Fusion (k=60) combines scores
       Final slice: sorted_keys[offset : offset + limit]
```

**Important**: FTS search (`queries.py:search()`) returns all entry columns *except* `body` to save tokens. Use `kb_get` to fetch full entry content.

## Embedding Pipeline

Background process managed by `EmbeddingWorker`:

1. `EmbeddingWorker` runs in a separate thread, polling for unembedded entries
2. `EmbeddingService.embed_entry()` generates vectors via configured provider
3. Vectors stored in sqlite-vec virtual table
4. `search_similar()` does KNN search against stored vectors

The pipeline is optional — if no embedding provider is configured, semantic and hybrid search gracefully degrade to keyword-only.
