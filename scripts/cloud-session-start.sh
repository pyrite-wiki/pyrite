#!/usr/bin/env bash
# SessionStart hook (.claude/settings.json): the cheap, per-session half of a
# Claude Code on the web environment. The expensive half, scripts/cloud-env-setup.sh,
# is pasted into the cloud environment's setup-script box once and its result
# (venv, hook environments, Postgres image) is cached as a snapshot; this hook
# then only has to
#   - bring the checkout up to date (reinstall only when a pyproject.toml,
#     the lockfile or the extras changed since the snapshot),
#   - write the repo-local .pyrite/config.yaml and sync the index,
#   - install the git hooks,
#   - put .venv/bin on PATH for the session (CLAUDE_ENV_FILE),
#   - start the Postgres container the Postgres tests need, and export
#     PYRITE_TEST_PG_URL when (and only when) it accepts connections.
#
# It also works, just slower, when no setup script ran: a contributor who
# skipped the environment step finds no .venv, so this installs everything
# (a minute or three) and pulls the image on first use. Nothing needs the
# setup script for correctness.
#
# Outside a cloud session (CLAUDE_CODE_REMOTE unset) it does nothing: a local
# checkout is set up once by scripts/new-worktree.sh, not on every session.
#
# Embeddings are left out by default (sentence-transformers pulls torch, and
# huggingface.co is not on the default network allowlist); their tests skip
# without them. For the full install set PYRITE_SETUP_EXTRAS=all,postgres in
# the environment's variables. PYRITE_SETUP_POSTGRES=0 opts out of Postgres.
set -euo pipefail

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

dir="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
export PYRITE_SETUP_EXTRAS="${PYRITE_SETUP_EXTRAS:-server,cli,ai,dev,postgres}"

"$dir/scripts/setup-checkout.sh" --if-changed "$dir" \
  || echo "warning: install failed; .venv may be incomplete (re-run scripts/setup-checkout.sh $dir)" >&2

# One checkout, one venv: install the hooks from it.
(cd "$dir" && .venv/bin/pre-commit install >/dev/null) \
  || echo "note: pre-commit hooks not installed; commits will skip ruff/KB checks (CI still runs them)" >&2

# A hook's environment ends with the hook; CLAUDE_ENV_FILE carries PATH into
# the session's shell so `pyrite`, `pytest` and `ruff` resolve to this venv.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"$dir/.venv/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi

# Postgres: never fatal, and the export appears only if the database is up.
pg_export="$("$dir/scripts/cloud-postgres.sh" start || true)"
if [ -n "$pg_export" ] && [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "$pg_export" >> "$CLAUDE_ENV_FILE"
fi

echo "pyrite: $dir/.venv ready ($PYRITE_SETUP_EXTRAS); KB config $dir/.pyrite/config.yaml; postgres tests $([ -n "$pg_export" ] && echo on || echo off)"
