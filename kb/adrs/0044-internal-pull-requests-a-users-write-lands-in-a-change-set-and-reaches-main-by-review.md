---
id: adr-0044
type: adr
title: "Internal pull requests: a user's write lands in a change set and reaches main by review"
adr_number: 44
status: proposed
date: 2026-10-02
tags: [architecture, multi-user, git, collaboration, write-path, invariants]
links:
- target: adr-0024
  relation: supersedes
  kb: pyrite
- target: adr-0029
  relation: amends
  kb: pyrite
- target: adr-0032
  relation: related
  kb: pyrite
- target: adr-0037
  relation: related
  kb: pyrite
- target: adr-0038
  relation: related
  kb: pyrite
- target: adr-0039
  relation: related
  kb: pyrite
- target: adr-0041
  relation: related
  kb: pyrite
- target: adr-0042
  relation: related
  kb: pyrite
- target: adr-0043
  relation: related
  kb: pyrite
- target: worktree-no-lost-commits-invariant
  relation: related
  kb: pyrite
---

# ADR-0044: Internal pull requests

> **Proposed** (spike `internal-pull-requests`, 2026-10-02). The maintainer
> accepts or rejects it. It describes the **end state** for multi-user writes
> and replaces ADR-0024. It does not change what the maintainer
> decided on 2026-10-02 for now: **every authorised write lands on the
> canonical files and worktree routing is off.** Nothing here is built until
> the maintainer says so; multi-user stays experimental and local use by one
> operator comes first. It depends on ADR-0039 (where the per-KB `landing`
> field lives), ADR-0042 (where a single-entry read comes from and which hash it
> returns) and ADR-0043 (the operator's plane). Code references are `origin/dev`
> at `ed71b8b0`. The git experiments ran on git 2.39.5; the scripts and their
> output are in the spike's working directory, not in the repository, and
> are not reproduced here.

## Context

The maintainer's end goal, in his words: "users … commit to their worktree,
and then create internal pull requests to the main kb from there."

Four things are already decided and are constraints here:

- A KB is Markdown files with YAML frontmatter in git, usable by any tool.
  Files and git are the source of truth. Pyrite keeps no sidecar record about
  files others edit (ADR-0041).
- Where a write lands is one rule, a function of principal and KB, the same on
  every surface.
- Two planes: users read and write entries per KB by grant; everything else is
  the operator's (ADR-0043). Commit, push, publish and export are the
  operator's for now.
- Pyrite's own development already works this way (ADR-0032): one branch per
  batch of work in its own worktree, a pull request, checks on the exact
  commit about to land, and a queue that refuses instead of rebasing for you.
  That is the model to mirror.

### What the code does today

ADR-0024's implementation is one long-lived branch per user, wired into four
REST routes.

| What | Where | Behaviour |
|---|---|---|
| Routing | `server/endpoints/entries.py:753-758, 821-826, 899-905, 936-941` | Four REST entry routes send a write to a worktree when the global role is not admin. MCP, the CLI and every other REST write route land on the canonical files (`git grep worktree` finds nothing in `mcp_server.py` or `pyrite/cli`). |
| Working copy | `services/worktree_service.py:106-114, 187` | Path `<repo parent>/.pyrite-worktrees/<user>`, branch `refs/heads/user/<user>`: keyed by repo and user. |
| Record | `storage/migrations.py:354-371`; `worktree_service.py:161-172` | One row per `(user, kb)`. Its submitted, rejected and feedback fields are nulled when the row is reused. |
| Commit | `worktree_service.py:284-293` | Nothing is committed until submit, which runs `git add -A` and one commit. |
| Merge | `worktree_service.py:323`; `services/git_service.py:1377-1420` | `git checkout main` then `git merge` **in the operator's checkout**. `main` is a literal. |
| Reset | `worktree_service.py:378-384` | `git reset --hard main` in the worktree. |
| Reads | `server/worktree_resolver.py:49-64`; `storage/backends/overlay_backend.py` | An overlay of a per-user diff index over the main index, on entry get and list when a KB is named. Delete has no tombstone (`overlay_backend.py:50-56`). |
| After a merge | `server/endpoints/worktree.py:320-344` | No main-index sync, no event, no invalidation of the cached diff index (`invalidate_diff_cache` has no caller). |
| Entry reviews | `services/review_service.py:38-56` | A different thing: a QA verdict on one entry, a row keyed by git blob hash. |

### Measured: today's working copy

The architect's probe (P2 to P9: the real `WorktreeService` in a temp repo; the
files it exercises are unchanged since):

