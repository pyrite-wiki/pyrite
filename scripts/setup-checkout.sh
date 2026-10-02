#!/usr/bin/env bash
# Set up one checkout for Pyrite work: its own .venv with pyrite and every
# extension installed, a repo-local .pyrite/config.yaml so `pyrite -k pyrite`
# means THIS checkout's kb/, the index, and the e2e ports.
#
#   scripts/setup-checkout.sh [dir]        # default: the checkout this script is in
#   PYRITE_SETUP_EXTRAS=cli,dev scripts/setup-checkout.sh
#
# Shared by scripts/new-worktree.sh (a laptop worktree) and
# scripts/cloud-session-start.sh (a Claude Code on the web session), so the
# two cannot drift. Idempotent: re-running brings an existing venv up to date.
# Git hooks are the caller's job -- where they install from differs.
set -euo pipefail

wt_dir="$(cd "${1:-$(dirname "$0")/..}" && git rev-parse --show-toplevel)"
extras="${PYRITE_SETUP_EXTRAS:-all}"
label="$(git -C "$wt_dir" branch --show-current 2>/dev/null || true)"
cd "$wt_dir"

# A venv per worktree: an editable install points at one checkout, so a
# shared venv would import whichever tree was installed last.
#
# It is cheaper than it looks, and `du` will tell you otherwise. On APFS `uv`
# clones blocks rather than copying them, so a venv `du` reports as 1.0 GB
# costs about 112 MB of real disk -- measured 2026-09-21 from free space
# before and after removing one, and 12 MB to rebuild it (#236).
# `UV_LINK_MODE=hardlink` changes nothing here: 3 MB against 5 MB for a second
# torch. So do not collapse this into a shared venv on the strength of a `du`
# number: the saving is not there, and the isolation is the whole point (#189).
#
# The cost that IS real is worktree *count*. The conductor's health step reaps
# a worktree once its PR merges.
if command -v uv >/dev/null 2>&1; then
  [ -x .venv/bin/python ] || uv venv -q .venv
  uv pip install -q --python .venv/bin/python -e ".[$extras]"
  for ext in extensions/*/; do
    [ -f "$ext/pyproject.toml" ] && uv pip install -q --python .venv/bin/python -e "$ext"
  done
else
  [ -x .venv/bin/python ] || python3 -m venv .venv
  .venv/bin/pip install -q -e ".[$extras]"
  for ext in extensions/*/; do
    [ -f "$ext/pyproject.toml" ] && .venv/bin/pip install -q -e "$ext"
  done
fi

# A repo-local config so `pyrite -k pyrite` in this worktree means THIS
# worktree's kb/, not the main checkout's (which is what ~/.pyrite registers).
# pyrite finds ./.pyrite/config.yaml by searching upward from the cwd; an
# explicit PYRITE_CONFIG_DIR still wins. Gitignored.
mkdir -p .pyrite
cat > .pyrite/config.yaml <<CFG
knowledge_bases:
- name: pyrite
  path: $wt_dir/kb
  kb_type: software
  description: "Pyrite project KB (checkout ${label:-detached})"
settings:
  index_path: $wt_dir/.pyrite/index.db
  auto_embed: false
CFG
.venv/bin/pyrite index sync >/dev/null 2>&1 || true

# This worktree's Playwright e2e ports, derived from its own path so two
# worktrees can run `npm run test:e2e` at once without colliding (Package
# A.1, #118: kb/backlog/playwright-package-a-1-per-worktree-ports-and-data-dir-118.md).
# Recorded here — not just computed on demand inside playwright.config.ts —
# so a human running the suite by hand (lsof, curl, a stray uvicorn to kill)
# sees the same numbers the config derives. `web/e2e/ports.ts` is the single
# source of truth for the hash; this only prints it. Requires the worktree's
# node_modules for `derivePorts`'s only import (node:crypto, no npm package),
# so it works even before `npm ci` has run in web/.
if command -v node >/dev/null 2>&1 && [ -f "$wt_dir/web/e2e/print-ports.ts" ]; then
  node "$wt_dir/web/e2e/print-ports.ts" "$wt_dir" > "$wt_dir/.pyrite/e2e-ports" \
    || echo "note: could not derive e2e ports (node too old for native TS?) — playwright.config.ts will still derive them at test time" >&2
else
  echo "note: node not found; skipping .pyrite/e2e-ports (playwright.config.ts derives ports itself at test time)" >&2
fi
