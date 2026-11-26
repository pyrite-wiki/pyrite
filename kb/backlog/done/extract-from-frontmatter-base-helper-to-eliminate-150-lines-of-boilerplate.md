---
id: extract-from-frontmatter-base-helper-to-eliminate-150-lines-of-boilerplate
title: Extract from_frontmatter base helper to eliminate 150 lines of boilerplate
type: backlog_item
tags:
- tech-debt
- models
- refactor
importance: 5
kind: refactor
status: done
priority: medium
effort: M
rank: 0
---

Every typed entry's from_frontmatter repeats 10-15 lines for base fields (id, title, body, summary, importance, tags, aliases, sources, links, provenance, metadata, created_at, updated_at, _schema_version). Extract _base_from_frontmatter() to Entry base class.