| | Situation | Result |
|---|---|---|
| P2 | An author writes and looks at "my changes" | Empty until submit: nothing is committed. |
| P3 | The same user writes to a second KB in the same repo | `StorageError`. |
| P9 | Two repos share a parent directory | The second collides on `.pyrite-worktrees/<user>`. |
| P4 | Merge while the operator has an uncommitted edit to the same file | Refused, reported as "Merge conflict". |
| P5 | Merge while the operator's checkout is on another branch | Succeeds, and leaves the operator's checkout switched to `main`. |
| P6 | After a merge | The user's branch is never brought up to date. |
| P7 | Reset after a committed edit | The commit is reachable from no ref. |
| P8 | A repo whose branch is `trunk` | Cannot merge. |

This spike added a read probe (real `WorktreeResolver`, main checkout at
`2e5d72e7`):

| | Situation | Result |
|---|---|---|
| R1 | Main moves; the author reads an entry they have not changed | They read main's new text. The file their next write patches still holds the old text. The two have different blob hashes. |
| R2 | The author then changes only that entry's title | Their own next read returns the old body. The text they read a moment before is gone from their view. |
| R3 | The author deletes an entry they had edited | The file is gone from their worktree; their read returns main's copy. |

### Measured: git can do what the end state needs

Temp repos, git 2.39.5. A 20,000-file repo (2,000 + 18,000 entries in two KBs)
for the costs.

| Question | Result |
|---|---|
| Can a merge be computed without touching any checkout? | Yes. `git merge-tree --write-tree` (git 2.38 or later) writes the merged tree in 7 ms; `git commit-tree` makes the merge commit. Neither touches a working tree, an index or a ref. |
| Can the operator's checkout then be advanced safely? | Yes. `git merge --ff-only <that commit>` in the canonical checkout (28 ms) moves the branch, the index and only the changed files. |
| Operator has an uncommitted edit to an **unrelated** file | Merge lands. The edit is untouched and still uncommitted. Same for a staged change to an unrelated file. |
| Operator has an uncommitted edit to a file the change touches | Refused by git before anything moves: "Your local changes to the following files would be overwritten". HEAD, index and the file are unchanged. |
| An untracked file where the change adds one | Refused the same way. |
| A real conflict | `merge-tree` exits 1 and names the files. No ref moves. |
| Operator's checkout is on another branch | The merge lands on that branch. `trunk`, or any name, works; nothing is switched. |
| Detached HEAD, or a merge or rebase in progress in the canonical checkout | Refused (`symbolic-ref` fails; "You have not concluded your merge"). |
| Main moves between computing the merge and advancing | Refused: "Not possible to fast-forward". The fast-forward is the compare-and-swap. |
| Can a change's branch live outside `refs/heads`? | Yes. A worktree's `HEAD` can be a symbolic ref to `refs/pyrite/changes/<id>`, once that ref exists. Commits advance it. `git branch` does not list it. |
| Is it published by an ordinary push? | `git push --all` publishes a `refs/heads/user/alice` branch. It does not publish `refs/pyrite/*`. `git push --mirror` publishes both. |
| What does a worktree write into the operator's repository? | The ref, the objects, and `.git/worktrees/<name>/` (`HEAD`, `index`, `gitdir`, `commondir`, logs). Nothing in the operator's working tree; `git status` stays clean. |
| Lease end | `git worktree remove` refuses a dirty working copy. After a commit it succeeds, leaves no `.git/worktrees` entry, and the tip survives `git gc --prune=now` because the ref holds it. Deleting the directory by hand and running `git worktree prune` gives the same. The working copy can be recreated from the ref. |
| Close without merging | One `git update-ref --stdin` transaction creates `refs/pyrite/closed/<id>` and deletes `refs/pyrite/changes/<id>`. The tip stays reachable. |
| A human with git pushes into the canonical repo | A push to the checked-out branch is refused by git's default (`receive.denyCurrentBranch`). A push to `refs/pyrite/changes/<name>` succeeds. |
| Cost of a working copy | Worktree, full checkout: 1.7 s, 78 MB. Worktree, sparse to one KB of 2,000: 154 ms, 8 MB, but it sets `extensions.worktreeConfig=true` in the operator's `.git/config`. `clone --local`: 1.0 s, 82 MB. A ref with no checkout: 9 ms. |
| Do the operator's git hooks run on a working-copy commit? | Yes: hooks are shared. `pre-commit` ran for a commit in the worktree; `--no-verify` skips it. `post-merge` runs on the fast-forward. |

