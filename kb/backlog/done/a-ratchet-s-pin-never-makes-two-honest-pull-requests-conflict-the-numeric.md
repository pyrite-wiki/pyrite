---
id: a-ratchet-s-pin-never-makes-two-honest-pull-requests-conflict-the-numeric
title: "A ratchet's pin never makes two honest pull requests conflict: the numeric-option list (#1608, slice 1)"
type: backlog_item
tags:
- quality
importance: 5
kind: improvement
status: in_progress
priority: high
assignee: agent:cloud-worker
effort: S
rank: 0
---

Drops NOT_YET_PINNED_SIZE so the numeric-option list is its own pin, and adds scripts/check_ratchets_only_shrink.py, a CI range check that fails when a registered list gains a row unless a commit in the range carries a Ratchet-raise trailer. Two pull requests that remove different rows then merge without touching the same line.
