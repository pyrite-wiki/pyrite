---
id: a-gate-names-board-yaml-only-for-a-cause-in-the-file-and-a-criterion-carries
title: "A gate names board.yaml only for a cause in the file, and a criterion carries only the keys its type reads (#1585, #1586)"
type: backlog_item
tags:
- bug
importance: 5
kind: bug
status: done
priority: medium
assignee: agent:cloud-worker
effort: S
rank: 0
---

A gate's blockers lookup now runs outside the board refusal, so an index or storage failure keeps its own error code and retryable on every tool that evaluates a gate, with or without a board.yaml. A judgment or agent_responsibility criterion that carries checker or params is refused when the board is loaded, because those keys are never read for its type.
