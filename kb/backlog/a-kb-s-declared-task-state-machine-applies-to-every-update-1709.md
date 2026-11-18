---
id: a-kb-s-declared-task-state-machine-applies-to-every-update-1709
title: A KB's declared task state machine applies to every update (#1709)
type: backlog_item
tags:
- bug
importance: 5
kind: bug
status: in_progress
priority: high
assignee: agent:cloud-worker
effort: S
rank: 0
---

A KB's kb.yaml types.task.state_machine (strict table or relaxed mode) is never applied on update because KBService._hook_ctx does not pass kb_schema, so the before_save hook always judges with the core TASK_WORKFLOW. Pass the schema so the declared machine applies on every surface (CLI, pyrite update, MCP, REST); maintainer decided yes on 2026-11-18.
