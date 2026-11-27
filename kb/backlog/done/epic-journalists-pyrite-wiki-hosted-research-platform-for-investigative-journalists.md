---
id: epic-journalists-pyrite-wiki-hosted-research-platform-for-investigative-journalists
title: 'Epic: journalists.pyrite.wiki — Hosted Research Platform for Investigative Journalists'
type: backlog_item
tags:
- epic
- product
- journalists
- multi-user
- hosted
- superseded
importance: 5
kind: epic
status: superseded
priority: high
effort: XL
rank: 0
links:
- target: epic-pyrite-publication-strategy
  relation: superseded_by
  kb: pyrite
---

**Superseded by [[epic-pyrite-publication-strategy]].** This epic was
an earlier version of the same thinking, scoped to
`journalists.pyrite.wiki` as the first branded deploy. The successor
epic rebrands the first deploy as `investigate.transparencycascade.org`,
generalizes "branded hosted Pyrite" into a white-labelable feature, and
adds the static-publication-sites surface (capturecascade.org,
detention-industrial) as a co-equal concern.

Remaining open tickets from this epic (invite-code registration, KB
seeding/packaging, authenticated MCP transport wiring, onboarding flow,
backup automation) are now tracked under sub-epic B of the successor.

---

Original content preserved below for reference.

## Vision

Invitation-only hosted Pyrite instance pre-loaded with 6 curated investigative KBs (~5,900 entries). Three access paths: web UI, authenticated MCP, REST API.

Value proposition: not selling software (it's open source). Selling a research head start — curated, sourced, cross-linked investigative knowledge plus AI-powered research tools.

## Phased Launch

### Phase 0: Read-Only Research Library (Low effort, immediate value)
- Deploy instance with auth enabled, invite-code registration
- Pre-load 6 KBs: cascade-timeline, cascade-research, cascade-solidarity, epstein-network, surveillance-industrial-complex, thiel-network
- Journalists get: search (keyword/semantic/hybrid), browse, graph, timeline, entry detail with sources, tags browser
- No AI features needed — pure research library access
- **Blocks**: invite-code registration system, KB packaging/seed script
- **Ready**: auth, deployment config (clone selfhost/), web UI, journalism-investigation extension

### Phase 1: Bring Your Own AI (Medium effort)
- Per-user encrypted API key storage (extend existing Fernet token infrastructure)
- Users configure Anthropic/OpenAI key in profile settings
- AI features activate: summarize, auto-tag, suggest links, RAG chat
- **Blocks**: BYOK per-user API key storage + routing
- **Cross-ref**: relates to [[byok-api-key-management-for-multi-user-deployments]]

### Phase 2: Connect Your Tools (Medium-high effort)
- Authenticated MCP endpoint (SSE or Streamable HTTP transport)
- Bearer token auth mapped to user accounts with per-KB permissions
- Claude Desktop / Claude Code connects directly
- **Blocks**: MCP transport + auth wiring
- **Cross-ref**: relates to [[authenticated-mcp-endpoint-for-remote-access]]

### Phase 3: Collaborate (Future)
- Per-user git worktrees for edits (V1 model: user branches + admin merge)
- Annotation/commenting on entries
- Research workflow templates
- **Cross-ref**: depends on [[epic-per-user-fork-system-for-multi-user-editing]] (V1 worktree system is already built: WorktreeService, merge queue, write routing all done per ADR-0024)

## Multi-User Git Architecture (Cross-Reference)

The V1 multi-user model (from memory/project notes):
- All KBs public/readable by everyone
- Edits happen on per-user git worktrees (not full clones)
- Users see main branch by default; edits create user-specific branches
- 'Submit changes' flags worktree for admin integration
- Admin reviews and merges into main via merge queue
- No GitHub PR workflow, divergence detection, or conflict resolution UI needed for V1

Core infrastructure already built:
- WorktreeService (done)
- Write routing to user worktrees (done)
- Admin merge queue UI (done)
- WorktreeResolver DI (done)

Remaining for Phase 3: onboarding flow, research workflow templates, fork divergence indicators (deferred), conflict resolution (deferred).

## What's Ready Now

| Component | Status |
|-----------|--------|
| Multi-user auth (local + GitHub OAuth) | Ready |
| Role-based access (read/write/admin) | Ready |
| Per-KB permissions | Ready |
| Usage tiers/quotas | Ready |
| Docker + Caddy deployment | Ready (clone selfhost/) |
| KB content (6 KBs, ~5,900 entries) | Ready (needs packaging) |
| Web UI (search, browse, graph, timeline) | Ready |
| Tags browser | Ready (just added) |
| Journalism-investigation extension | Ready |
| WorktreeService + merge queue | Ready |
| Write routing to user worktrees | Ready |

## What to Build

| Item | Phase | Effort | Priority |
|------|-------|--------|----------|
| Invite-code registration | 0 | S | Critical |
| KB packaging/seed script for 6 KBs | 0 | S | Critical |
| Deploy config for journalists.pyrite.wiki | 0 | S | Critical |
| BYOK per-user API keys | 1 | M | High |
| Authenticated MCP endpoint | 2 | M-H | High |
| Onboarding flow for journalists | 1-2 | M | Medium |
| Backup automation | 0-1 | S | Medium |
| Research workflow templates | 3 | M | Low |
