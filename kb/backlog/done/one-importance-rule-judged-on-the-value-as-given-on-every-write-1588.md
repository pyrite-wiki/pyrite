---
id: one-importance-rule-judged-on-the-value-as-given-on-every-write-1588
title: One importance rule, judged on the value as given, on every write (#1588)
type: backlog_item
tags:
- bug
importance: 5
kind: bug
status: in_progress
priority: high
assignee: agent:cloud-worker
effort: M
rank: 0
---

A create, add, bulk create and update of importance either store a whole number from 1 to 10 as it was sent or refuse it with the same errors row, because the model keeps the value as given long enough for Entry.validation_errors to judge it on every path and validate_importance is tightened. A value already in a file, at the top level or under metadata:, is judged by the same predicate and only reported.
