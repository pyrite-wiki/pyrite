---
id: unify-kb-registry-config-yaml-and-db-kb-table-should-be-a-single-source-of-truth
title: 'Unify KB registry: config.yaml and DB kb table should be a single source of truth'
type: backlog_item
tags:
- bug
- architecture
- config
- kb-registry
importance: 5
kind: bug
status: done
priority: high
effort: M
rank: 0
---

## Problem

Two KB registries exist with partial sync:
1. config.yaml knowledge_bases list — read by PyriteConfig, KBService, KBRepository, link commands
2. DB kb table — written by KBRegistryService for dynamically added KBs

config.get_kb() only searches config.yaml. KBs added via 'pyrite kb add' (DB-only) are invisible to the service layer, causing 'KB not found' errors for link, create, update operations.

seed_from_config() pushes config → DB at startup, but DB → config never happens.

## Fix

Make PyriteConfig.get_kb() fall back to the DB when config.yaml doesn't have the KB. Or: at startup, merge DB-registered KBs into PyriteConfig.knowledge_bases so all code paths see all KBs regardless of registration source.

## Impact

Affects any user who adds KBs via the web UI or 'pyrite kb add' and then tries to use CLI/MCP operations on them.
