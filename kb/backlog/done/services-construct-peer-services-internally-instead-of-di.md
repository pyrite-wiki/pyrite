---
id: services-construct-peer-services-internally-instead-of-di
title: Services construct peer services internally instead of DI
type: backlog_item
tags:
- tech-debt
- architecture
- di
importance: 5
kind: refactor
status: done
priority: medium
effort: M
rank: 0
---

QAService, TaskService, and KBService create fresh instances of each other via lazy imports instead of receiving dependencies. QAService creates KBService without embedding worker. Consider a lightweight ServiceContainer or pass services at construction.