## Options

**The working copy**

| | Gain | Cost |
|---|---|---|
| A branch with no checkout (plumbing only) | Nothing on disk, nothing under `.git/worktrees`. | Pyrite's write path, validation and hooks work on a directory (`KBRepository`, `find_file`, `DocumentManager`). Every write would need a second implementation. |
| A clone per change | Writes nothing into the operator's repository until the change is offered. A place to hang a remote, which federation (ADR-0018) would want. | The commits live only in Pyrite's data directory, so removing the clone at lease end would lose them unless they are first pushed into the operator's repository as refs. It then writes the same refs and objects a worktree does, and needs a fetch every time main moves. |
| A worktree per user, for ever (today) | One checkout per user. | Two sessions of one user share one checkout on one branch, which is the hazard ADR-0032 §1 retired. A second KB in the repo, a rejected change and a merged one all share the branch (P3, P6). |
| **A worktree per change set, as a disposable checkout of a ref** | Shares objects and refs with main at once. One session, one branch, one checkout. The ref holds the work, so the checkout can be removed and recreated at any time. | Writes `.git/worktrees/<name>` into the operator's repository while a checkout exists. One checkout on disk per change being worked on. |

**The record of a pull request**

| | Files are the truth | Privacy in a shared repo | ADR-0029 state/machinery |
|---|---|---|---|
| A row only | Fails if the row is all there is: delete the database and the change is gone from Pyrite's view. | Private by policy. | §6 already calls queue state machinery. |
| An entry in the KB or a coordination KB | A file. | Visible to every reader, published by a push, and rendered by a site generator. Tells readers what an investigator is working on. | Not machinery: it would be content about machinery. |
| git notes or a metadata ref (Gerrit NoteDb, git-appraise) | In git, outside the tree. | Not pushed by default. | A second store with its own format that no other tool reads. |
| **The ref is the change; the merge commit is the outcome; a row holds only what is neither** | The commits, the open/closed state and the outcome survive deleting the database. | The ref is not pushed by default; the row is private by policy; the outcome on main says only who and which change. | The row is machinery (submitted flag, feedback, checkout lease), as §6 says. |

## Decision

**A user's write to a reviewed KB lands in a change set: a git ref the user
owns, cut from main, one commit per write. An internal pull request is a
change set submitted for review. A merge is a merge commit on the branch the
canonical checkout is on, made without disturbing that checkout. The ref and
the merge commit are the record; the database holds only queue state.**

Vocabulary: **main** is the branch the canonical checkout is on, whatever its
name, and the files in that checkout. A **change set** is the unit of work. Its
**working copy** is a checkout of it. An **internal pull request** is a change
set whose author has submitted it.

### 1. One landing rule

`AccessPolicy.lands_in(principal, kb) -> Canonical | ChangeSet | Deny`, in
`pyrite/services/access_policy.py` beside `authorize` (ADR-0037 §1).

| Principal | KB `landing: canonical` (the default) | KB `landing: review` |
|---|---|---|
| The operator (CLI, stdio MCP, operator credential) | Canonical | Canonical |
| User with a write grant | Canonical | That user's change set |
| Anonymous (the policy grants it no write) | Deny | Deny |
| No write permission | Deny | Deny |

- `landing` is a per-KB policy field set by the operator **in the config file**
  (ADR-0039 decision 1), never in the KB's own tree and never by a surface.
  Default `canonical`, which is today's decision. Turning review on is the
  operator's choice per KB.
- **This is the attribute that marks a coordination KB** (ADR-0029 §6): it is a
  KB with `landing: canonical`. Tasks, claims and locks stay shared and are
  never overlaid. No separate "coordination" flag is needed.
- One service seam asks the rule. Every surface gets the `KBService` it writes
  with from one factory that takes the principal and the KB and returns a
  service bound to the canonical files or to the change set's working copy.
  REST, HTTP MCP, stdio MCP and the CLI all pass through it. No surface
  decides where a write lands.
- An agent over HTTP MCP is a user principal (ADR-0043), so it lands where
  that user lands. A write may name a change set (`change: <id>`); without one it goes
  to the user's current change set for that KB, opened on first write. An
  agent that works several tasks at once names one change set per task.
