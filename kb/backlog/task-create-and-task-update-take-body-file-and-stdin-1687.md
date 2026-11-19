---
id: task-create-and-task-update-take-body-file-and-stdin-1687
title: task create and task update take --body-file and --stdin (#1687)
type: backlog_item
tags:
- cli
importance: 5
kind: feature
status: in_progress
priority: medium
assignee: agent:cloud-worker
effort: S
rank: 0
---

Add --body-file <path> (and - for stdin) and --stdin to task create and task update, plus --body on update, reusing the one body reader from pyrite create. Two body sources are refused (exit 2); a file or stdin body that opens with frontmatter is refused with a pointer to pyrite create -t task --body-file (option C of the groom). Spec: issue #1687 groom 2026-11-17.
