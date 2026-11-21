---
id: bug-worktree-diff-index-committed-into-user-branch
type: backlog_item
title: "BUG: worktree diff-index SQLite committed into user's branch on submit"
kind: bug
status: done
priority: high
effort: XS
tags: [bug, worktree, multi-user, git, data-corruption, adr-0024]
---

## Problem

`WorktreeService.ensure_worktree` created the diff-index database at
`<worktree>/.pyrite/diff-index.db` (inside the worktree directory) but did
not exclude that path from git. A subsequent `git add .` on submit
committed the SQLite binary (and its WAL/SHM siblings) into the user's
branch, which would then merge into the canonical KB on accept.

## Impact

End-to-end data-corruption risk on the multi-user collaboration path
([ADR-0024](../../adrs/0024-worktree-collaboration-model.md)): a per-user diff
index — meant to be ephemeral, per-worktree, and machine-local — would have
shipped into the KB's main branch and propagated to every fork on the next
sync. The binary contents include the user's draft entries; merging them
through git is incoherent (binary diff) and a privacy regression.

Surfaced when exercising the worktree write path end to end during the
review-flow integration.

## Root cause

`pyrite/services/worktree_service.py:201` created the diff DB without
writing any local exclusion. The `.pyrite/` directory had no `.gitignore`
and `.git/info/exclude` wasn't being touched either — a self-contained
worktree directory needs a self-contained exclusion.

## Fix

Write a `.gitignore: *` inside `.pyrite/` at worktree creation so the
directory self-excludes regardless of how submit is implemented.
Implementation in commit `9e13966`:

```python
diff_db_path.parent.mkdir(parents=True, exist_ok=True)
# The diff index lives inside the worktree (.pyrite/), so self-exclude it
# from git — otherwise `git add .` on submit commits the SQLite binaries
# into the user's branch and they'd merge into the KB.
(diff_db_path.parent / ".gitignore").write_text("*\n")
```

`.pyrite/` excluding everything is correct: nothing in there should ever be
versioned. Self-contained per-worktree exclusion means a future
worktree-bootstrap change (different submit logic, a new file in `.pyrite/`)
inherits the safe default.

## Related

- `bug-worktree-first-write-fails-foreign-key-constraint` — companion fix
  in the same commit; the FK bug would have hit *before* the gitignore bug
  could fire in practice, but both needed fixing for the write path to work.
- `epic-fork-system` — closed; this is one of the rough edges that
  surfaced during deferred-work exploration.
- Fix commit: `9e13966`.
