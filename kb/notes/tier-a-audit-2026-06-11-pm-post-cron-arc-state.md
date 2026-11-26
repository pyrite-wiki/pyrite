---
id: tier-a-audit-2026-06-11-pm-post-cron-arc-state
title: Tier A audit 2026-06-11 PM — post-cron-arc state
type: article
tags:
- backlog
- audit
- grooming
- tier-a
importance: 5
quality: stub
review_status: draft
protection_level: none
lifecycle: archived
---

## TIER A AUDIT (post-cron-arc 2026-06-11 PM)

This is a structural sweep of the remaining Tier A (rank ≥ 1000)
backlog after the long /pyrite-dev cron arc that closed r1000-r1300,
r1700, r1900, r2000-r2300, locked r1175 + r1500 design questions,
and added r1300 and r2200 follow-ups.

The audit finds 6 items remaining in Tier A. **None is a close-and-
ship single-fire shape.** Three are blocked on user decisions, one
needs a fixture to reproduce, and two are XL epics that need
decomposition. Plus one unranked Mark-filed item that is Tier-A-
shaped but unranked.

### Cluster: blocked on user design decision (3 items)

| Rank | ID | Effort | What's blocked |
|---|---|---|---|
| r1500 | split-plugin-protocol-into-capability-protocols | M | Option A (5 capability protocols) vs Option B (one Protocol + `capabilities: set` declaration). Yielded in commit a99e05c. 7 sub-decisions documented in ticket body. |
| r1400 | split-backend-protocol-entitystore-searchengine-embeddingstore | L | Same shape as r1500: split a fat Protocol into capability protocols, with the added wrinkle that the existing 39 methods straddle three responsibilities (Entity CRUD, Search, Embeddings) AND two backends (SQLite, Postgres) with non-trivial divergence. Also gated by ADR-0013's Phase 2/3, which is itself a backlog item. |
| r1175 | feat-relaxed-state-machine-mode | S | Yielded in commit 981e02b. 7 sub-decisions documented in ticket body (config location, status_reason field shape, toggle granularity, backward-compat, CLI ergonomics, atomic-claim preservation, retire-in-same-commit). |

**These are not "the fire failed" yields — they are "this is bigger
than 4 minutes and the design space is forked." Asking 5+ fires of
the loop to speculate and refactor would burn cycles to land in the
wrong place.**

For r1500 / r1175 the ticket bodies already contain recommended
answers to each sub-decision. If those recommendations are accepted
as-written, a fire can go straight to RED tests + implementation.

### Cluster: blocked on better reproduction (1 item)

| Rank | ID | Effort | What's blocked |
|---|---|---|---|
| r1150 | bug-task-status-single-item-json-read-intermittently-empty-while-list-view-correct | S | Read-after-write timing window between `task status <id>` and `task list`. Conductor session observed it once on 2026-06-10; no isolated test has reproduced it since. The empty `pyrite task list` against the `pyrite` KB (no tasks exist here) means a fixture KB is needed before any test can pin it. Don't fix speculatively — same Iron Law as r1100 reminded us when it was filed without empirical reproduction. |

Verified at HEAD: `pyrite task list -k pyrite --format json` returns
`{count: 0, tasks: []}` — no tasks in this KB. To repro, a fixture KB
with tasks + a forced claim-then-immediate-read would need to be
built, ideally as a deliberately-tight loop that exposes the race.

### Cluster: XL epics needing decomposition (2 items)

| Rank | ID | Effort | What's needed |
|---|---|---|---|
| r1600 | epic-normalization-and-data-cleanup | XL | Epic. Body covers KB normalization, data health, cascade plugin deprecation. Multi-week effort. Needs decomposition into sub-tickets with their own ranks before any fire can pick up a slice. |
| r2400 | epic-pyrite-publication-strategy | XL | Epic. Body covers static sites + hosted investigation instance. Multi-week effort. Same — needs decomposition. |

These are not stuck — they're correctly sized as epics, just at the
"epic" granularity. A grooming pass (not a /pyrite-dev fire) is the
right next step: read the body, file 4-8 sub-tickets, rank them,
retire the epic shell or keep it as a tracking entry.

### Unranked (Tier-A-shaped, awaits ranking)

| ID | Effort | Why Tier-A-shaped |
|---|---|---|
| feat-editorial-notes-sidecar | M | Filed by user this session. Body says editorial metadata is "the PRIMARY SOURCE of malformed-YAML index-sync failures" with ~27 drafts unindexed — same grooming-tools-sabotage-grooming family as r1100/r1200/r1300. Same design-question profile as r1175/r1500 (7+ open Qs). Per fire 16's status: recommend r1170 (between r1175 relaxed-state-machine and r1150 task-status-empty since both are conductor-friction items). User to confirm rank. |

### Recommended next moves

In rough order of leverage:

1. **Answer the 7 design Qs for r1175 and r1500** (and r1400 if it
   follows the same Option A vs B shape) so the loop can resume
   close-and-ship work on them. Each ticket has recommended answers
   in its body — confirming or modifying takes minutes.

2. **Rank feat-editorial-notes-sidecar** (suggest r1170) and decide
   whether it overlaps with r1175 enough to bundle (both touch the
   "what lives in frontmatter vs. somewhere else" decision).

3. **Decompose r1600 and r2400** into sub-tickets. This is a grooming
   pass, not a development fire — reading the epic bodies and filing
   4-8 children each.

4. **r1150** stays flagged. Build the fixture-based repro the next
   time the conductor surfaces it in a real session, or as a follow-up
   to whatever task-system work lands (it likely co-locates with r1175
   if the relaxed mode lands).

### Recommendation against continued cron-loop fires on the current queue

The original work queue (r1000-r1200) is fully closed. The
discretionary close-and-ship work I picked up (r1300, r1700, r1900,
r2000-r2300) is also closed. Continuing to chain /pyrite-dev fires
will increasingly produce design-Q yields rather than landed code,
which means each fire is paying the prompt-cache TTL for very
little incremental closure.

**Recommend pausing the cron loop until the design Qs above are
answered** OR until the user files a new bug with a clear repro.
The next high-leverage move is a grooming session, not another
fire.
