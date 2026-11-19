---
id: tests-pin-kb-name-refusal-and-acceptance-on-mcp-write-tools-1781
title: Tests pin kb_name refusal and acceptance on MCP write tools (#1781)
type: backlog_item
tags:
- mcp
importance: 5
kind: feature
status: in_progress
priority: medium
assignee: agent:cloud-worker
effort: S
rank: 0
---

Pin both kb_name branches of the MCP unknown-argument rule (#1758/#1774): refusal on write tools declaring another KB argument (kb_link, kb_commit, kb_push), and acceptance on a locates_entry core write tool and a plugin write tool. Tests only, in tests/test_mcp_unknown_arguments.py.
