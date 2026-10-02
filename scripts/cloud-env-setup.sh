#!/usr/bin/env bash
# The slow, cacheable half of a Claude Code on the web environment. Paste this
# one line into the environment's "Setup script" box (claude.ai/code, cloud
# environment settings):
#
#   curl -fsSL https://raw.githubusercontent.com/pyrite-wiki/pyrite/dev/scripts/cloud-env-setup.sh | bash
#
# It builds the checkout's .venv (pyrite with the server,cli,ai,dev,postgres
# extras, and every in-repo extension), builds the pre-commit hook
# environments, and pulls the Postgres image the Postgres tests need. All of
# that is files on disk, so when it finishes inside the ~5 minutes Anthropic
# allows, the filesystem is snapshotted and later sessions start from it
# (https://code.claude.com/docs/en/cloud-environments#environment-caching).
#
# Running it twice is safe (a present venv, hook environment and image are
# reused). It always exits 0: a non-zero exit fails the session, and
# scripts/cloud-session-start.sh repeats whatever this missed. Independent
# work runs in parallel: the image pull alongside the installs.
#
# Where the repo is. The docs do not say whether the clone exists when a setup
# script runs, nor whether the snapshot keeps it, so this is correct either
# way. Given a checkout (PYRITE_REPO_DIR, the cwd's repo, or one found under
# /workspace /home /root /mnt /opt) it installs into that checkout. Given none
# it shallow-clones the repo to a temp dir only to fill the package cache
# (uv's), and deletes it; the session hook then installs from a warm cache.
#
# Settings: PYRITE_SETUP_EXTRAS (default server,cli,ai,dev,postgres; `all,postgres`
# adds embeddings, which need huggingface.co allowed); PYRITE_SETUP_POSTGRES=0
# skips the image; PYRITE_REPO_URL for a fork.
set -uo pipefail

export PYRITE_SETUP_EXTRAS="${PYRITE_SETUP_EXTRAS:-server,cli,ai,dev,postgres}"
repo_url="${PYRITE_REPO_URL:-https://github.com/pyrite-wiki/pyrite.git}"
start=$SECONDS
note() { echo "cloud-env-setup: $*" >&2; }

is_checkout() { [ -n "${1:-}" ] && [ -f "$1/scripts/setup-checkout.sh" ] && [ -f "$1/pyproject.toml" ]; }

find_checkout() {
  local c
  for c in "${PYRITE_REPO_DIR:-}" "$(git rev-parse --show-toplevel 2>/dev/null || true)"; do
    is_checkout "$c" && { echo "$c"; return; }
  done
  if [ -f "$0" ]; then
    c="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)"
    is_checkout "$c" && { echo "$c"; return; }
  fi
  for c in $(find /workspace /home /root /mnt /opt -maxdepth 3 -type f \
               -path '*/scripts/cloud-env-setup.sh' 2>/dev/null | sed 's#/scripts/[^/]*$##'); do
    is_checkout "$c" && { echo "$c"; return; }
  done
}

repo="$(find_checkout)"
tmp=""
if [ -z "$repo" ]; then
  tmp="$(mktemp -d)"
  note "no checkout found; warming the package cache from a throwaway clone"
  git clone -q --depth 1 "$repo_url" "$tmp/pyrite" || { note "clone failed; the session hook will install instead"; exit 0; }
  repo="$tmp/pyrite"
fi
here="$repo/scripts"

# The image pull is independent of everything else.
"$here/cloud-postgres.sh" pull &
pull_pid=$!

# The installs, then the hook environments (they need the venv's pre-commit).
if "$here/setup-checkout.sh" "$repo"; then
  (cd "$repo" && .venv/bin/pre-commit install-hooks >/dev/null 2>&1) \
    || note "pre-commit environments not built; the first commit will build them"
else
  note "install failed; the session hook will retry"
fi

wait "$pull_pid" || true
[ -z "$tmp" ] || rm -rf "$tmp"
note "done in $((SECONDS - start))s ($repo, extras $PYRITE_SETUP_EXTRAS)"
exit 0
