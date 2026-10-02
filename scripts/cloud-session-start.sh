#!/usr/bin/env bash
# SessionStart hook (.claude/settings.json): set up a Claude Code on the web
# session so it is ready for Pyrite work -- venv, extensions, repo-local KB
# config, index, git hooks -- through the same scripts/setup-checkout.sh a
# laptop worktree uses.
#
# Outside a cloud session (CLAUDE_CODE_REMOTE unset) it does nothing: a local
# checkout is set up once by scripts/new-worktree.sh, not on every session.
#
# A cloud VM starts fresh each session, so this runs every time; on a warm
# venv it is an up-to-date check. Embeddings are left out by default
# (sentence-transformers pulls torch, and huggingface.co is not on the
# default network allowlist); their tests skip without them. For the full
# install set PYRITE_SETUP_EXTRAS=all in the cloud environment's variables.
set -euo pipefail

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

dir="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
export PYRITE_SETUP_EXTRAS="${PYRITE_SETUP_EXTRAS:-server,cli,ai,dev}"

"$dir/scripts/setup-checkout.sh" "$dir"

# One checkout, one venv: install the hooks from it.
(cd "$dir" && .venv/bin/pre-commit install >/dev/null) \
  || echo "note: pre-commit hooks not installed; commits will skip ruff/KB checks (CI still runs them)" >&2

# A hook's environment ends with the hook; CLAUDE_ENV_FILE carries PATH into
# the session's shell so `pyrite`, `pytest` and `ruff` resolve to this venv.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"$dir/.venv/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi

echo "pyrite: $dir/.venv ready ($PYRITE_SETUP_EXTRAS); KB config $dir/.pyrite/config.yaml"
