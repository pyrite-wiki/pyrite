---
id: adr-0018
type: adr
title: "Web UI KB Management via Git Forks"
adr_number: 18
status: accepted
deciders: ["markr"]
date: "2026-03-05"
tags: [architecture, web-ui, git, multi-user, storage]
links:
  - target: "adr-0001"
    relation: "extends"
    note: "Builds on git-native storage for multi-user web workflows"
  - target: "adr-0003"
    relation: "extends"
    note: "Two-tier durability applies per-fork"
  - target: "adr-0024"
    relation: "superseded_by"
    note: "In part: the per-user fork/clone/PR mechanics are replaced for the single-instance case (see ADR-0024 'What this supersedes from ADR-0018')"
---

> **Superseded in part by [[adr-0024]] (the per-user fork, shallow-clone,
> GitHub-PR and org/user-directory mechanics, for the single-instance
> multi-user case).** ADR-0024 says this design "remains valid as a future
> evolution for federated/cross-instance collaboration"; that part stands.

> **Status.** The status stays `accepted` and not `superseded`: [[adr-0024]]
> supersedes only the single-instance mechanics, and this design stands for
> cross-instance collaboration. [[adr-0024]] is itself being superseded by
> [[adr-0044]] (proposed).

# ADR-0018: Web UI KB Management via Git Forks

## Context

Pyrite's web UI needs multi-user KB management where users can browse, edit, and contribute to knowledge bases. The core challenge: multiple users editing the same git-backed KB creates conflicts, and we need a workflow that preserves Pyrite's git-native storage model (ADR-0001) while enabling concurrent collaboration.

Key requirements:
- Users see their own version of the KB by default
- The UI shows links to upstream where content has diverged
- Merging user changes back to the canonical KB uses pull requests
- Storage efficiency matters — full clones per user don't scale

## Decision

### Architecture: Per-User Forks with Shallow Clones

```
server filesystem/
├── orgs/
│   └── acme-corp/
│       ├── repos/
│       │   └── knowledge/              ← upstream (bare or working)
│       │       ├── kb/                  ← KB 1
│       │       └── research/            ← KB 2
│       └── config.yaml
├── users/
│   └── alice/
│       ├── forks/
│       │   └── acme-corp--knowledge/   ← shallow fork
│       │       ├── kb/
│       │       └── research/
│       └── config.yaml
```

**Hierarchy:**
1. **Organizations** own repos. Each org gets a filesystem directory.
2. **Repos** are git repositories. A repo can contain multiple KBs in different base directories.
3. **Users** get their own directory with shallow forks of repos they access.
4. **Forks** are `git clone --depth=1` of the upstream repo. They share git objects where possible.

### Fork Lifecycle

1. **First access:** User visits a KB → system creates a shallow fork (`git clone --depth=1 --branch main <upstream>`)
2. **Browsing:** Reads come from the user's fork. Fast, no locking.
3. **Editing:** Writes commit to the fork's local branch.
4. **Syncing:** Periodic `git fetch --depth=1 origin main` pulls upstream changes. Auto-merge if no conflicts.
5. **Contributing:** User clicks "Submit changes" → system creates a GitHub PR from fork branch to upstream main.
6. **Merging:** PR review and merge happens on GitHub (or via Pyrite admin API). Post-merge, forks pull the update.

### UI Divergence Indicators

When the user's fork diverges from upstream:

- **Entry list:** Badge showing "N entries differ from upstream"
- **Entry view:** If the user's version differs from upstream, show a banner: "Your version differs from upstream. [View upstream] [Submit PR]"
- **Diff view:** Side-by-side comparison of user's version vs upstream
- **Sync status:** Header indicator showing fork sync state (up-to-date, behind, ahead, diverged)

The UI always shows the user's fork by default. Upstream content is fetched on-demand for comparison.

### Existing Infrastructure

Pyrite already has building blocks for this:

| Component | Location | Capability |
|-----------|----------|------------|
| `RepoService.fork_and_subscribe()` | `pyrite/services/repo_service.py` | GitHub fork + clone + remote setup |
| `RepoService.subscribe()` | `pyrite/services/repo_service.py` | Clone with remote tracking |
| `RepoService.sync()` | `pyrite/services/repo_service.py` | Pull upstream changes |
| `GitService.fork_repo()` | `pyrite/services/git_service.py` | GitHub API fork creation |
| `GitService.clone()` | `pyrite/services/git_service.py` | Git clone with token injection |
| `GitService.pull()` / `push()` | `pyrite/services/git_service.py` | Push/pull with auth |
| `UserService` | `pyrite/services/user_service.py` | User identity and auth |

### Conflict Resolution

- **Auto-merge:** If upstream and fork changes don't overlap, git merge handles it.
- **Conflict:** If merge fails, the fork stays on its branch. UI shows "Merge conflict — resolve manually or discard your changes."
- **Discard:** User can reset fork to upstream: `git reset --hard origin/main`.

### Storage Efficiency

- **Shallow clones** (`--depth=1`): Only latest commit, not full history. ~10x smaller.
- **Shared objects:** On the same filesystem, git can hardlink objects between repos.
- **Lazy forks:** Forks are created on first write, not first read. Read-only users can read from upstream directly.
- **TTL cleanup:** Inactive forks (no commits in 30+ days) can be garbage-collected and recreated on next access.

## Consequences

### Positive
- Users work in isolation — no locking, no real-time conflict
- Git history preserved for all changes (audit trail)
- PR-based review workflow for quality control
- Scales to many users without repo contention
- Leverages GitHub's PR infrastructure for review/merge

### Negative
- Disk usage: one shallow clone per user per repo (mitigated by shallow + lazy)
- Stale forks: users may edit old content if they don't sync
- Merge complexity: non-trivial conflicts require manual resolution
- GitHub dependency: PR workflow assumes GitHub (could abstract to other forges)

### Neutral
- SQLite index (engagement tier) remains per-fork — each user has their own index
- Embedding vectors are per-fork (could share upstream embeddings as optimization)

## Implementation Phases

1. **Phase 1:** Org/user directory structure, fork creation, basic read/write
2. **Phase 2:** Sync and divergence detection, UI indicators
3. **Phase 3:** PR creation and merge workflow
4. **Phase 4:** Conflict resolution UI, storage optimization
