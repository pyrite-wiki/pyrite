---
id: map-collaboration-and-review-of-changes
title: "Where does a user's write land? Topic map: worktrees, change sets, internal pull requests and the merge queue"
type: note
tags:
- map
- design
- collaboration
- multi-user
- worktree
- pull-request
- merge-queue
- review
---

# Where does a user's write land?

Answers "where does a user's write land?", "how does a change reach main?",
"what became of worktree routing?". Layer 2 under [[design]] (P7, P8).
Multi-user is experimental; the maintainer decided on 2026-10-02 that, for
now, every authorised write lands on the canonical files and worktree routing
is off. That decision is not yet in an accepted ADR or in the code.

## The design today

1. For now every authorised write lands on the canonical files. **decided**
   (maintainer, 2026-10-02; recorded in ADR-0044, proposed). The code still
   routes non-admin REST entry writes to a per-user worktree
   ([[adr-audit-2026-10]]).
2. Worktree per user, branch `user/{name}`, "Submit changes", an in-app admin
   merge queue, no GitHub dependency. **decided, built, contradicted**
   [[adr-0024]]; its permissions section is contradicted by [[adr-0037]].
3. Fork per user, GitHub PRs, org hierarchy: kept only as the future
   cross-instance design. **decided** [[adr-0018]], superseded in part.
4. A user worktree is an ephemeral KB leased to a user session; promote is the
   admin merge; the diff index is an overlay; coordination KBs and runtime
   state are exempt from routing. **decided, direction only** [[adr-0029]]
   section 6; the lease reaper does not exist.
5. Invariant: no user commit is ever unreachable after a merge, reject or
   reset. **decided** [[adr-0029]] section 6 (tracked as
   `worktree-no-lost-commits-invariant`).
6. One landing rule, a function of principal and KB: `canonical` or the user's
   change set, by a per-KB `landing` field in the operator's file. **proposed**
   ADR-0044 section 1.
7. A change set is a git ref under `refs/pyrite/changes/`, one KB, one commit
   per write; an internal pull request is a submitted change set. The ref and
   the merge commit are the record; the database holds only queue state.
   **proposed** ADR-0044 sections 2 and 3.
8. A merge is `merge-tree` plus a fast-forward in the canonical checkout, which
   is never switched or reset; a refused merge changes nothing. **proposed**
   ADR-0044 section 5.
9. The author reads their own change set's blob and a diff-index overlay;
   everyone else reads main. **proposed** ADR-0044 section 4.
10. Merge is the operator's action. **proposed** ADR-0043 decision 5.
11. Pyrite's own flow is the model: a branch per batch of work, a PR, checks on
    the commit about to land, a queue that refuses rather than rebases for you.
    **decided** [[adr-0032]]; ADR-0044 mirrors it.

## Invariants a test could check

- For every write entry point, the file changed is where `lands_in(principal,
  kb)` says (L1); a write whose landing place is unreachable changes nothing
  anywhere (L2) (ADR-0044).
- W1 every commit a user made is reachable from a ref after any operation; W3 no
  operation on a change set changes the canonical checkout except a successful
  merge; M3 a refused merge leaves the change set open (ADR-0044).
- No reader other than the author receives unmerged content, by read or event
  (R3).
- No user commit is ever unreachable ([[adr-0029]] section 6).

## ADRs in reading order

ADR-0044 (proposed; the end state, with a table of what becomes of
[[adr-0024]]), [[adr-0024]] (built; read its audit standing), [[adr-0029]]
section 6, [[adr-0032]] for the model it copies. History only: [[adr-0018]].

## Where the code starts

Components [[collaboration-services]], [[git-service]], [[repo-service]].
Paths: `pyrite/services/worktree_service.py`, `pyrite/server/worktree_resolver.py`
(called by `pyrite/server/endpoints/entries.py`),
`pyrite/server/endpoints/worktree.py`,
`pyrite/storage/backends/overlay_backend.py`.

## Tests that pin it

`tests/test_overlay_backend.py`. The ADRs name no test for the merge queue.
ADR-0044 proposes `tests/test_doc_internal_pull_requests.py` and a git-driven
invariant set (L, W, P, R, M); none exists.

## Known gaps

- Routing is switched off by decision, not in code; ADR-0044 step 0 does it.
- ADR-0044's probes found ordinary failures in today's worktree: a second KB
  in one repo errors, a reset can leave a commit reachable from no ref, a
  merge switches the operator's branch, `main` is a literal, the overlay has no
  delete tombstone and nothing is committed until submit.
- Worktree GC is unbuilt; "concurrent edits ... admin resolves" is open
  ([[adr-audit-2026-10]]).
- Not decided: per change set or per user, `refs/pyrite/` or branches, merge
  commit or squash (ADR-0044 questions 1 to 4); git-identity mapping for
  changes pushed from outside.
