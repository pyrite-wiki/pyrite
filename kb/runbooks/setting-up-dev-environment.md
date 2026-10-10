---
id: setting-up-dev-environment
type: runbook
title: "Setting Up the Development Environment"
runbook_kind: setup
audience: "developers"
tags: [setup, development]
---

## Prerequisites
- Python 3.11+ (3.13 recommended)
- git

## Steps

Follow [the canonical contributor setup](../../CONTRIBUTING.md#initial-setup).
It is run as a walkthrough test; the install, extension registration and KB
configuration commands live there so the recipes cannot drift.

For parallel sessions, use `scripts/new-worktree.sh <branch>` after that
initial setup. The main checkout's venv owns the shared hooks and outlives
any individual worktree.

## Troubleshooting
- If pre-commit pytest fails: ensure extensions are installed in `.venv/` not just system Python
- If ruff-format modifies files: re-stage and commit again (hooks run twice on failure)
- If `generate_entry_id()` errors: it takes only 1 arg (title), not 3