- If the landing place cannot be reached (not a git repository, the working
  copy cannot be created), the write is refused. It never falls back to
  canonical.
- Every write response says where it landed.

### 2. The change set and its working copy

- A change set is the ref `refs/pyrite/changes/<user>/<n>` in the repository
  that holds the KB, created at main's current commit. It is not under
  `refs/heads`, so `git branch`, `git push` and `git push --all` in the
  operator's repository are unaffected.
- **A change set belongs to one KB.** Every path it changes lies under that
  KB's directory. A write to another KB in the same repository opens another
  change set.
- Every write through Pyrite is one commit on the ref. Author: the principal.
  Committer: the Pyrite instance. Working-copy commits run no repository hooks.
  The working copy is therefore clean after every write, and the blob at the
  ref's tip is the file.
- The working copy is a git worktree under Pyrite's data directory, named so
  its `.git/worktrees` entry is recognisably Pyrite's. It is a cache of the
  ref: created when a write needs it, leased, and removed when idle. Removing
  it loses nothing. A full checkout is the default; a sparse one is an
  optimisation that needs a line in the operator's `.git/config`.
- The lease is on the checkout, not on the change. When a lease or an agent
  session ends, the checkout may be reaped. The change set stays open, owned
  by its author, listed under their changes, and any later session of that
  user can continue it by id.
- A change set leaves `refs/pyrite/changes/` in exactly two ways: it is
  merged, or it is closed, which moves the ref to `refs/pyrite/closed/<user>/<n>`.
  Nothing resets a ref. Nothing deletes a ref whose tip is not reachable from
  main.

**A worktree in the operator's repository, judged as a guest.** Pyrite writes
three things into the operator's `.git`: objects, refs under `refs/pyrite/`,
and `.git/worktrees/pyrite-*` while a checkout exists. All three are named as
Pyrite's, none is in the working tree, and removing `refs/pyrite/` and the
worktree entries returns the repository to what it was plus unreachable
objects. A clone would avoid the worktree entries only; the refs and objects
arrive anyway at lease end. This is acceptable on one condition: it happens
only in a KB whose operator set `landing: review`. A repository whose KBs all
land canonical is never written to in this way.

### 3. The pull request as git and files

| | Where it lives |
|---|---|
| The change | The commits on `refs/pyrite/changes/<user>/<n>`, each by its author. |
| Open, or closed without merging | Which namespace the ref is in. |
| Draft or submitted; title and description; a reviewer's request for changes; the checkout lease | A row (`change_set`), declared as a state table (ADR-0029 §4). |
| The outcome of a merge | The merge commit on main: second parent is the change's tip; trailers `Pyrite-Change: <user>/<n>` and `Reviewed-by: <merger>`; the title and description as its message. |
| What a pull request changes | `git diff <merge base>..<tip>`, computed, never stored. |

- Rows are rebuilt from refs. A ref under `refs/pyrite/changes/` with no row
  is listed as a draft whose author is the name in the ref. Deleting the
  database loses the submitted flag and any unanswered feedback, and nothing
  else.
- Nothing about a pull request is written into the KB's tree. No entry, no
  sidecar. `git log --first-parent` on main is the list of merged changes.
- An entry review (`review_service.py`) is unchanged and separate: a verdict
  on one entry at one blob hash.

### 4. Reads while a change is open

- **What you read is what your next write patches.** A single-entry read by
  the author is the blob at the change set's tip, for a changed entry and an
  unchanged one alike. The hash returned with it (ADR-0042 §7) is that
  blob's hash, and a write compares against the same blob (ADR-0042
  decisions 9 and 11).
- Cross-entry reads (list, count, search, tags, links, graph) are the main
  index overlaid by the change set's diff index. The diff index is rebuilt at
  any time from `git diff --name-status <merge base> <tip>`: added and
  modified entries are rows, deleted entries are tombstones. An entry the
  author deleted appears in none of their reads. Semantic search is served
  from main and says so; a changed entry is embedded when it merges.
- A change set is **behind** when main's commit is not its ancestor. That is
  computed from git, not stored. Pyrite brings a behind change set up to date
  by merging main into it when that merge is clean, at the author's next
  write, at submit, and on request. The author's commits stay as they are; the
  update is a merge commit by the Pyrite instance.
- When the update would conflict, nothing is changed and nothing is reset.
  The change set is reported as conflicting, with the entries named. The
  author resolves each by choosing a side and editing. Pyrite never writes
  conflict markers into an entry file.
