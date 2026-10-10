#!/usr/bin/env bash
# Do this branch's new tests fail without its change? The local entry point to
# the CI `verify-red` job: the same script, the same verdicts.
#
#   scripts/verify-red.sh            # against PYRITE_BASE, upstream/dev, origin/dev, then dev
#   scripts/verify-red.sh --json e.json
#
# It never touches this tree: it works in a throwaway `git worktree` under
# $TMPDIR, including your uncommitted changes (see scripts/verify_red_ci.py).
# Run it once before reporting and paste the `verify-red: ...` line.
#
#   VERIFY_RED_BASE    integration ref (default PYRITE_BASE, upstream/dev, origin/dev, then dev)
#   VERIFY_RED_PYTHON  interpreter (default this checkout's .venv/bin/python)
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
top="$(git rev-parse --show-toplevel)"
py="${VERIFY_RED_PYTHON:-$top/.venv/bin/python}"
[ -x "$py" ] || py=python3
base=()
[ -z "${VERIFY_RED_BASE:-}" ] || base=(--base "$VERIFY_RED_BASE")
exec "$py" "$here/verify_red_ci.py" --python "$py" ${base[@]+"${base[@]}"} "$@"
