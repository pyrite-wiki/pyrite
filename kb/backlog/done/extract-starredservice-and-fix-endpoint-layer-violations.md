---
id: extract-starredservice-and-fix-endpoint-layer-violations
title: Extract StarredService and fix endpoint layer violations
type: backlog_item
tags:
- tech-debt
- architecture
- layer-separation
importance: 5
kind: refactor
status: done
priority: medium
effort: S
rank: 0
---

starred.py, blocks.py, settings_ep.py, and reviews.py bypass the service layer to access storage directly. Create thin services (StarredService at minimum) to encapsulate the DB queries. Also add get_auth_service DI provider for admin endpoints.
