---
id: a-cancelled-child-does-not-count-toward-its-parent-s-completion-1683
title: A cancelled child does not count toward its parent's completion (#1683)
type: backlog_item
tags:
- bug
importance: 5
kind: bug
status: done
priority: medium
assignee: agent:cloud-worker
effort: S
rank: 0
---

A task whose remaining children are all cancelled reads complete by its children today, so it drops out of task list --status open and a claim on it is refused. Option A (maintainer, 2026-11-17): a parent is complete by its children only when at least one child is done and every other child is done or cancelled, decided in derive_completion().
