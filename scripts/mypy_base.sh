#!/usr/bin/env bash
# Print the reachable comparison commit, or an empty line for baseline fallback.
set -euo pipefail
case "${EVENT:-}" in
  pull_request) base="$(git rev-parse --verify HEAD^1 2>/dev/null || true)" ;;
  push) base="${PUSH_BEFORE:-}" ;;
  merge_group) base="${QUEUE_BASE:-}" ;;
  *) base="" ;;
esac
if [[ "$base" =~ ^[0-9a-fA-F]{40}$ ]] && [[ -n "${base//0/}" ]]; then
  if git cat-file -e "$base^{commit}" 2>/dev/null || git fetch --depth=1 origin "$base"; then
    printf '%s\n' "$base"
    exit 0
  fi
fi
printf '\n'
