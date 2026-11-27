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
**Fix**: Stage the formatted file explicitly, then commit again: `git add path/to/file && git commit`.

### pytest hook fails with import error
**Symptom**: `ModuleNotFoundError: No module named 'pyrite_zettelkasten'`
**Cause**: The checkout's extensions are not installed in its virtual environment.
**Fix**: Run `scripts/setup-checkout.sh` in this checkout to install the core package and extensions.

### Python version mismatch
**Symptom**: `requires-python = ">=3.11"` error
**Cause**: System Python is older than 3.11
**Fix**: Run `scripts/setup-checkout.sh` with Python 3.11 or later, then use `.venv/bin/python` for this checkout.

### ruff lint errors after hook
**Symptom**: `Found N errors (N fixed, 0 remaining)` but commit still fails
**Cause**: ruff auto-fixed the issues but the files need re-staging
**Fix**: Stage each changed file explicitly, then commit again: `git add path/to/file && git commit`.
