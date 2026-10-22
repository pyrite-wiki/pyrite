---
id: rest-api
type: component
title: "REST API Server"
kind: service
path: "pyrite/server/"
owner: "markr"
dependencies: ["fastapi", "slowapi", "pydantic", "pyrite.storage", "pyrite.config", "pyrite.services"]
tags: [core, api, server, fastapi]
---

FastAPI HTTP + WebSocket server structured as a factory (`create_app()`) with fully isolated app instances. All service state lives on `app.state` with DI providers wired via `dependency_overrides` for test isolation. Three-tier access model (read/write/admin) resolved from API keys, session cookies, or anonymous tier.

## Architecture

- Factory pattern: `create_app()` in `api.py` creates isolated FastAPI instances
- 21 endpoint modules under `endpoints/`, each an `APIRouter` under `/api` prefix
- Auth endpoints mounted outside `/api` for OAuth login/logout/callback
- Rate limiting via `slowapi` with SHA-256-anonymized client IPs
- WebSocket at `/ws` for real-time index progress and multi-tab updates
- One body limit for the whole app: `body_limit.RequestBodyLimitMiddleware` counts every HTTP request body (REST, `/auth`, the `/mcp` mount, plugin routers) against `settings.max_request_body_bytes` and answers `413 PAYLOAD_TOO_LARGE` once it passes, replacing whatever the app answers after the overflow (FastAPI would otherwise turn the read error into a `400`). A route needs no limit of its own; `tests/test_request_body_limit.py` walks every OpenAPI route to keep it so

## Key Modules

- `api.py` — app factory, DI providers, auth middleware, CORS, rate limiter
- `body_limit.py` — the request-body size limit (above)
- `request_guard.py` — Host allow-list and cross-origin write refusal
- `endpoints/` — 17 domain routers (entries, search, kbs, collections, graph, timeline, AI, etc.)
- `auth_endpoints.py` — OAuth routes outside /api prefix
- `schemas.py` — Pydantic request/response models
- `mcp_server.py` — Model Context Protocol server for AI agents
- `websocket.py` — WebSocket connection manager
- `static.py` — SPA static file serving

## Related

- [[server-schemas]] — Pydantic models
- [[mcp-server]] — MCP surface
- [[mcp-tool-schemas]] — tool definitions
