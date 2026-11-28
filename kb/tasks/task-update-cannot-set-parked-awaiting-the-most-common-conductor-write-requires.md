---
id: task-update-cannot-set-parked-awaiting-the-most-common-conductor-write-requires
title: task update cannot set parked_awaiting — the most common conductor write requires hand-editing YAML
type: task
importance: 5
status: done
priority: 7
status_reason: "Resolved by #1688"
status_change_log:
- date: "2026-11-26T17:27:12Z"
  from: in_progress
  to: done
  by: operator
  comment: "Resolved by #1688 (park and unpark commands exist)"
---

## The gap

`pyrite task update` exposes `--status`, `--assignee`, `--priority`, `--reason/--status-reason`,
`--comment`, `--by`. It does **not** expose `parked_awaiting`.

Parking a task — marking it legitimately waiting on an external event rather than stalled — is one
of the most frequent conductor writes. Because there is no flag, every park is a hand-edit of the
file's YAML frontmatter via a regex rewrite.

## Why that is worse than it sounds

- **Block-string frontmatter edits fail silently.** A multi-line match breaks on invisible
  whitespace and the write reports success. The standing discipline is to read the file back after
  every such edit — an extra step per park that a flag would remove entirely.
- **No audit trail.** `task update` records a `status_change_log` entry with `--comment` and `--by`.
  A hand-edit records nothing about who parked it or why, beyond the value itself.
- **It bypasses validation**, so a malformed or misspelled key lands silently — which is how the
  field ends up inconsistent in the first place (see GH #51, and #15 for the underlying cause).
- **Inconsistent with the model.** `status_reason` is a first-class flag for "why is it in this
  state." `parked_awaiting` answers the same kind of question for a task held open, and is arguably
  more load-bearing, since the dispatch gate reads it.

## Proposal

Add `--parked-awaiting TEXT` to `task update` (and consider `--unpark` / passing an empty string to
clear it). Treat it like `--status-reason`: recorded in the status-change log, validated on write.

## Design question worth settling first

Should `parked_awaiting` become a declared `TaskEntry` schema field rather than a convention carried
in frontmatter metadata? Arguments for: the dispatch gate reads it, the CLI should be able to write
it, and #15 showed that non-schema keys are exactly what gets silently dropped. Arguments against:
it is a conductor-workflow convention, and promoting workflow conventions into the core task schema
may not generalize past this use.

That is an ADR-shaped question, which is why this is a pyrite task rather than a GitHub issue.

## Provenance

Surfaced 2026-09-18 auditing conductor tooling across a research tick. Four parks were performed in
that tick, all by hand-editing YAML. Related GitHub issues: #48 (validator silently skipped),
#51 (parked_awaiting missing from the index), #52 (discoverability of task reset/checkpoint).
