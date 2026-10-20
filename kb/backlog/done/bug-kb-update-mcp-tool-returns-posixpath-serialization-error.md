---
id: bug-kb-update-mcp-tool-returns-posixpath-serialization-error
title: 'Bug: kb_update MCP tool returns PosixPath serialization error'
type: backlog_item
tags:
- bug
- mcp
- agents
importance: 5
status: done
priority: medium
rank: 0
---

Cannot reproduce after v0.23.0 tech debt sprint. Likely fixed by one of: SafeEncoder extraction, _esc None guard, str() on file_path in MCP handlers, or BaseBackend extraction. All MCP update paths now convert Path objects to strings.
