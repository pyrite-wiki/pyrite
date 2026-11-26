---
id: mcp-server-imports-business-logic-from-cli-layer-layer-inversion
title: MCP server imports business logic from CLI layer (layer inversion)
type: backlog_item
tags:
- tech-debt
- architecture
- layer-separation
importance: 5
kind: refactor
status: done
priority: high
effort: S
rank: 0
---

_batch_suggest and _discover_neighbors are imported from cli/link_commands.py into server/mcp_server.py. Extract into a LinkDiscoveryService in services/ and have both CLI and MCP call that.
