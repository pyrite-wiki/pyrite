#!/usr/bin/env bash
# Do this branch's new tests fail without its change? The local entry point to
# the CI `verify-red` job: the same script, the same verdicts.
#
#   scripts/verify-red.sh            # against origin/dev (else dev)
#   scripts/verify-red.sh --json e.json
#
# It never touches this tree: it works in a throwaway `git worktree` under
# $TMPDIR, including your uncommitted changes (see scripts/verify_red_ci.py).
# Run it once before reporting and paste the `verify-red: ...` line.
#
# It runs pytest twice, so it takes the machine-wide heavy-run lock
# (scripts/heavy_lock.py, #168) and waits its turn behind any other suite or
# Playwright run; PYRITE_SUITE_LOCK_SKIP=1 skips the lock. CI's verify-red
# job calls verify_red_ci.py directly and takes no lock.
#
#   VERIFY_RED_BASE    integration ref (default origin/dev, then dev)
#   VERIFY_RED_PYTHON  interpreter (default this checkout's .venv/bin/python)
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
top="$(git rev-parse --show-toplevel)"
py="${VERIFY_RED_PYTHON:-$top/.venv/bin/python}"
[ -x "$py" ] || py=python3
base=()
[ -z "${VERIFY_RED_BASE:-}" ] || base=(--base "$VERIFY_RED_BASE")
exec "$py" "$here/heavy_lock.py" --label "scripts/verify-red.sh" -- \
  "$py" "$here/verify_red_ci.py" --python "$py" ${base[@]+"${base[@]}"} "$@"
