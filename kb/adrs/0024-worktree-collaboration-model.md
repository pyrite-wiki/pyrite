---
id: adr-0024
title: "Git Worktree Collaboration Model"
type: adr
adr_number: 24
status: accepted
date: 2026-03-30
tags: [architecture, collaboration, git, multi-user]
links:
- target: adr-0018
  relation: supersedes
  kb: pyrite
- target: epic-fork-system
  relation: tracked_by
  kb: pyrite
- target: adr-0029
  relation: amended_by
  kb: pyrite
---

> **Amended by [[adr-0029]] (section 6, "Worktrees are user-leased
> ephemerals").** Where the two differ, ADR-0029 section 6 wins: a user
> worktree is an ephemeral KB leased to a user session; its search index is a
> per-worktree *diff* index (overlay), not a full index per worktree;
> coordination KBs and runtime state are exempt from worktree routing;
> worktree GC is the lease reaper (Phase 3 below). See also
> `kb/designs/adr-audit-2026-10.md` for sections whose standing is open.

> **Being superseded by [[adr-0044]]** (internal pull requests; proposed, not yet
> accepted). Until it is accepted this ADR keeps `accepted`. Worktree routing is
> off for now (maintainer decision, 2026-10-02), so the routing and per-user
> worktree sections describe code that exists but is not switched on. Amended
> by [[adr-0029]] section 6 (above) and by [[adr-0043]] (the "all KBs readable by
> all authenticated users" permissions model is replaced by per-KB grants). The
> evidence is in `kb/designs/adr-audit-2026-10.md`.

# ADR-0024: Git Worktree Collaboration Model

## Context

Pyrite needs multi-user editing for shared investigator instances. ADR-0018 designed a per-user fork system using shallow clones, GitHub PRs, and org/user directory hierarchies. That design is comprehensive but heavyweight — it requires GitHub accounts for all contributors, OAuth token management, fork lifecycle management, and storage for full clones per user.

For the actual use case (a small group of investigators sharing a Pyrite instance seeded with existing KBs), we need something simpler: users log in, edit entries, and submit changes for admin review. No GitHub dependency, no fork management, no external service coordination.

### Why git worktrees

Git worktrees (`git worktree add`) create additional working directories backed by the same repository. They share the entire object store — branches, commits, blobs — with zero data duplication. Each worktree checks out a different branch, enabling concurrent work without interference.

At Pyrite's scale (single-digit concurrent users), worktrees are ideal:
- **Zero storage overhead** — shared object store, only working directory files are duplicated
- **No network operations** — everything is local, no GitHub API calls
- **Branch-per-user** — natural isolation with standard git merge for integration
- **Instant creation** — `git worktree add` takes milliseconds

## Decision

**Use git worktrees for per-user editing with an in-app admin merge queue. No GitHub dependency for collaboration.**

### Architecture

```
/path/to/kb-repo/                    ← main branch (canonical, read-only for users)
├── kb/
├── cascade-research/
├── ...
└── .git/
    └── worktrees/
        ├── user-alice/              ← worktree on branch user/alice
        ├── user-bob/                ← worktree on branch user/bob
        └── user-carol/             ← worktree on branch user/carol
```

### Read/write routing

- **All users read from main.** The shared view shows canonical content.
- **On first edit, a worktree is created** on branch `user/{username}` forked from current main.
- **All subsequent writes** go to the user's worktree. The user sees their own edits immediately.
- **Index per worktree** — each worktree gets its own search index so user edits are searchable.

### Submission and merge

- **"Submit changes" button** — user flags their branch as ready for review. Sets a `submitted_at` timestamp in the DB.
- **Admin merge queue** — admin panel lists all submitted branches with:
  - Diff against main (files changed, entry-level diffs)
  - Accept (merge branch into main, rebase user branch)
  - Reject (with optional feedback message, clears submitted flag)
- **Post-merge** — user's worktree is rebased onto updated main. If the rebase fails (conflict), the worktree is reset to main and the user is notified.

### Permissions model (V1)

- All KBs are readable by all authenticated users
- Users can only write to their own worktree
- Only admins can merge into main
- No per-KB permissions in V1 (add later if needed)

### What this supersedes from ADR-0018

| ADR-0018 concept | V1 replacement |
|------------------|----------------|
| Per-user shallow clones | Git worktrees (zero-copy) |
| GitHub fork workflow | Local branch per user |
| GitHub PR for review | In-app merge queue |
| Org/user directory hierarchy | `.git/worktrees/user-{name}/` |
| Divergence detection UI | Diff in merge queue (admin-only) |
| Conflict resolution UI | Admin handles manually; user worktree reset on conflict |

ADR-0018's design remains valid as a future evolution for federated/cross-instance collaboration. This ADR handles the single-instance multi-user case.

## Consequences

### Positive
- No GitHub dependency for collaboration — self-contained
- Near-zero storage overhead per user
- Simple mental model: "you edit on your branch, admin merges"
- Works with any git hosting (or no hosting) as upstream
- Existing git_ops endpoints need minimal changes

### Negative
- No remote backup of user work until merged to main and pushed
- Admin is a merge bottleneck (acceptable at small scale)
- Concurrent edits to the same entry produce merge conflicts (admin resolves)
- Each worktree needs its own search index (disk cost: ~1 index per user)

### Neutral
- Users don't see each other's unmerged work (isolation by design)
- Git history shows user attribution via branch commits
- Can add GitHub push/PR workflow later as an optional "export to upstream" feature

## Implementation

### Phase 1: Worktree management + write routing
- `WorktreeService`: create, list, get_path, reset, delete
- Request-scoped KB path resolution: reads from main, writes to user worktree
- Per-worktree index (reuse existing IndexManager with different DB path)

### Phase 2: Submission and merge queue
- "Submit changes" UI button + API endpoint
- Admin merge queue page: list submitted, diff, merge, reject
- Post-merge rebase and notification

### Phase 3: Polish
- User's "my changes" view showing their pending edits
- Entry-level "edited by you" indicators
- Worktree GC for inactive users
