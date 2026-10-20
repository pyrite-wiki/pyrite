#!/usr/bin/env bash
# Playwright, one heavy run at a time on this machine (#168).
#
#   scripts/e2e.sh                         # the whole browser suite
#   scripts/e2e.sh e2e/file.spec.ts        # one spec; any `playwright test` args
#   scripts/e2e.sh --headed | --debug
#
# A Playwright run starts uvicorn, vite and Chromium workers: it takes the
# same machine-wide lock as `scripts/test-affected --run` and
# `scripts/verify-red.sh` (scripts/heavy_lock.py), waits its turn, and says
# who it is waiting for. `npm run test:e2e` calls this script, so it cannot
# go around the lock. Local workers: PW_WORKERS, else Playwright's default
# (half the cores; a lower default waits on #906 -- web/e2e/workers.ts).
#
#   PYRITE_SUITE_LOCK_SKIP=1   run without the lock
#   CI / GITHUB_ACTIONS set    no lock (and no Python needed)
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/../web"
truthy() { case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in ""|0|false|no|off) return 1;; *) return 0;; esac; }
if truthy "${CI:-}" || truthy "${GITHUB_ACTIONS:-}"; then
  exec npx playwright test "$@"
fi
exec python3 "$here/heavy_lock.py" --label "scripts/e2e.sh $*" -- npx playwright test "$@"
