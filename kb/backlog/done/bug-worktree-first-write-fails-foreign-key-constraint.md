---
id: bug-worktree-first-write-fails-foreign-key-constraint
type: backlog_item
title: "BUG: first overlay write into a fresh worktree fails with SQLite FOREIGN KEY error"
kind: bug
status: done
priority: high
effort: XS
tags: [bug, worktree, multi-user, sqlite, foreign-key, adr-0024]
---

## Problem

`WorktreeService.ensure_worktree` pre-created the diff-index database at
`<worktree>/.pyrite/diff-index.db` but **never registered the KB inside it**.
The first overlay entry-write into a fresh worktree then failed with:

```
sqlite3.IntegrityError: FOREIGN KEY constraint failed
```

…because the `entry` table's `kb_name -> kb.name` foreign key had no
matching row to point at.

## Impact

Hard-blocker on the multi-user write path ([ADR-0024](../../adrs/0024-worktree-collaboration-model.md)):
every brand-new worktree was unusable until the user got the magic SQL run
against the diff DB. There was no helpful error, just the raw SQLite
constraint message bubbling up — the user saw "save failed" with no actionable
detail.

Surfaced when exercising the worktree write path end to end during the
review-flow integration.

## Root cause

`pyrite/services/worktree_service.py:201` did:

```python
_diff_db = PyriteDB(diff_db_path)
_diff_db.close()
```

…which initialized the schema but inserted no KB row. The overlay backend
then did the obvious thing on first write and was correctly rejected by the
FK constraint.

## Fix

Register the KB in the diff DB at worktree creation (pointing at the
worktree path, with the same kb_type/description as the source KB) so the
FK is satisfied for every subsequent write. Implementation in commit
`9e13966`:

```python
_diff_db = PyriteDB(diff_db_path)
_diff_db.register_kb(
    name=kb_name,
    kb_type=kb_config.kb_type,
    path=str(worktree_path),
    description=kb_config.description,
)
_diff_db.close()
```

## Related

- `bug-worktree-diff-index-committed-into-user-branch` — companion fix in
  the same commit. The two bugs sit immediately adjacent in the worktree
  bootstrap path; both had to be fixed for the write path to be usable
  end to end.
- `epic-fork-system` — closed epic; this is one of the rough edges that
  surfaced during deferred-work exploration.
- Fix commit: `9e13966`.
