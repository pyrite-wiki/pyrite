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

### 1. Clone and create venv
```bash
git clone <repo-url>
cd pyrite
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install pyrite with all optional dependencies plus dev tooling
```bash
pip install -e ".[all]"
```
`dev` alone (pytest, ruff, mypy, build, pre-commit) is tooling only — it has
no `fastapi` and no CLI deps, so it cannot even collect the suite. `all` is
the meta-extra `pyrite[server,cli,ai,semantic,dev]` and is what CONTRIBUTING
uses.

### 3. Install all extensions
```bash
for ext in extensions/*/; do pip install -e "$ext"; done
```
This installs every directory under `extensions/` as it exists today, so the
list here cannot drift out of sync the way an enumerated one has before.

### 4. Install pre-commit hooks
```bash
pre-commit install
```

### 5. Verify

Use [Lint](../../CONTRIBUTING.md#lint) and
[Running the tests](../../CONTRIBUTING.md#running-the-tests).

## Troubleshooting
- If the pre-push affected-test run fails: ensure extensions are installed in `.venv/` not just system Python
- `KB_NOT_FOUND` from `pyrite sw backlog -k pyrite`: the checkout's `kb/` is not registered. Run `scripts/setup-checkout.sh`, then check with `pyrite kb list`.
- "no .venv" when you push: this worktree has no venv. Run `scripts/setup-checkout.sh` in it.
- If ruff-format modifies files: re-stage and commit again (hooks run twice on failure)
- If `generate_entry_id()` errors: it takes only 1 arg (title), not 3
