---
id: collaboration-services
type: component
title: "Collaboration Services"
kind: service
path: "pyrite/services/"
owner: "markr"
dependencies: ["pyrite.config", "pyrite.storage.database", "pyrite.storage.index", "pyrite.github_auth", "pyrite.services.user_service", "httpx"]
tags: [core, collaboration, git]
---

Business logic layer providing stateless service classes that eliminate duplicated logic between the API, CLI, and MCP surfaces. Each service takes `(config, db)` as constructor arguments with no shared singleton state. KBService is the central workhorse composing DocumentManager, IndexManager, and plugin hooks for entry CRUD. Specialized services (LLM, Embedding, Search, QA, Graph, Auth) are purpose-built and consumed by all three surfaces.

## Architecture

- 30+ service classes, each independent and stateless
- DI via `server/api.py` app.state for HTTP, `cli/context.py` context managers for CLI
- KBService coordinates write path: validate → hooks → save → index → hooks
- SearchService dispatches across FTS5, semantic, and hybrid modes

## Key Services

- `KBService` — entry CRUD, index sync, plugin lifecycle hooks
- `SearchService` — FTS5 + semantic search dispatch
- `LLMService` — OpenAI-compatible multi-provider LLM calls
- `EmbeddingService` / `EmbeddingWorker` — vector embedding generation
- `QAService` — rubric-based quality evaluation (tiered)
- `AuthService` — session management, API key hashing
- `GraphService` — knowledge graph queries and analytics
- `TaskService` — task DAG management with dependency tracking

## Related

- [[kb-service]] — central orchestrator
- [[search-service]] — search dispatch
- [[rest-api]] — HTTP surface consuming services
