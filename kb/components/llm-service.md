---
id: llm-service
type: component
title: "LLM Service"
kind: service
path: "pyrite/services/llm_service.py"
owner: "markr"
dependencies: ["anthropic", "openai", "pyrite.config"]
tags: [core, ai, llm]
---

# LLM Service

Provider-agnostic LLM abstraction supporting Anthropic (Claude), OpenAI (GPT), OpenRouter (200+ models), Ollama (local), and a stub/no-op provider. SDKs are optional dependencies imported lazily.

## API

```python
class LLMService:
    def __init__(self, settings: Settings) -> None
    def status(self) -> dict[str, Any]          # {"configured": bool, "provider": str, "model": str}
    async def complete(self, prompt: str, system: str | None = None, max_tokens: int = 1024) -> str
    async def stream(self, prompt: str, system: str | None = None) -> AsyncIterator[str]
    async def embed(self, texts: list[str]) -> list[list[float]]
```

## Provider Mapping

| Provider | SDK | How | Embeddings |
|----------|-----|-----|------------|
| `anthropic` | `anthropic` | Direct SDK | Not supported (returns empty) |
| `openai` | `openai` | Direct SDK | text-embedding-3-small |
| OpenRouter | `openai` | `base_url="https://openrouter.ai/api/v1"` | Depends on model |
| Ollama | `openai` | `base_url="http://localhost:11434/v1"` | Depends on model |
| `stub`/`none` | — | No-op, returns empty strings | Returns empty vectors |

## Configuration

Two layers, DB settings take priority over config file:

1. **DB settings** (via Settings page UI): `ai.provider`, `ai.apiKey`, `ai.model`, `ai.baseUrl`
2. **Config file** (`~/.pyrite/config.yaml`): `ai_provider`, `ai_api_key`, `ai_model`, `ai_api_base`
3. **Environment variables**: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENAI_API_BASE`

## DI Integration

The REST API exposes `get_llm_service()` in `api.py` which:
- Reads AI settings from the DB (via KBService) with config file fallback
- Returns a cached singleton `LLMService` instance
- Is invalidated when any `ai.*` setting is changed via the settings endpoint

```python
from ..api import get_llm_service
llm: LLMService = Depends(get_llm_service)
```

## Consumers

- **AI endpoints** (`ai_ep.py`): Summarize, auto-tag, suggest-links, chat — all use `get_llm_service` DI
- **Admin endpoint** (`admin.py`): `GET /api/ai/status` — checks if provider is configured
- **Query expansion** (`query_expansion_service.py`): Uses LLMService for search term expansion
- **Embedding service** (`embedding_service.py`): Local sentence-transformers (separate from LLMService)

## Error Handling

- If provider is `stub`/`none` or API key is empty: `status()` returns `configured: false`
- AI endpoints check `status()["configured"]` and return HTTP 503 with `AI_NOT_CONFIGURED` error
- SDK import failures raise `RuntimeError` with installation instructions

## Related

- [[adr-0007]] — Three-surface AI architecture with BYOK
- [REST API Server](rest-api.md) — Hosts AI endpoints that consume this service
- [Search Service](search-service.md) — Used for RAG context retrieval in chat
