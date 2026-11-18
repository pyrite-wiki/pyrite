---
id: a-presented-credential-that-does-not-verify-is-never-anonymous-1754
title: A presented credential that does not verify is never anonymous (#1754)
type: backlog_item
tags:
- auth
importance: 5
kind: bug
status: in_progress
priority: medium
assignee: agent:cloud-worker
effort: M
rank: 0
---

A request that presents an API key (header, query or Bearer) or a session cookie that does not verify gets 401 on REST, MCP over HTTP and the live-update socket, whatever anonymous_tier says; only a request with no credential becomes the anonymous visitor. One credential resolver answers verified / none / presented-but-failed for every surface, and REST accepts Authorization: Bearer as an API key. See GitHub issue #1754 (groom and maintainer decision).
