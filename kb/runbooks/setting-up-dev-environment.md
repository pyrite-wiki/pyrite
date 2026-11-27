---
id: setting-up-dev-environment
type: runbook
title: "Setting Up the Development Environment"
runbook_kind: setup
audience: "developers"
tags: [setup, development]
---

## Setup

For setup steps, prerequisites, and dependency lock management, see [CONTRIBUTING.md](../../CONTRIBUTING.md), "Development Setup" (using `scripts/setup-checkout.sh`).

## Troubleshooting
- If the pre-push pytest run fails: ensure extensions are installed in `.venv/` not just system Python
- `KB_NOT_FOUND` from `pyrite sw backlog -k pyrite`: the checkout's `kb/` is not registered. Run `scripts/setup-checkout.sh`, then check with `pyrite kb list`.
- "no .venv" when you push: this worktree has no venv. Run `scripts/setup-checkout.sh` in it.
- If ruff-format modifies files: re-stage and commit again (hooks run twice on failure)
- If `generate_entry_id()` errors: it takes only 1 arg (title), not 3
