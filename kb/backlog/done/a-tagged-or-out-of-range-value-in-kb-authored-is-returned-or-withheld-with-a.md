---
id: a-tagged-or-out-of-range-value-in-kb-authored-is-returned-or-withheld-with-a
title: A tagged or out-of-range value in kb_authored is returned or withheld with a true reason (#1568)
type: backlog_item
tags:
- bug
importance: 5
kind: bug
status: in_progress
priority: medium
assignee: agent:cloud-worker
effort: S
rank: 0
---

orient's kb_authored now returns a !!str-tagged value as the string, withholds any other explicit YAML tag (scalar, mapping or list) under one new reason that does not print the tag, and names a 1e400-style overflow as too large for JSON instead of NaN or infinite. The grid test gains the tagged and overflow shapes, asserts no shape gets the catch-all reason, and skips only when the KB's own loader fails.
