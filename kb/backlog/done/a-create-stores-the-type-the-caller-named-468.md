---
id: a-create-stores-the-type-the-caller-named-468
title: A create stores the type the caller named (#468)
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

Goal state: on every create surface, the entry type stored is the type the caller named, unless a plugin has *declared* that the core type maps to one of its own. Nothing is inferred from subclassing. One resolver answers for the write path and for orient/kb_schema. Today `pyrite create -t note --allow-undeclared` in a `kb_type: software` KB writes `adrs/plain-note.md` with `type: adr`; a REST daily note there becomes an ADR.

Issue: https://github.com/pyrite-wiki/pyrite/issues/468
