---
id: adr-0004
type: adr
title: "Folder-per-Author Permission Convention"
adr_number: 4
status: accepted
deciders: ["markr"]
date: "2025-10-15"
tags: [architecture, permissions, collaboration]
---

> **Inert until identity reaches hooks (2026-10-03).** The app-layer check is
> inert today: hooks run with `user=""`, so the author check returns early. Per
> [[adr-0043]] decision 7, the dispatcher injects the acting identity from the
> session principal; until then this convention is enforced only at the git layer
> (CODEOWNERS). Evidence: `kb/designs/adr-audit-2026-10.md`.

## Context

Multi-author KBs need permission enforcement. We needed a model that works at both the application layer and the git layer without complex ACL infrastructure.

## Decision

Use a folder-per-author convention: `writeups/alice/my-essay.md`. This gives two enforcement layers:
1. **App layer**: hooks check `before_save` that the author matches the folder
2. **Git layer**: branch protection rules can restrict who pushes to which paths

Single-author KBs (Zettelkasten) don't need this. Shared-namespace content (Encyclopedia published articles) uses flat layout with drafts per-author.

## Consequences

- Simple, filesystem-visible permission model
- Works with standard git hosting (GitHub CODEOWNERS, branch rules)
- No custom ACL database needed
- Extensions choose their permission model: single-author, author-owned, or collaborative
