---
id: test-a-kb-name-resolves-once-a-new-get-kb-outside-the-registry-fails-t3-1-1530
title: "test: a KB name resolves once; a new get_kb outside the registry fails (T3-1, #1530)"
type: backlog_item
tags:
- quality
importance: 5
kind: improvement
status: done
priority: medium
assignee: agent:cloud-worker
effort: S
rank: 0
---

Add tests/test_kb_names_resolve_once.py: an AST ratchet on .get_kb( outside the registry module with a per-file baseline, plus a behavioural walk whose failures today are strict expected failures per site group. Add the ResolvedKB name for the resolved handle in services/kb_resolver.py (alias and docstring only, no behaviour change).
