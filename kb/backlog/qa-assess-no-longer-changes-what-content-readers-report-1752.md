---
id: qa-assess-no-longer-changes-what-content-readers-report-1752
title: qa assess no longer changes what content readers report (#1752)
type: backlog_item
tags:
- qa
importance: 5
kind: bug
status: in_progress
priority: medium
assignee: agent:cloud-worker
effort: M
rank: 0
---

qa assess writes qa_assessment entries into the KB; validate, status, gaps and export site count them as content. Option A (maintainer, 2026-11-18): records stay entries; one shared predicate (constant + SQL clause builder in the QA service layer) decides what a QA record is and every content reader goes through it. Property: qa assess changes nothing validate/status/gaps/export site report for the same content entries.
