---
id: authenticated-mcp-endpoint-sse-http-transport-with-bearer-auth
title: Authenticated MCP endpoint (SSE/HTTP transport with Bearer auth)
type: backlog_item
tags:
- mcp
- auth
- multi-user
importance: 5
kind: feature
status: done
priority: high
effort: L
rank: 0
---

Add network-accessible MCP transport (SSE or Streamable HTTP) mounted at /mcp on the FastAPI app. Bearer token auth mapped to user accounts with per-KB permission enforcement. Enables Claude Desktop and Claude Code to connect to the hosted instance.

MCP SDK has mcp.server.sse and mcp.server.streamable_http available. Need: transport wiring, auth middleware, per-request user context propagation to tool handlers.

Cross-ref: authenticated-mcp-endpoint-for-remote-access
