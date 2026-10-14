---
id: adr-0029
type: adr
title: "Libraries, KB Lifecycles, and Runtime State — the registry's single source of truth"
adr_number: 29
status: accepted
deciders: ["markr"]
date: "2026-07-03"
tags: [architecture, registry, config, libraries, ephemeral, tasks, storage]
links:
- target: adr-0003
  relation: supersedes
  kb: pyrite
- target: collapse-kb-registry-to-one-source-of-truth
  relation: implements
  kb: pyrite
---

> **Amended by [[adr-0039]] (2026-10-03).** Section 1 (the registry), section 3
> (ephemeral KBs never in the library YAML) and section 4 (the claim guard, with
> [[adr-0042]]) are amended: the operator's config file is the registry, the
> server never writes it, and ephemeral KBs are database rows only. Section 6 also
> amends [[adr-0024]], and [[adr-0043]] amends the authorization parts. Sections
> 2 to 4's libraries, leases and state tables are still unimplemented (0.26 by
> their own phasing). Evidence: `kb/designs/adr-audit-2026-10.md`.

## Context

Every recurrent field bug traces to derived state diverging from its
source of truth, and the KB registry is the worst offender: KBs live
in both `config.yaml` and the DB `kb` table (bridged by an in-memory
cache), plus a second user-level YAML — `~/kb/config.yaml` and
`~/.pyrite/config.yaml` disagree on ~25 KBs in the primary
deployment. Ephemeral/test KBs leak into the durable registry
(`/private/tmp/test-release-kb` is still registered months later).
Coordination artifacts live inside canon KBs
(`cascade-research/notes/` mixes 1,549 task/lead/synthesis files).
Claim liveness is inferred from file mtimes, producing a documented
near-miss duplicate dispatch and ~100 lines of defensive liturgy in
the investigation-conductor skill. The shared-instance pilot
(epic-shared-instance-readiness) needs "what peers can see" to be a
reviewable artifact, not an accident of which `kb add` commands ran
on which machine.

The founding principle (ADR-0001/0003) is that files in git are the
record and the database is disposable derived state. The registry
architecture violates that principle inside the tool's own plumbing.

### Alternatives considered

