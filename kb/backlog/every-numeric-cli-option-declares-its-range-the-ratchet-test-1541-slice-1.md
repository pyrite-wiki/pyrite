---
id: every-numeric-cli-option-declares-its-range-the-ratchet-test-1541-slice-1
title: "Every numeric CLI option declares its range: the ratchet test (#1541 slice 1)"
type: backlog_item
tags:
- quality
importance: 5
kind: improvement
status: done
priority: medium
assignee: agent:cloud-worker
effort: S
rank: 0
---

Slice 1 of https://github.com/pyrite-wiki/pyrite/issues/1541: a structural test walks every registered command and fails for an int or float option or argument with no min or max, unless it is on a reviewed UNBOUNDED list or the seeded NOT_YET list (pinned size, may only shrink). No CLI option changes in this slice.
