---
id: cron-job-to-auto-sync-upstream-kb-changes-on-capturecascade-org
title: Cron job to auto-sync upstream KB changes on capturecascade.org
type: backlog_item
tags:
- deployment
- cascade
- automation
importance: 5
kind: feature
status: retired
priority: medium
effort: S
rank: 0
lifecycle: archived
---

capturecascade.org serves from a Docker container seeded with static KB data. When the upstream cascade-timeline KB is updated (new events, corrections), the deployed site doesn't pick up changes until manually reseeded.

Need a periodic job (cron or systemd timer) that:
1. Pulls latest cascade-kb repo
2. Copies updated KB files into the container
3. Rebuilds the index
4. Re-exports timeline.json (with sources)
5. Re-renders the site cache

Could run hourly or daily. Should be idempotent and only re-render if files actually changed.
