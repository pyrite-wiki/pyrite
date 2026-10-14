---
id: the-pre-push-hook-examines-only-the-first-ref-of-a-multi-ref-push
title: The pre-push hook examines only the first ref of a multi-ref push
type: backlog_item
tags:
- process
- testing
- hooks
importance: 5
kind: tech_debt
status: proposed
priority: low
effort: S
rank: 0
---

## Problem

`git push origin a b` runs the pre-push hook once, and pre-commit's shim
(`pre_commit/commands/hook_impl.py`, `_pre_push_ns`, 4.7.0) returns on the
FIRST pushed ref that has something to push. `scripts/test-affected` is handed
that ref's range (`PRE_COMMIT_FROM_REF` / `PRE_COMMIT_TO_REF`) and never learns
the others exist: the commits on `b` reach the remote with no local test run.

This predates the verified-commit skip (PR #854) and is not changed by it, but
the skip makes one shape of it starker: `git push origin <verified-sha>:x
unverified-branch` now runs nothing, where before it ran the tests for the
first ref's range (which were not the second ref's either).

Found while enumerating what each kind of push does for #854.

## Why it is not fixed there

The hook entry cannot see the ref list: pre-commit consumes the hook's stdin
and exports one range. Closing it means leaving pre-commit for the pre-push
stage (a hand-written `pre-push` that loops over stdin and calls
`scripts/test-affected` per ref), which changes the hook layout
`tests/test_dev_process_config.py` pins. That is a decision, not a patch.

## Acceptance

- A push of several refs runs the selection for every ref that has something
  to push, or refuses a multi-ref push with a message saying to push them one
  at a time. Either way a test in a temporary repository pushes two refs and
  shows the second is not let through untested.
- The single-ref path, the pass stamps and the verified-commit skip behave as
  they do today (`tests/test_test_affected_stamps.py`,
  `tests/test_test_affected_verified.py`).

## Size of the risk

Small: the local run is feedback, not the gate (ADR-0032). CI on the pull
request, the merge queue and the push to `dev` all test what a multi-ref push
let through. It is worth a line because the hook's documentation says what a
push runs, and for the second ref of a push that is not true.
