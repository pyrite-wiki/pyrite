#!/usr/bin/env bash
# Print the path of a pip constraints file exported from uv.lock (#1357).
#
#   uv pip install -c "$(scripts/lock-constraints.sh)" -e ".[all]"
#
# uv.lock is the one lock (Dependabot's `uv` ecosystem maintains it). Installs
# keep the `uv pip install -e` verb, `--system` included, and the extension
# loop, so they apply the lock as constraints instead of switching to
# `uv sync`. The export is never committed: a second file would go stale the
# moment Dependabot bumps uv.lock. --frozen reads the lock as it is; whether it
# still matches pyproject.toml is `scripts/lock-deps.sh --check`.
# UV=<path> picks the uv binary (setup-checkout.sh's pip path installs one).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$(mktemp "${TMPDIR:-/tmp}/pyrite-constraints.XXXXXX")"
"${UV:-uv}" export --frozen --all-extras --no-emit-project --no-hashes --no-annotate --no-header \
  --project "$root" -o "$out" >/dev/null 2>&1 || { rm -f "$out"; echo "lock-constraints: uv export failed (is uv.lock present?)" >&2; exit 1; }
echo "$out"
