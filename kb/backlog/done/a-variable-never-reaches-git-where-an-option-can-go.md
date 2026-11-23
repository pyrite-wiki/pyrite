---
id: a-variable-never-reaches-git-where-an-option-can-go
title: A variable never reaches git where an option can go
type: backlog_item
tags:
- security
importance: 5
kind: bug
status: done
priority: medium
effort: S
rank: 0
---

Every git argv Pyrite builds passes each variable value after -- or --end-of-options, as the value of an option that takes one, or after a same-function validation refusing a leading dash. A structural AST test enforces it. First site: document_manager git rm.
