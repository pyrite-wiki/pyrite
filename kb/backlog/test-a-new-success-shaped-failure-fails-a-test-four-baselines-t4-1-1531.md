---
id: test-a-new-success-shaped-failure-fails-a-test-four-baselines-t4-1-1531
title: "test: a new success-shaped failure fails a test; four baselines (T4-1, #1531)"
type: backlog_item
tags:
- quality
importance: 5
kind: improvement
status: in_progress
priority: medium
assignee: agent:cloud-worker
effort: S
rank: 0
---

Adds tests/test_a_failure_is_never_a_success.py with per-file shrink-only baselines for HTTPException sites, error dicts without error_code, unknown code literals and broad excepts, plus a classification table for the broad handlers. Tests only; later T4 slices lower the baselines.
