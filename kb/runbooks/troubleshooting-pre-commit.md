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
**Fix**: `git add <file> && git commit` (the hooks run twice — first pass reformats, second pass should pass)

### pre-push pytest hook fails with import error
**Symptom**: `ModuleNotFoundError: No module named 'pyrite_zettelkasten'`
**Cause**: Extensions not installed in `.venv/` (the pre-push `pytest-full` hook uses `.venv/bin/activate`)
**Fix**: `.venv/bin/pip install -e extensions/<name>` for each extension

### Python version mismatch
**Symptom**: `requires-python = ">=3.11"` error
**Cause**: System Python is older than 3.11
**Fix**: Use `.venv/` Python (3.11+) for all operations. The venv is set up with the correct version.

### ruff lint errors after hook
**Symptom**: `Found N errors (N fixed, 0 remaining)` but commit still fails
**Cause**: ruff auto-fixed the issues but the files need re-staging
**Fix**: `git add <file> && git commit` again
