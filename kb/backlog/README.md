---
type: note
title: "Pyrite Backlog"
tags: [backlog, process]
---
# Pyrite Backlog

Features, improvements, and bugs for Pyrite. The backlog is queried through
the CLI — the file layout below is a convention, not a source of truth.

## Source of truth: the CLI

```bash
# Active items, prioritized
pyrite sw backlog

# Filter by status / priority
pyrite sw backlog --status proposed
pyrite sw backlog --status "in-progress"

# Full body for one item
pyrite get <item-id> -k pyrite

# What links to / from an item
pyrite backlinks <item-id> -k pyrite
```

There is intentionally no `BACKLOG.md` index file. The previous numbered-list
file was deleted in April 2026 — it drifted from reality whenever an item was
moved without re-numbering. `pyrite sw backlog` reads frontmatter directly and
cannot drift.

Epics provide narrative grouping inside individual items (`pyrite get
epic-<name> -k pyrite`).

## Folder layout

```
kb/backlog/
├── README.md           # this file
├── *.md                # active items
├── done/               # completed items
└── future-ideas/       # lower-priority, deferred
```

| Location | Purpose |
|----------|---------|
| `kb/backlog/*.md` | Active priority items (target: ~30) |
| `kb/backlog/done/` | Completed — moved here when `status: completed` is set |
| `kb/backlog/future-ideas/` | Valid ideas not in the current focus; promote when priorities shift |

## Workflow: completing an item

1. Set status via CLI (validates + reindexes):
   ```bash
   pyrite update <item-id> -k pyrite -f status=completed
   ```
   Do **not** hand-edit frontmatter for status changes — the CLI keeps the index
   in sync and rejects invalid transitions.

2. Move the file to `done/`:
   ```bash
   git mv kb/backlog/<item-id>.md kb/backlog/done/
   ```

3. Resync the index so the move is reflected:
   ```bash
   pyrite index sync
   ```

4. If the work revealed new tech debt or follow-on features, create new items:
   ```bash
   pyrite create -k pyrite -t backlog_item --title "..." -b "..." --tags <tags>
   ```
   Place in `kb/backlog/` if high priority, `kb/backlog/future-ideas/` if low.

5. Verify with `pyrite sw backlog`.

## Promoting a future-idea

```bash
git mv kb/backlog/future-ideas/<item-id>.md kb/backlog/
pyrite update <item-id> -k pyrite -f priority=<high|medium>
pyrite index sync
```

## Backlog item format

```yaml
---
id: <kebab-case-id>
type: backlog_item
title: "Human-readable title"
kind: feature | improvement | bug | epic | refactor | chore | process | docs | tech_debt
status: proposed | in_progress | completed | retired | deferred | superseded
priority: critical | high | medium | low
effort: S | M | L | XL
tags: [relevant, tags]
---

## Problem
...

## Proposed solution
...

## Acceptance criteria
- ...
```

Body sections vary — most items use **Problem / Proposed solution / Acceptance
criteria**. Epics use **Goals / Phases / Subtasks** (linked via `[[wikilinks]]`
so `pyrite backlinks` finds them).
