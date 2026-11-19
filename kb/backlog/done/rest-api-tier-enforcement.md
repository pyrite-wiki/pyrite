---
id: rest-api-tier-enforcement
type: backlog_item
title: "REST API Tier Enforcement"
kind: feature
status: done
priority: medium
effort: L
tags: [api, security, access-control]
---

# REST API Tier Enforcement

Add role-based access control to the REST API so it enforces the same three-tier model (read/write/admin) that the MCP server already enforces.

## Context

The MCP server enforces tiers at construction time — a read-tier server physically cannot expose write tools. The CLI has separate binaries (pyrite, pyrite-admin, planned pyrite-read). But the REST API has no tier enforcement: any authenticated client (or any client when auth is disabled) can call any endpoint, including admin operations like index sync, KB management, and plugin toggles.

## Scope

### Authentication enhancement
- Support multiple API keys with associated roles: `read`, `write`, `admin`
- Store keys in config (hashed) with role assignment
- Backwards compatible: single `api_key` setting continues to work as admin-level access
- New: `api_keys` list in settings with `{key_hash, role, label, created_at}`

### Endpoint tier annotation
- Decorate each endpoint with its required tier: `@requires_tier("read")`, `@requires_tier("write")`, `@requires_tier("admin")`
- GET endpoints default to read tier
- POST/PUT/DELETE endpoints default to write tier
- Admin endpoints (index sync, KB management, plugin toggle) require admin tier
- AI endpoints (summarize, chat) require write tier (they read but cost money)

### Middleware
- New `TierMiddleware` or FastAPI dependency that:
  1. Extracts the API key from header/query
  2. Looks up the key's role
  3. Compares against the endpoint's required tier
  4. Returns 403 with clear error message if insufficient

### Fallback behavior
- When `api_key` is empty (auth disabled): all endpoints accessible (current behavior, backwards compatible)
- When single `api_key` is set: that key gets admin access (current behavior)
- When `api_keys` list is configured: each key has its assigned role

## Rationale

Multi-user or shared deployments need per-client access control. Agent workflows benefit from least-privilege: a research agent should use a read-only API key, preventing accidental mutations. This aligns the REST API with the MCP server's tier model.

## References

- [REST API Server](../../components/rest-api.md)
- [MCP Server](../../components/mcp-server.md) — reference implementation of tier enforcement
