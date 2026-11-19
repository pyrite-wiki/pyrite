---
id: mcp-drops-unknown-argument-keys-without-a-word-1758
title: MCP drops unknown argument keys without a word (#1758)
type: backlog_item
tags:
- mcp
importance: 5
kind: bug
status: in_progress
priority: medium
assignee: agent:cloud-worker
effort: M
rank: 0
---

An MCP call whose arguments carry a key the tool's inputSchema does not declare (task_create due, task_claim agent_id) succeeds and silently loses what was asked. Maintainer decision on #1758: refuse unknown keys on write tools, report them on read tools, one check at the dispatcher chokepoint driven by each tool's declared schema and registered tier.
