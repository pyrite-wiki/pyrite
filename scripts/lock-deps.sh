#!/usr/bin/env bash
# Regenerate (or check) requirements.lock, the one set of dependency versions
# CI and contributor installs use (#1357).
#
#   scripts/lock-deps.sh            # re-resolve only what pyproject.toml changed
#   scripts/lock-deps.sh --upgrade  # move every pin to the newest allowed version
#   scripts/lock-deps.sh --check    # exit 1 if the lock no longer matches pyproject.toml
#
# The file is a pip constraints file: it pins versions and installs nothing by
# itself, so `pyproject.toml` stays the contract with users and the published
# wheel is unchanged. Installs apply it with `-c requirements.lock`.
#
# --universal resolves once for every platform and Python >= 3.11 (CI runs
# 3.11, 3.12 and 3.13), with environment markers where a pin differs.
# --all-extras covers every extra any job installs (server, cli, ai, semantic,
# postgres, dev). Extensions declare no dependencies of their own.
# uv keeps the pins already in the output file, so a plain run changes only what
# pyproject.toml's ranges force; --upgrade is the deliberate move.
set -euo pipefail
cd "$(dirname "$0")/.."

mode="${1:-}"
case "$mode" in ""|--upgrade|--check) ;; *) echo "usage: $0 [--upgrade|--check]" >&2; exit 2 ;; esac
command -v uv >/dev/null 2>&1 || { echo "lock-deps: uv is required (https://docs.astral.sh/uv/)" >&2; exit 2; }

compile() {  # compile <output file> [extra uv flags]
  local out="$1"; shift
  uv pip compile pyproject.toml --all-extras --universal --python-version 3.11 \
    --no-annotate --quiet -o "$out" "$@"
}

if [ "$mode" = "--check" ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  cp requirements.lock "$tmp/requirements.lock"
  # Recompile on top of the current pins: they change only if pyproject.toml
  # no longer agrees with them.
  cp pyproject.toml "$tmp/"
  (cd "$tmp" && compile requirements.lock)
  if ! diff -u requirements.lock "$tmp/requirements.lock"; then
    echo "lock-deps: requirements.lock is stale; run scripts/lock-deps.sh and commit it" >&2
    exit 1
  fi
  echo "lock-deps: requirements.lock matches pyproject.toml"
  exit 0
fi

if [ "$mode" = "--upgrade" ]; then compile requirements.lock --upgrade; else compile requirements.lock; fi
echo "lock-deps: wrote requirements.lock"
