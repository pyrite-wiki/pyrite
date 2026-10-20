"""
The suite must not inherit a parent git process's environment.

Under the pre-commit pytest hook the suite is a child of `git commit`, which
exports GIT_DIR / GIT_INDEX_FILE pointing at the *real* repository. GIT_DIR
beats `cwd`, so a test that runs `git config user.name Test` inside its
tmp_path writes to the developer's .git/config instead. That is how this repo
came to author 39 commits as "Test <test@test.com>", and how a private
GIT_INDEX_FILE made test_kb_commit fail only under the hook
(backlog: tests-must-not-inherit-git-env-autouse-fixture).

The guard lives in the root conftest.py. This canary re-creates the hook's
conditions in a subprocess and asserts the decoy repository is untouched.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files whose fixtures run `git init` / `git config` / `git commit` directly.
GIT_USING_TESTS = [
    "tests/test_version_service.py",
    "tests/test_kb_commit.py",
]


def _git(args, cwd, env=None):
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=120,
    )


@pytest.fixture
def decoy_repo(tmp_path):
    """Stands in for the developer's real repository."""
    repo = tmp_path / "decoy"
    repo.mkdir()
    assert _git(["init", "--initial-branch=main"], repo).returncode == 0
    _git(["config", "user.name", "Real Developer"], repo)
    _git(["config", "user.email", "real@example.com"], repo)
    (repo / "f.txt").write_text("x")
    _git(["add", "f.txt"], repo)
    assert _git(["commit", "-m", "init"], repo).returncode == 0
    return repo


def _fingerprint(repo: Path) -> dict[str, str]:
    return {
        "config": (repo / ".git" / "config").read_text(),
        "head": _git(["rev-parse", "HEAD"], repo).stdout,
        "refs": _git(["for-each-ref"], repo).stdout,
        "status": _git(["status", "--porcelain"], repo).stdout,
    }


@pytest.mark.parametrize("test_file", GIT_USING_TESTS)
def test_suite_under_a_git_hook_leaves_the_outer_repo_untouched(decoy_repo, test_file):
    before = _fingerprint(decoy_repo)

    hook_env = os.environ.copy()
    hook_env.update(
        {
            "GIT_DIR": str(decoy_repo / ".git"),
            "GIT_INDEX_FILE": str(decoy_repo / ".git" / "index"),
            "GIT_AUTHOR_NAME": "Hook Author",
            "GIT_AUTHOR_EMAIL": "hook@example.com",
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", test_file, "-q", "-x", "-p", "no:cacheprovider"],
        cwd=str(REPO_ROOT),
        env=hook_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )

    assert _fingerprint(decoy_repo) == before, "a test wrote into the outer repository"
    assert result.returncode == 0, (
        f"{test_file} failed under hook conditions:\n{result.stdout[-1500:]}"
    )
