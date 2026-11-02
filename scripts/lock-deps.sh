#!/usr/bin/env bash
# Refresh or check uv.lock, the one set of dependency versions CI and
# contributor installs use (#1357).
#
#   scripts/lock-deps.sh            # re-resolve only what pyproject.toml changed
#   scripts/lock-deps.sh --upgrade  # move every pin to the newest allowed version
#   scripts/lock-deps.sh --check    # exit 1 if uv.lock no longer matches pyproject.toml
#
# Run it after changing a dependency or the project version in pyproject.toml
# (uv.lock records both). uv.lock covers every platform and Python >= 3.11 and
# every extra. pyproject.toml's ranges stay the contract with people who
# `pip install pyrite`; the published wheel does not read the lock.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null 2>&1 || { echo "lock-deps: uv is required (https://docs.astral.sh/uv/)" >&2; exit 2; }
case "${1:-}" in
  "")        uv lock; echo "lock-deps: uv.lock is up to date" ;;
  --upgrade) uv lock --upgrade; echo "lock-deps: uv.lock upgraded" ;;
  --check)   uv lock --check || { echo "lock-deps: uv.lock is stale; run scripts/lock-deps.sh and commit it" >&2; exit 1; } ;;
  *)         echo "usage: $0 [--upgrade|--check]" >&2; exit 2 ;;
esac
