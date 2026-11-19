---
id: every-search-response-says-what-was-searched-1757
title: Every search response says what was searched (#1757)
type: backlog_item
tags:
- search
importance: 5
kind: feature
status: in_progress
priority: medium
assignee: agent:cloud-worker
effort: S
rank: 0
---

Keyword search indexes title, summary and body only; field values and tags are not searched, so a miss on a field-only mention reads as absence. Add a searched list to the one search_response mapping, derived from the constant that builds the index, so CLI, MCP kb_search and REST carry it. Option A of #1757; indexing field values and tags (B) is a separate issue.