- Readers other than the author see main only.

### 5. Merge

- `Action.KB_MERGE` on the change set's KB. It is the operator's (ADR-0043
  decision 5). Later it is a capability the operator grants; that is the
  capabilities direction (ADR-0043 decision 6), not this decision.
- The steps, none of which checks anything out:
  1. Refuse unless the canonical checkout is on a branch, with no merge,
     rebase or cherry-pick in progress.
  2. Refuse unless every changed path is an entry file under the change set's
     KB.
  3. `git merge-tree` of main's commit and the tip. A conflict refuses and
     names the entries.
  4. Check the merged tree: every changed entry parses and validates, and no
     id is held by two files (ADR-0038 I1). This is the analogue of the merge
     queue running the checks on the exact commit about to land.
  5. `git commit-tree` with the trailers, then `git merge --ff-only` in the
     canonical checkout.
- What step 5 refuses, and leaves untouched: an uncommitted or untracked file
  in the canonical checkout at a path the change touches, and a main that
  moved since step 3. The refusal names the files. Uncommitted work at other
  paths is not a reason to refuse and is not touched.
- After a merge: the main index is reconciled for exactly the changed paths;
  one event per changed entry goes to the KB's readers; the author is told;
  the change set's ref, diff index and checkout are removed (its commits are
  now reachable from main); other open change sets on that KB become behind.
- A reviewer may instead **request changes** (the row returns to draft with
  the feedback) or **close** (section 2).
- Events about unmerged work reach only its author.

### 6. Other writers of main

- **A human editing the canonical files, or using git in the canonical
  repository, is the operator.** A hand edit, a commit, a pull, a checkout of
  another branch: all are main moving. Pyrite learns of a moved commit by
  comparing against git when it next acts on a change set, and learns of
  changed files by the reconcile (ADR-0038); there is no watcher.
- The operator's CLI and stdio MCP land canonical, uncommitted, as now. Commit
  and push stay the operator's.
- A push from elsewhere into the checked-out branch is refused by git's
  default. Pyrite does not change that setting.
- A ref someone pushes to `refs/pyrite/changes/` with git appears as a draft
  with no Pyrite author. Only the operator sees it. Mapping git identities to
  principals is not decided here.

### 7. Invariants

Each is checkable by a test that drives real git in a temp repository.

- **L1.** For every write entry point on REST, HTTP MCP, stdio MCP and the
  CLI, the file changed is in the place `lands_in(principal, kb)` names.
  Generated from the entry-point inventory (ADR-0037 §5), not hand-written.
- **L2.** A write whose landing place cannot be reached changes no file
  anywhere.
- **L3.** A KB with `landing: canonical` has no change sets, and a write to it
  by any authorised principal changes the canonical file.
- **W1.** After any Pyrite operation, every commit a user ever made is
  reachable from a ref (`git rev-list --all`). Covers merge, request changes,
  close, update, lease expiry, checkout removal, and the directory being
  deleted by hand.
- **W2.** After any write through Pyrite, the change set's working copy has
  no uncommitted change, and the commit's author is the principal.
- **W3.** No Pyrite operation on a change set changes the canonical
  checkout's `HEAD`, index, or any file, except a successful merge.
- **W4.** `git branch` and `git push --all --dry-run` in the operator's
  repository show nothing of Pyrite's.
- **W5.** Every path a change set changes is under its KB's directory.
- **P1.** With the database deleted and rebuilt, every open change set is
  listed, with its author and diff, and every merged one is found from main's
  first-parent history.
- **P2.** A change set id is never reused.
- **R1.** For an author with an open change set, a single-entry read followed
  by a write carrying its hash is not refused as stale unless someone wrote
  that entry in that change set in between.
- **R2.** An entry the author deleted is returned by none of their reads.
- **R3.** No reader other than the author receives an entry's unmerged
  content, by read or by event.
- **M1.** A merge leaves every uncommitted or untracked file in the canonical
  checkout byte-identical, or is refused having changed nothing.
- **M2.** A merge never switches the canonical checkout's branch, and works
  for any branch name.
- **M3.** A refused merge, for any reason, leaves the change set open and its
  ref unmoved.
- **M4.** After a successful merge, the main index rows of the changed entries
  match the files with no further sync, and one event per changed entry was
  sent.
- **M5.** No entry file ever contains a conflict marker written by Pyrite.

## Acceptance

