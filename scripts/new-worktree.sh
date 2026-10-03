#!/usr/bin/env bash
# One session, one branch, one checkout (ADR-0032).
#
#   scripts/new-worktree.sh fix/what-it-fixes            # branch from the project dev
#   scripts/new-worktree.sh feature/thing v0.24.1        # branch from a tag/sha
#
# Creates ../pyrite-wt/<branch-with-slashes-as-dashes>/ as a git worktree on a
# new branch, gives it its own .venv with pyrite + every extension installed,
# and installs the git hooks. Prints the directory to cd into. Idempotent for
# an existing branch: reuses it.
#
# Why a worktree per session: two sessions cannot hold different branches in
# one working tree, and a shared tree is how one session's hook stashed
# another's edits, and how a test run replaced the shared .git/index
# (2026-09-17). The unit of isolation is the branch.
set -euo pipefail

branch="${1:?usage: $0 <branch-name> [start-point]}"
start="${2:-}"

repo_root="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)"
wt_parent="$(dirname "$repo_root")/pyrite-wt"
wt_dir="$wt_parent/${branch//\//-}"

cd "$repo_root"
python="${PYRITE_SETUP_PYTHON:-python3}"
if [ -n "$start" ]; then
  start="$("$python" "$repo_root/scripts/base_ref.py" --base "$start")"
else
  start="$("$python" "$repo_root/scripts/base_ref.py" --fetch)"
fi

# Hooks must outlive a worktree. Bootstrap the main checkout before making one.
if [ ! -x "$repo_root/.venv/bin/pre-commit" ]; then
  "$repo_root/scripts/setup-checkout.sh" "$repo_root"
fi

mkdir -p "$wt_parent"
if git show-ref --verify --quiet "refs/heads/$branch"; then
  echo "branch $branch exists; checking it out in a worktree"
  git worktree add -q "$wt_dir" "$branch"
else
  git worktree add -q -b "$branch" "$wt_dir" "$start"
fi

cd "$wt_dir"

# The venv, the repo-local config, the index and the e2e ports: the same
# steps a cloud session runs (scripts/cloud-session-start.sh).
"$repo_root/scripts/setup-checkout.sh" "$wt_dir"

# Hooks live in the shared .git and the installed shim embeds the path of the
# Python that installed them. Install from the MAIN checkout's venv, which
# outlives any worktree: hooks installed from a worktree's venv break for
# every checkout the moment that worktree is removed (learned the hard way).
(cd "$repo_root" && .venv/bin/pre-commit install >/dev/null)

cat <<EOF

worktree: $wt_dir
branch:   $branch (from $start)
venv:     $wt_dir/.venv
config:   $wt_dir/.pyrite/config.yaml  (pyrite KB -> this worktree's kb/)
e2e ports: $wt_dir/.pyrite/e2e-ports  (this worktree's Playwright ports; cat it before running lsof by hand)

  cd "$wt_dir"
  ... work, commit ...
  git push -u origin "$branch"
  gh pr create --base dev --fill && gh pr merge --auto --rebase
  # if another PR merges first the PR goes BEHIND: gh pr update-branch --rebase

When merged (the branch is deleted on GitHub automatically):
  git -C "$repo_root" worktree remove "$wt_dir" && git -C "$repo_root" branch -d "$branch"
EOF
