---
id: inconsistent-error-response-shapes-across-rest-mcp-and-cli
title: Inconsistent error response shapes across REST, MCP, and CLI
type: backlog_item
tags:
- tech-debt
- server
- consistency
importance: 5
kind: refactor
status: done
priority: low
effort: M
rank: 0
---

REST uses code/message/hint, MCP uses error/error_code/suggestion, some MCP handlers return bare error dicts without using _error(). Audit MCP handlers and standardize.
