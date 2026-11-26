---
id: extract-websocket-broadcast-helper-and-worktree-resolver-di
title: Extract WebSocket broadcast helper and worktree resolver DI
type: backlog_item
tags:
- tech-debt
- server
- dry
importance: 5
kind: refactor
status: done
priority: low
effort: S
rank: 0
---

Same 8-line broadcast block copy-pasted 4 times in endpoints. Same worktree resolver block copied 4 times. Extract broadcast_event() helper and get_effective_service() DI dependency.
