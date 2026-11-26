---
id: deploy-config-for-journalists-pyrite-wiki-docker-caddy-tls
title: Deploy config for journalists.pyrite.wiki (Docker + Caddy + TLS)
type: backlog_item
tags:
- deployment
- journalists
- infrastructure
importance: 5
kind: feature
status: done
priority: critical
effort: S
rank: 0
---

Clone deploy/selfhost/ config for journalists.pyrite.wiki. Configure: domain, GitHub OAuth (optional), anonymous_tier=none, allow_registration with invite codes, pre-loaded 6 KBs. Add to shared Caddy on the VPS or provision a separate server.
