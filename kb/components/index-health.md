---
id: index-health
title: Index Health
type: component
tags:
- core
- services
importance: 5
kind: module
path: pyrite/services/index_health.py
owner: core
---

One verdict over an index health report. `pyrite index health`, `pyrite-admin index health` and QA's `index_out_of_date` gate all read `IndexManager.check_health`; none of them decides for itself whether the report is healthy. Before this module the admin command ignored `duplicates`, `content_changed` and error-severity `off_list_values`, and QA ignored `orphaned_entries`, so one report read unhealthy on one surface and healthy on another (#1119, #1147).

`health_verdict(health)` (`pyrite/services/index_health.py:13`) returns `status` (`healthy`, `warning` or `unhealthy`), `is_unhealthy` and `has_warning`. Unhealthy means search or a read would answer from something other than the files: missing, unindexed, stale or content-changed files, duplicate ids ([[adr-0038]] decided question 3), unreadable files, orphaned entries, error-severity off-list values. A warning names a problem that does not stop the index serving what it holds: broken links, schema errors, malformed or unmodellable frontmatter (see [[file-operations]]), `double_spellings` ([[adr-0051]]), links whose KB a rebuild would change. The two sets are the code at :29-:58; add a new report key there and nowhere else.

## Public functions (line numbers on this branch)

- `health_verdict(health)` — `pyrite/services/index_health.py:13`

## Who calls it

- `pyrite index health` (`pyrite/cli/index_commands.py:526`)
- `pyrite-admin index health` (`pyrite/admin_cli.py:322`)
- `QAService` `index_out_of_date` (`pyrite/services/qa_service.py:255`)

The report itself is built by `IndexManager.check_health` (`pyrite/storage/index.py:1361`); this module only reads it.

## Related

- [[index-manager]] — builds the report
- [[adr-0038]]
