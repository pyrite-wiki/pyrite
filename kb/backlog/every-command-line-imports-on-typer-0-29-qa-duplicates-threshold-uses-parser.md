---
id: every-command-line-imports-on-typer-0-29-qa-duplicates-threshold-uses-parser
title: "Every command line imports on Typer 0.29: qa duplicates --threshold uses parser= (#1597)"
type: backlog_item
tags:
- bug
importance: 5
kind: bug
status: in_progress
priority: high
assignee: agent:cloud-worker
effort: S
rank: 0
---

qa duplicates --threshold is declared with a parser function instead of click_type=, a keyword Typer 0.29 removed, so pyrite, pyrite-read and pyrite-admin import on every Typer version pyproject.toml allows. The refusals (out of range, non-finite, not a number) stay usage errors. A structural test refuses the keyword anywhere under pyrite/ and extensions/.
