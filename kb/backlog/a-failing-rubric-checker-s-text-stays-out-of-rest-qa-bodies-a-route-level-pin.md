---
id: a-failing-rubric-checker-s-text-stays-out-of-rest-qa-bodies-a-route-level-pin
title: "A failing rubric checker's text stays out of REST QA bodies: a route-level pin"
type: backlog_item
tags:
- quality
importance: 5
kind: chore
status: done
priority: medium
assignee: agent:cloud-worker
effort: S
rank: 0
---

The REST QA validate routes answer a checker that raises with Pyrite's fixed text. The property holds; it is the one QA text field with no end-to-end REST test. Add the pin to tests/test_rest_bodies_carry_no_fault_text.py, parametrised over what the checker raises and over both routes.
