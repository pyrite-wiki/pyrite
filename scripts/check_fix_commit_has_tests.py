#!/usr/bin/env python3
"""Require `fix:`-prefixed commits to touch tests/ or extensions/*/tests/.

Mechanizes Iron Law 1 (no production code without a failing test first) at
the commit boundary -- a `fix:` commit with zero test lines is exactly the
class of regression the Iron Law exists to prevent (see 40e7a39, the fix
that shipped with zero test lines and this ticket's own motivation).

Two modes:

- Local commit-msg hook (default): receives the commit message file path as
  argv[1], diffs the staged tree against HEAD, and exits non-zero if the
  message starts with `fix:` but no staged file lives under a tests/
  directory.
- CI range mode (`--range BASE HEAD`): walks every commit in BASE..HEAD (a
  pull request's own commits) and applies the same rule to each commit's own
  message and own changed paths -- not the range's cumulative diff, so an
  earlier offending commit isn't hidden by a later, unrelated commit that
  happens to touch tests/. Used because outside contributors' PRs never run
  the local commit-msg hook, so the rule has to be enforced again in CI.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# `fix:`, `fix(scope):`, `fix!:` and `fix(scope)!:` (#1074). Case-sensitive on
# purpose: Conventional Commits types are lower case, and "Fix the crash" is
# prose that this check should not guess at.
FIX_SUBJECT = re.compile(r"fix(\([^)\n]*\))?!?:")


def is_fix_commit(message: str) -> bool:
    """True if the subject line is a conventional fix: `fix:`, `fix(scope):`,
    `fix!:` or `fix(scope)!:`."""
    subject = message.strip().splitlines()[0] if message.strip() else ""
    return FIX_SUBJECT.match(subject) is not None


_FRONTEND_TEST_SUFFIXES = (".test.ts", ".test.js", ".spec.ts", ".spec.js")


def _is_test_path(path: str) -> bool:
    """A path that is a test: anything under a tests/ directory (top-level
    tests/ or extensions/*/tests/), or a frontend test file under web/ --
    vitest files sit beside their source and Playwright specs live in
    web/e2e/, so neither is ever under a tests/ directory (#159)."""
    parts = Path(path).parts
    if "tests" in parts:
        return True
    return bool(parts) and parts[0] == "web" and path.endswith(_FRONTEND_TEST_SUFFIXES)


def touches_tests(changed_paths: list[str]) -> bool:
    """True if any changed path is a test (see _is_test_path)."""
    return any(_is_test_path(path) for path in changed_paths)


def get_staged_paths() -> list[str]:
    """Paths staged for this commit, relative to the repo root."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def get_commit_message(sha: str) -> str:
    """The full commit message (subject + body) for a single commit."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%B", sha],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def get_commit_paths(sha: str) -> list[str]:
    """Paths that commit `sha` itself changed (not the cumulative diff of a
    range -- each commit is judged on what it, individually, touched)."""
    result = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def get_range_commits(base: str, head: str) -> list[str]:
    """Commit SHAs in base..head, oldest first."""
    result = subprocess.run(
        ["git", "rev-list", "--reverse", f"{base}..{head}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


_NO_TEST_MESSAGE = (
    "no {what} is under a tests/ directory.\n"
    "  Iron Law 1: no production code without a failing test first.\n"
    "  If this fix genuinely has no testable behavior change (e.g. a\n"
    "  pure docs/config fix), reword the subject to drop the 'fix:' (or\n"
    "  'fix(scope):', 'fix!:') prefix, or add the regression test the fix should have shipped\n"
    "  with."
)


def check_range(base: str, head: str) -> int:
    """CI mode: apply the rule to every commit in base..head individually.

    Returns 0 if every `fix:` commit in the range touches tests/, 1 if any
    does not (each offender is reported, not just the first).
    """
    failed = False
    for sha in get_range_commits(base, head):
        message = get_commit_message(sha)
        if not is_fix_commit(message):
            continue
        if touches_tests(get_commit_paths(sha)):
            continue
        subject = message.strip().splitlines()[0] if message.strip() else ""
        print(
            f"ERROR: {sha[:12]} '{subject}' starts with a fix prefix (fix:, fix(scope):, fix!:) but "
            + _NO_TEST_MESSAGE.format(what="changed file"),
            file=sys.stderr,
        )
        failed = True
    return 1 if failed else 0


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--range":
        return check_range(sys.argv[2], sys.argv[3])

    if len(sys.argv) < 2:
        print("check_fix_commit_has_tests: missing commit-msg file argument", file=sys.stderr)
        return 1

    message = Path(sys.argv[1]).read_text()
    if not is_fix_commit(message):
        return 0

    changed_paths = get_staged_paths()
    if touches_tests(changed_paths):
        return 0

    print(
        "ERROR: commit message starts with a fix prefix (fix:, fix(scope):, fix!:) but "
        + _NO_TEST_MESSAGE.format(what="staged file"),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
