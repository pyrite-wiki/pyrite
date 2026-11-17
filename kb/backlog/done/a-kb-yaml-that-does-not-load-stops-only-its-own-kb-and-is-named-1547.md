---
id: a-kb-yaml-that-does-not-load-stops-only-its-own-kb-and-is-named-1547
title: A kb.yaml that does not load stops only its own KB, and is named (#1547)
type: backlog_item
tags:
- bug
importance: 5
kind: bug
status: done
priority: high
assignee: agent:cloud-worker
effort: M
rank: 0
---

https://github.com/pyrite-wiki/pyrite/issues/1547: one registered KB whose kb.yaml is not UTF-8 or not valid YAML stops every command on every KB; record the error on that KB, answer KB_CONFIG_INVALID for operations that need its schema, keep its rows, never rewrite the file.
