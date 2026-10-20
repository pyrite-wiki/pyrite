# Pyrite Architecture: Key Source Files

Reference map of the core modules. Consult it when planning a change, to know where to look first. The decisions behind them are the ADRs: `pyrite sw adrs`.

## Plugins

| File | Purpose |
|------|---------|
| `pyrite/plugins/protocol.py` | `PyritePlugin` Protocol (15 methods + `name` attribute) |
| `pyrite/plugins/registry.py` | Plugin discovery and aggregation |

## Models & Schema

| File | Purpose |
|------|---------|
| `pyrite/models/core_types.py` | 9 built-in entry types + `ENTRY_TYPE_REGISTRY` |
| `pyrite/models/factory.py` | Entry factory — `build_entry()` single dispatch point |
| `pyrite/schema/` | `KBSchema`, `FieldSchema` — schema-as-config validation |
| `pyrite/schema/field_aliases.py` | Which key a type reads under another name (`field_aliases` in a schema), and which spelling a file uses for a field (`metadata.k`: ADR-0051) |
| `pyrite/config.py` | `PyriteConfig`, `Settings`, `KBConfig` |

## CLI

| File | Purpose |
|------|---------|
| `pyrite/cli/__init__.py` | Main Typer app; registers the command groups |
| `pyrite/cli/*_commands.py` | One module per command group; KB names via `cli/context.py` → `services/kb_resolver.py` |

## Storage

| File | Purpose |
|------|---------|
| `pyrite/storage/database.py` | `PyriteDB` (SQLite + FTS5) |
| `pyrite/storage/queries.py` | SQL queries (FTS search, tag search, graph) |
| `pyrite/storage/file_operations.py` | `apply(text, ops, span)`: field operations on a file's own text, a value-level patch (ADR-0042) |
| `pyrite/storage/entry_writer.py` | The only writer of an entry file's bytes (one exception left, `ids pin`): `create_entry_text`, `publish_entry_text` (compare-and-replace under the lock) |

## Server (REST + MCP)

| File | Purpose |
|------|---------|
| `pyrite/server/api.py` | REST API factory, deps, rate limiter, central `PyriteError` handler |
| `pyrite/server/endpoints/` | Per-feature endpoint modules (kbs, search, entries, etc.) |
| `pyrite/server/mcp_server.py` | MCP server (3-tier tools) |

## Services

| File | Purpose |
|------|---------|
| `pyrite/services/kb_service.py` | `KBService` CRUD with hooks |
| `pyrite/services/kb_resolver.py` | The one KB resolver: a KB name becomes one KB, a KB set, or `KB_NOT_FOUND` (ADR-0049) |
| `pyrite/services/search_service.py` | `SearchService` — keyword, semantic, hybrid search + RRF |
| `pyrite/services/embedding_service.py` | `EmbeddingService` — vector storage, similarity search |
| `pyrite/services/embedding_worker.py` | `EmbeddingWorker` — background embedding pipeline |
| `pyrite/services/llm_service.py` | `LLMService` — provider-agnostic LLM abstraction |
| `pyrite/services/template_service.py` | `TemplateService` — entry templates and presets |
| `pyrite/services/git_service.py` | `GitService` — git operations, commit, diff |
| `pyrite/services/repo_service.py` | `RepoService` — multi-repo management |
| `pyrite/services/user_service.py` | `UserService` — user identity and auth |
| `pyrite/services/clipper.py` | `ClipperService` — web clipper |
| `pyrite/services/graph_service.py` | `GraphService` — graph/link queries (extracted from `KBService`) |
| `pyrite/services/export_service.py` | `ExportService` — KB export and git operations (extracted from `KBService`) |
| `pyrite/services/ephemeral_service.py` | `EphemeralKBService` — ephemeral KB lifecycle with TTL (extracted from `KBService`) |
| `pyrite/services/quota_service.py` | `QuotaService` — usage tier limit checks (extracted from `KBService`) |
| `pyrite/services/collection_query.py` | Collection query functions |
