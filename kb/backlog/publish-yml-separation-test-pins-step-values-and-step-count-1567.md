---
id: publish-yml-separation-test-pins-step-values-and-step-count-1567
title: publish.yml separation test pins step values and step count (#1567)
type: backlog_item
tags:
- quality
importance: 5
kind: chore
status: in_progress
priority: medium
assignee: agent:cloud-worker
effort: S
rank: 0
---

tests/test_publish_workflow_separation.py pins, for each publishing job, the exact list of steps (uses with its SHA, and the with values), and for the build job the upload steps' name and path. Five planted changes that passed before now fail. Tests only.