1. **DB `kb` table as the registry's source of truth.** Rejected: the
   registry becomes the one thing in pyrite that cannot be
   reconstructed from a clone, diffed, or reviewed; violates the
   founding principle; loses the audit trail ("peer X was granted KB
   Y" as a commit) that the journalist threat model values.
2. **Three storage classes of KB** (versioned / DB-durable /
   ephemeral-DB-only). Rejected after analysis: it split KB physics
   into two truth regimes and ended the unconditional "DB is
   disposable" recovery property. The class-2 (DB-durable) category
   turned out to conflate two different things — entry-shaped data
   (which works as files) and row-shaped data (which was never KB
   content at all).
3. **Status quo** (dual registry, convention-guarded). Rejected: it
   is the bug class.

## Decision

**One sentence: everything in pyrite is markdown in a git repo; some
repos have no upstream and are leased; everything else in the
database is either derived index or declared machinery.**

### 1. YAML is the registry's source of truth — exactly one file

The KB registry lives in a YAML file. `pyrite kb create` / `kb add`
WRITE that file (ruamel round-trip, file lock + atomic rename). The
DB `kb` table is demoted to a derived cache rebuilt from the file at
load — never authoritative, covered by the same verify-after-write
discipline as the entry index. The user-level dual-YAML drift is
resolved by consolidation: `~/.pyrite/` owns the registry;
`~/kb/config.yaml` is migrated in and retired.

### 2. The file is a library definition

A **library** is a named set of KBs + the derived DB that indexes
exactly that set + settings:

```yaml
# ~/.pyrite/libraries/journalism.yaml
library: journalism
db: ~/.pyrite/db/journalism.db      # derived; delete = rebuild
knowledge_bases:
  - name: cascade-research
    path: ~/tcp-kb-internal/cascade-research
  - name: cascade-timeline
    path: ~/cascade-kb/cascade-timeline
    mode: readonly                   # optional; see ownership rule
settings:
  search_mode: keyword
```

- A library file is **self-contained and closed**: it lists its KBs
  in full. No cross-file merging, no global-plus-library overlays —
  the dual-registry disease was two half-authorities; a library is
  one whole authority for one context, selected atomically. The only
  global state is a two-line pointer (`current: journalism`) plus
  `--library` / `PYRITE_LIBRARY` overrides.
- A **deployment serves one library**. The pilot's shared instance
  is a library file versioned in its deploy repo — what peers see is
  a reviewed, diffable artifact. A peer-facing server structurally
  cannot see KBs outside its library, before auth is even consulted.
- The library is the **contention domain**: task claims are CAS
  against the library DB. Therefore every KB has exactly ONE writing
  library; other libraries may mount it `mode: readonly` (enforced
  at write time). Without this rule, per-library DBs would silently
  narrow the claim guard's scope and allow cross-library duplicate
  claims on the same task file.

### 3. One KB physics, two lifecycles

Every KB is files + a git repo + a derived index. Two lifecycle
settings:

- **Durable**: has an upstream (or is deliberately long-lived
  local); listed in the library YAML; never GC'd. Recovery: clone +
  reindex.
- **Ephemeral**: local `git init`, no upstream; NEVER written to the
  library YAML — attached to the running library as a runtime
  overlay; namespaced (`eph-` prefix); **leased** to a task (a
  single worker's task, or the parent/epic task for multi-agent job
  memory); lives in a scratch root the GC owns. Commits happen at
  worker checkpoints, not per write, so promotion reviews a
  meaningful diff. Three exits: **reap** (lease expires → delete dir
  + index rows, verified), **promote** (entries move into a durable
  KB through the validated write path, links rewritten), or
  **graduate** (the whole KB proves durably useful and is added to
  the library YAML — the pipeline-conductor-log pattern).
  A crashed job's memory KB remains inspectable until the lease
  reaper takes it.

Uses for ephemerals: research-task scratch, multi-agent job memory
(the blackboard pattern the conductors already run via "stateful,
not message-based" coordination), tasks-as-lock, test KBs.

**Link rule: links may point up the durability ladder, never down.**
Durable entries must not wikilink into ephemeral KBs (they would
dangle after GC). Enforced as a QA rule.

**Task placement**: tasks live in coordination KBs or ephemeral KBs,
never loose in canon. Two task species, declared via a
`retention: record | lock` field:
- *record* (e.g. the investigation backlog): work-log-bearing
  provenance the conductors read back ("the log trail is how future
  ticks know what already happened"); durable, archived on close.
- *lock* (e.g. draft-conductor phase-claim primitives): pure
  mutexes; ephemeral, reaped.

### 4. Runtime state is machinery, not content

Row-shaped data — **claim leases, grants, quotas, engagement
counters** — was never KB content and does not become files. It
lives in DB tables declared in an owned registry of state tables.
`rebuild` is a single owned operation that drops **derived** tables
only and structurally cannot touch state tables. Recovery for state
is DB backup/restore (now load-bearing).

- **Claim leases**: the claim CAS is unchanged — it remains the one
  concurrency guard. A lease (`lease_expires_at`) is added ON TOP:
  renewal piggybacks on any write by the assignee, with
  `task checkpoint` as the explicit heartbeat; long-running workers
  must never lose a live claim. Reclaim is itself a CAS
  (`WHERE status = in_progress AND lease_expires_at < now`).
  Parking (`parked_awaiting:`) is a declared no-lease state. This
  retires the mtime-proxy liveness inference and most of the
  investigation-conductor's stale-claim procedure.
- **Grants** (hosted multi-user): which user sees which KB is state,
  not registry. Users never mutate the library file; operators
  version it.
- The physical split of state tables into a separate `state.db` is
  **deferred with named triggers**: (a) hosted quotas/grants carry
  real multi-user weight, (b) state backup cadence needs to differ
  from index lifecycle, or (c) the social plugin gets a real
  deployment. Until then: one DB file, enforced table-ownership
  boundary.

### 5. ADR-0003 is superseded (absorbed, not reversed)

ADR-0003's two-tier durability was this design in miniature. Its
content tier generalizes to "every KB, one physics, two lifecycles";
its engagement tier was the first sighting of the runtime-state
category. Votes/reputation counters are state tables; entry-shaped
engagement (e.g. reader comments on a published site) would be KB
entries. The social plugin's disposition: **extract, don't delete**
(first candidate for plugin-repo-extraction; unused today; live use
case recorded in `kb/positioning/support-docs-platform.md`).

### 6. Worktrees are user-leased ephemerals (unifies ADR-0024)

The multi-user editing model (ADR-0024: worktree per user, branch
`user/{name}`, submit → admin merge queue) is an instance of this
ADR's ephemeral lifecycle, not a parallel system:

- A user worktree is an **ephemeral KB leased to a user session**
  (local branch, no upstream, per-worktree diff index via
  OverlaySearchBackend). Its exits map exactly: **promote** = admin
  merge (the in-app "pull request"), **reap** = worktree GC for
  inactive users (ADR-0024 Phase 3 — implement it AS the 0.26 lease
  reaper with a user-session lease-holder type, not as bespoke
  machinery), **reset** = explicit discard. `submitted_at` and queue
  state are runtime state (machinery).
- **Coordination KBs and runtime state are exempt from worktree
  routing** — always shared, never overlaid. Tasks are shared state;
  a claim made in one user's diff DB would be invisible to another
  user's CAS, silently breaking the one concurrency guard. Worktree
  write-routing applies to canon content KBs only.
- **Invariant: no user commit is ever unreachable** after any
  merge/reject/reset path (branch refs must survive worktree
  resets). Tracked: [[worktree-no-lost-commits-invariant]].
- **Write-phase gates** (post-pilot; the 0.25 pilot is read-only):
  MCP/CLI writes do not route through worktrees today (the resolver
  is server-side only) — "peer agents get worktree-routed writes
  over MCP" is the real multi-user milestone (0.27-shaped; belongs
  in the parity matrix). Overlay V1 limits (no delete tombstones —
  deleted entries reappear from main; graph/tags/semantic served
  from main only) must be closed before peers write. Merge/reject
  outcomes surface as events per
  [[notifications-condition-ledger]].

## Consequences

- The "silently skips DB-registered KBs" bug class dies structurally:
  one accessor (`all_kbs()`) over one truth (the library file) plus a
  marked ephemeral overlay.
- `library rebuild` (delete DB, reindex) stays unconditionally safe —
  the recovery property that has absorbed every index bug to date is
  preserved with zero exceptions.
- Registry changes become commits: auditable, reviewable, and
  reconstructable from a fresh clone.
- Canon KBs decontaminate: task/coordination residue stops
  accumulating in research corpora (the cascade-research junk-drawer
  cause is removed at the architecture level; migration is the
  operation's own choice of timing).
- Startup parses the library YAML; a hand-edited typo becomes a
  load-time error with a good message rather than silent divergence.
- New costs accepted: file lock + atomic rename on registration
  writes (rare); a scratch-root GC daemon/sweep for ephemerals; DB
  backup becomes load-bearing for state tables.

## Phasing

- **0.25 (scope-frozen set, unchanged):**
  [[collapse-kb-registry-to-one-source-of-truth]] implements the
  format + consolidation only — the single consolidated YAML becomes
  a valid library file named `default`; `kb add`/`create` write it;
  DB demoted to verified cache; `~/kb/config.yaml` migrated in;
  `all_kbs()` sweep completed. No switching, no leases, no ephemeral
  rework in 0.25.
- **0.26:** library switching (`pyrite library list/switch/create`,
  `--library`, per-library DBs, readonly mounts), claim leases,
  ephemeral leasing/GC/promotion, coordination-KB task migration,
  state-table registry.

## Evidence base

The design must express everything the investigation-conductor does
today (task claims, stale sweeps, parked tasks, work-log read-back,
child tasks, cross-KB handoff to draft-conductor) — that skill is the
migration test. Field evidence: dual-registry fixes f0304ec/f193ef7
and their recurrence; leaked test-KB registrations; the mtime
near-miss documented in investigation-conductor Step 1; the
2026-07-02/03 audits.
