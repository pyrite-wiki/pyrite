---
id: troubleshooting-pre-commit
type: runbook
title: "Troubleshooting Pre-commit Hook Failures"
runbook_kind: troubleshooting
audience: "developers"
tags: [git, pre-commit, troubleshooting]
---

## Common Issues

### ruff-format reformats files, commit fails
**Symptom**: Commit fails, but running it again also fails.
**Cause**: ruff-format modifies files, but they need to be re-staged.
**Fix**: `git add -u && git commit` (the hooks run twice — first pass reformats, second pass should pass)

### Test or extension import failure

Tests run at pre-push, not at commit. Follow the canonical
[hook and failure policy](../../CONTRIBUTING.md#branches-hooks-and-pull-requests)
and [development setup](../../CONTRIBUTING.md#development-setup) for installing extensions.

### Python version mismatch
**Symptom**: `requires-python = ">=3.11"` error
**Cause**: System Python is older than 3.11
**Fix**: Use `.venv/` Python (3.11+) for all operations. The venv is set up with the correct version.

### ruff lint errors after hook
**Symptom**: `Found N errors (N fixed, 0 remaining)` but commit still fails
**Cause**: ruff auto-fixed the issues but the files need re-staging
**Fix**: `git add -u && git commit` again
