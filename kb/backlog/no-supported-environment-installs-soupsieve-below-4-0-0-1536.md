---
id: no-supported-environment-installs-soupsieve-below-4-0-0-1536
title: No supported environment installs soupsieve below 4.0.0 (#1536)
type: backlog_item
tags:
- dependencies
importance: 5
kind: bug
status: in_progress
priority: high
assignee: agent:cloud-worker
effort: S
rank: 0
---

https://github.com/pyrite-wiki/pyrite/issues/1536: uv.lock holds soupsieve 2.10 and 3.0.4, both under high advisories; lock 4.0.0 or later on every supported Python, or report why that needs a requires-python decision.
