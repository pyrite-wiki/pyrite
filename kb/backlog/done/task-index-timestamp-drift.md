---
id: task-index-timestamp-drift
title: "BUG: `updated_at` / `created_at` fields null in task index for many tasks; breaks 'done today' queries"
type: backlog_item
tags: [task-system, bug, index, timestamps, conductor-workflow, query]
importance: 5
kind: bug
status: done
priority: high
effort: S
rank: 1100
---

## Problem

`pyrite task list --format json` returns task records where `updated_at` and `created_at` are frequently `null` or empty strings, even for tasks that have been recently created or transitioned.

Documented session friction (2026-06-02): the investigation-conductor's tick-9 backlog snapshot tried to compute "done today / opened today" via:

```python
today = '2026-06-02'
done_today = [t for t in tasks if t.get('status')=='done' and (t.get('updated_at','') or '').startswith(today)]
open_today = [t for t in open_tasks if (t.get('created_at','') or '').startswith(today)]
```

Result: `DONE TODAY: 0 / OPENED TODAY: 0` — when in fact ~25-30 tasks had been transitioned to done that session and ~50-60 had been opened. The index `updated_at` / `created_at` fields were either null or not being populated on transitions.

Git history shows the correct dates (commits are timestamped accurately). The bug is in the index layer, not the data layer.

This affects multiple workflow patterns:
- Conductor session-summary queries
- Velocity tracking ("how many tasks did this session close?")
- Grooming staleness detection (relies on timestamp truth)
- Any user-side "show me today's work" query

## Reproduction (suggested)

1. Create a task via `pyrite task create`
2. `pyrite task list --format json | jq '.tasks[] | select(.id == "<just-created-id>") | .created_at'`
3. Observe whether the field is populated and matches the actual creation time

Then walk a task through states:
1. `pyrite task update <id> --status in_progress`
2. `pyrite task list --format json | jq '.tasks[] | select(.id == "<id>") | .updated_at'`
3. Observe whether updated_at reflects the transition

## Suspected sources

- Index-sync may not be updating these fields on task transitions
- The fields may exist in the schema but not be wired into all write paths
- The fields may rely on git-commit-time which doesn't propagate to the index until index-sync runs

## Proposed fix

1. Identify whether the issue is at write-time (task update doesn't set updated_at) or at index-sync time (index doesn't pull the timestamps)
2. Ensure both `created_at` and `updated_at` are populated on every task create / update operation
3. If git-commit-time is the canonical source, ensure index-sync reads it consistently
4. Add a `pyrite ci` validation check for null timestamps as a regression-prevention measure

## Related

- `task-list-age-filter.md` — depends on reliable timestamps to be usable
- `task-update-comment-flag.md` — proposed `status_change_log` should populate timestamps reliably too

## Effort

S — likely a small fix once the root cause is identified. Could be larger if it turns out the timestamp story across schema / git / index needs broader redesign.

## Empirical reproduction confirmed (2026-06-11)

Reproduced cleanly during the Tier A audit loop pass:

- `pyrite get bug-create-silently-accepts-undeclared-types-...` JSON
  output contains no `rank` field at all (not even `null`) — the field is
  fully omitted from the projection.
- `pyrite sw backlog` shows `updated_at: None` for **all 525 backlog
  items**, including tickets touched seconds ago by `pyrite update`.
  Whatever indexing happens on update does not propagate to
  `updated_at`.

High-confidence bug; the fix is unblocked.
