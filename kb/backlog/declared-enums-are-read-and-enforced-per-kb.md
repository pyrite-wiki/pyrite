---
id: declared-enums-are-read-and-enforced-per-kb
title: Declared enums are read and enforced per KB
type: backlog_item
tags:
- schema
- enhancement
importance: 5
kind: feature
status: in_progress
priority: high
assignee: agent:pyrite-worker-opus
effort: L
rank: 0
---

Theme: **declared enums are read, checked the same way everywhere, and enforced per KB** — closes #555 and #47.

Maintainer decision (2026-09-28, in #555): fix, warn, clean up, then enforce; enforcement is a per-KB option, on by default.

The spec is the `## Groom 2026-09-29` comment on #555 (acceptance 1-15, touches, out of scope, Q1-Q4). Q1-Q4 take the groom's recommended answers unless the maintainer says otherwise: `validation.enforce_enums` (independent of `validation.enforce`), the on-disk exception covers plugin enum errors but the switch does not, health key `off_list_values` (unhealthy when on / warning when off), `values:` a permanent alias with no runtime warning.

Model: opus. Plan first (one page, as a PR comment) before building. Cold read: yes.