Doc-driven. The passage below is `docs/proposing-and-reviewing-changes.md`;
`tests/test_doc_internal_pull_requests.py` runs it. A block headed `as alice`
is sent with alice's credential over HTTP MCP; `as operator` is the CLI on the
server; `git` runs in the canonical repository and its output must match.
The git blocks are the contract. The spellings of Pyrite's own commands and
tools are illustrative: the interfaces are alpha and follow the CLI contract
(#303).

Setup: a repository on branch `trunk` holding the KB `field-notes`; the
operator has set `landing: review` on it and given alice a write grant. The
entry `jacob` says `species: monkey`.

**1. Alice proposes a change.**

```as alice
kb_update {"kb": "field-notes", "id": "jacob", "set": {"species": "rabbit"}}
```
```result
{"updated": "jacob", "landed": {"in": "change", "id": "alice/1"}}
```
```git
$ git status --porcelain
$ git branch --format='%(refname:short)'
trunk
$ git log -1 --format='%an: %s' refs/pyrite/changes/alice/1
alice: Update jacob
$ git diff --name-status trunk refs/pyrite/changes/alice/1
M	field-notes/jacob.md
```

Alice reads her own change. Everyone else reads main.

```as alice
kb_get {"kb": "field-notes", "id": "jacob", "fields": ["species"]}
```
```result
{"species": "rabbit"}
```
```as operator
$ pyrite get jacob -k field-notes --field species
monkey
```

**2. Alice submits it.**

```as alice
change_submit {"id": "alice/1", "title": "Jacob is a rabbit"}
```
```as operator
$ pyrite change list -k field-notes
alice/1  submitted  alice  1 entry  Jacob is a rabbit
$ pyrite change diff alice/1
-species: monkey
+species: rabbit
```

**3. The operator is in the middle of something, and merges anyway.** A hand
edit to another entry is sitting uncommitted in the canonical checkout.

```as operator
$ echo "a note to self" >> field-notes/todo.md
$ pyrite change merge alice/1
merged alice/1 into trunk (1 entry). 1 uncommitted file left as it was.
```
```git
$ git status --porcelain
 M field-notes/todo.md
$ git log -1 --first-parent --format='%s | %(trailers:key=Pyrite-Change,valueonly,separator=%x2C) | %(trailers:key=Reviewed-by,valueonly,separator=%x2C)'
Jacob is a rabbit | alice/1 | operator
$ git for-each-ref refs/pyrite
```
```as operator
$ pyrite get jacob -k field-notes --field species
rabbit
```

**4. A merge that would overwrite the operator's work is refused.** Alice
opens `alice/2` changing `todo`; the operator's edit to `todo.md` is still
uncommitted.

```as operator
$ pyrite change merge alice/2
refused: field-notes/todo.md has uncommitted changes in the main KB.
Commit or discard them, then merge. Nothing was changed.
```
```git
$ git status --porcelain
 M field-notes/todo.md
$ git for-each-ref --format='%(refname)' refs/pyrite
refs/pyrite/changes/alice/2
```

**5. Closing a change keeps it.**

```as operator
$ pyrite change close alice/2 --reason "superseded"
closed alice/2. Its commits are kept at refs/pyrite/closed/alice/2.
```

The document's other examples: main moves under an open change and Alice's
next read and write agree; two changes to the same entry, where the second is
reported as conflicting and nothing is reset; a second KB in the same
repository gets its own change; a KB with `landing: canonical` takes Alice's
write straight to the file; the same four steps on a repository whose branch
is `main`.

## The path there

| Step | What | Needs first |
|---|---|---|
| 0 | **Routing off** (decided 2026-10-02): `lands_in` exists and returns canonical; the four REST routes stop routing; the worktree endpoints answer "not enabled". Any existing `user/*` branch is merged or kept as a ref first. | — |
| 1 | **One seam, a correct working copy** (the architect's option C, in this ADR's terms). `landing` in the config file (ADR-0039); the write-target factory under every surface; a change set as a ref under `refs/pyrite/changes/`; a commit per write; the merge of section 5; close instead of reset. Invariants L1 to L3, W1 to W5, M1 to M3, M5, P2. Behind `landing: review`, off by default. | The entry-point inventory (`tests/_surface_inventory.py`, landed with ADR-0037 theme 0), which L1 is generated from. ADR-0039 (a KB listed in the file has its `landing`). |
| 2 | **Reads.** Blob-at-tip single reads, tombstones, every read family, the update of a behind change set, author-only events. R1 to R3, M4. | ADR-0042 decisions 9 to 11 (a single-entry read reads the file; its hash; writes return the new hash). ADR-0038 step 3 (sticky location): on `2e5d72e7` a one-field update moved the file into `notes/`, so a change's diff was a delete and an add. |
| 3 | **Rows from refs, and the doc.** P1; the walkthrough above as the running test; the web changes and merge-queue pages rewritten on the new endpoints. | Steps 1 and 2. |
| 4 | **Delegated merge.** `KB_MERGE` as a granted capability. | ADR-0043 decision 6 (master file, user-owned file). |
| later | Changes offered by git from outside; federation (ADR-0018). | Identity mapping. |

**Deleted from today's code** at step 1: `server/worktree_resolver.py` and its
four call sites in `entries.py`; `WorktreeService.reset_to_main`, `merge` and
the `(user, kb)` row reuse; `GitService.merge_branch` (the only caller of
`git checkout` in a user's repository); the literal `main`; the
`.pyrite-worktrees` directory beside the repository; the diff index and its
`.gitignore` inside the worktree; `app.state.pyrite_diff_db_cache`; the
`worktree` table (replaced by `change_set`); the `/worktree/*` and
`/admin/merge-queue/*` routes in their present shape. Kept:
`OverlaySearchBackend` (extended at step 2), `GitService.worktree_add`,
`worktree_remove`, `diff_branches`.

### What becomes of ADR-0024

| ADR-0024 | Disposition |
|---|---|
| Git worktrees as the mechanism; no GitHub dependency; shared object store | **Kept.** |
| An in-app review queue; users do not see each other's unmerged work; attribution through commits | **Kept.** |
| Branch `user/{name}`, one per user | **Superseded** by a change set per piece of work under `refs/pyrite/changes/`. |
| "On first edit a worktree is created; all subsequent writes go to it" | **Superseded** by `lands_in`, per KB, on every surface. |
| "Index per worktree" | **Superseded** by the diff index (as ADR-0029 §6 already said). |
| Submit sets a timestamp in the DB | **Kept** as the row; the ref is the change. |
| Merge by checkout in the main repository; "rebase user branch" after | **Superseded** by section 5. A merged change set ends; nothing is rebased. |
| "If the rebase fails, the worktree is reset to main" | **Withdrawn.** Nothing is reset. |
| Permissions V1 (all KBs readable, only admins merge, no per-KB permissions) | **Superseded** by ADR-0037's policy and `KB_MERGE`. |
| Worktree GC for inactive users | **Superseded**: the checkout is reaped, the change is not. |
| GitHub push or PR as a later export | **Kept** as later. |

**ADR-0029 §6 is amended in one sentence.** "A user worktree is an **ephemeral
KB leased to a user session** ... **promote** = admin merge ..., **reap** =
worktree GC ..., **reset** = explicit discard" becomes: the leased ephemeral is
the **working copy**, not the change. Its "promote" is the merge, its "reap"
removes a checkout, and its "reset" is replaced by close. The same section's
"Coordination KBs and runtime state are exempt from worktree routing" is
carried by `landing: canonical`, which is the attribute that marks a
coordination KB.

## Consequences

**Easier**
- Where a write lands is one question with one owner, and a test generated
  from the entry-point inventory answers it for every surface.
- The operator's repository looks the same to the operator: same branch, same
  uncommitted work, no extra branches, nothing extra pushed.
- A merged change is visible to any git tool as a merge commit with its
  author's commits behind it. A proposed one is visible as a ref.
- Deleting the database loses no work and no outcome.
- A session that ends, crashes or is reaped leaves nothing to recover.
- The unit matches Pyrite's own flow, so the conductor's vocabulary (branch,
  pull request, behind, update, merge) applies to KB work unchanged.

**Harder**
- git 2.38 or later is required for a KB with `landing: review`.
- One commit per write makes long histories on a change set. The merge commit
  is the unit a reader of main sees (`--first-parent`).
- Main's history gains merge commits. ADR-0032's linear-history rule is for
  Pyrite's own repository and is not applied to KBs.
- A change set is confined to one KB. A change that must touch two KBs is two
  pull requests.
- The operator's uncommitted canonical writes are ahead of every change set:
  an author's branch is cut from the commit, not from the files. A merge that
  meets them is refused per file (section 5), so a reviewed KB works best when
  the operator commits.
- Cross-entry reads can be fresher than the author's base while a change set
  is behind and conflicting. Single-entry reads are not, which is the
  invariant that matters for writes.
- `refs/pyrite/closed/` grows. Pruning it is an explicit operator command.
- Two code paths produce commits: per-write commits in a working copy, and
  the merge commit by plumbing.

## Alternatives considered

- **Keep the per-user branch and repair it** (rebase after merge, preserve the
  tip before reset). Rejected: one branch per user cannot hold a rejected
  change, a merged one and new work at once, and two sessions of one user
  share a checkout.
- **A clone per change.** Rejected for now: it writes the same refs into the
  operator's repository by lease end, and adds a fetch per movement of main.
  It is the right shape for federation and is left to ADR-0018's successor.
- **No checkout: write the branch with plumbing.** Rejected: a second write
  path.
- **Change-set refs under `refs/heads`.** Rejected: measured, `git push --all`
  publishes them, and they appear in the operator's `git branch`.
- **The pull request as an entry in a coordination KB.** Rejected: readable
  by every reader of that KB, published by a push, rendered by a site
  generator, and it records in a file something that is true only until the
  merge.
- **Review state in git notes or a metadata ref.** Deferred. It would make
  the submitted flag and the feedback survive a database loss, at the cost of
  a format only Pyrite reads. Question 3.
- **Squash on merge.** Rejected as the default: the author's commits would be
  reachable only from an archive ref, and attribution on main would be one
  line.
- **`git update-ref` alone to advance main.** Rejected: measured, it leaves
  the checked-out index and files behind the ref. `git merge --ff-only` in the
  checkout moves all three or none.
- **A change set that spans every KB in the repository.** Rejected: a merge
  would need authority over each KB touched, and one diff index would serve
  several KBs.
- **Auto-commit the operator's canonical writes** so main's commit always
  equals its files. Not decided here: commit is the operator's.

## Questions for the maintainer

None blocks the current state (routing off, every write canonical). Questions 1, 2 and 8 shape step 1; 3 to 7 and 9 to 11 are settled by the build or later.

1. **Is a change set per piece of work, or per user?** Your words were "their
   worktree". This ADR reads that as: each user has a current change set per
   KB, opened on first write, so the default behaviour is one working copy per
   user; an agent or a second task names another. *Recommended: per change
   set, with a default one per user and KB.*
2. **`refs/pyrite/` or ordinary branches?** Ordinary branches are easier to
   see with `git branch` and are pushed by `git push --all`. *Recommended:
   `refs/pyrite/`, because unreviewed work should not leave the machine by an
   ordinary push.*
3. **Is a row enough for "submitted" and for a reviewer's feedback?** If the
   database is lost they are lost and every open change reappears as a draft.
   *Recommended: yes, a row. Revisit if review discussion becomes something
   people want to keep.*
4. **Merge commit, or squash?** *Recommended: merge commit with trailers.*
5. **May the operator's uncommitted edits block a merge per file, as
   measured?** The alternative is to require a clean checkout, which refuses
   far more often. *Recommended: per file.*
6. **Is a worktree entry under the operator's `.git` acceptable for a guest**,
   given it appears only for a KB the operator set to `landing: review`?
   *Recommended: yes. A clone buys little, as measured.*
7. **Working-copy commits skip the repository's hooks?** The operator's
   `pre-commit` would otherwise run on every user write. *Recommended: skip;
   Pyrite's own validation is the check, and the operator's hooks run when the
   operator commits.*
8. **One KB per change set?** *Recommended: yes.*
9. **Idle drafts.** Never closed automatically, or closed (ref kept) after a
    period the operator sets? *Recommended: never automatically; the operator
    lists and closes.*
10. **May the operator propose a change to a reviewed KB instead of landing
    canonical**, for example to have an agent's work reviewed? *Recommended:
    yes, by naming a change set explicitly; the default stays canonical.*
11. **Sparse checkouts.** Eleven times faster and a tenth of the disk on the
    measured repository, for one line in the operator's `.git/config`.
    *Recommended: not until a real KB needs it.*

---

<small>Evidence: the spike's experiments (merge without checkout; working copy,
lease end, push and update; costs on 20,000 files; reads through the real
`WorktreeResolver`; a hooks check), kept in the spike's working directory and
not in the repository. git 2.39.5, macOS. The architect's probe P2 to P9 was
not re-run; the files it exercises are byte-identical between `d35eae77` and
`ed71b8b0`.</small>
