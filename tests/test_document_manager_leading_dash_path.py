"""Removing the old file of a moved entry whose path begins with "-".

`git rm <path>` reads a leading "-" as an option; the path must follow `--`.
Real git repository, no mocks. Property:
tests/test_git_argv_values_follow_end_of_options.py
"""

import subprocess
from pathlib import Path

import pytest

from pyrite.storage.document_manager import DocumentManager


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, timeout=30
    ).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "kb"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "-x").mkdir()
    (root / "-x" / "old.md").write_text("old\n")
    _git(root, "add", "--", "-x/old.md")
    _git(root, "commit", "-q", "-m", "seed")
    monkeypatch.chdir(root)
    return root


@pytest.mark.parametrize(
    "relative",
    [
        True,
        pytest.param(
            False,
            marks=pytest.mark.control(
                reason="an absolute path never starts with '-', so it passes without the fix; "
                "it pins the shape callers use today"
            ),
        ),
    ],
    ids=["relative", "absolute"],
)
def test_a_dash_folder_old_file_is_removed_from_git(repo, relative):
    old = Path("-x/old.md") if relative else repo / "-x" / "old.md"
    new = repo / "new.md"
    new.write_text("new\n")

    DocumentManager._remove_old_file(object.__new__(DocumentManager), old, repo)

    assert not (repo / "-x" / "old.md").exists()
    assert "-x/old.md" not in _git(repo, "ls-files").split("\n")
    # git rm staged the deletion (a plain unlink would leave it unstaged)
    assert "D  -x/old.md" in _git(repo, "status", "--porcelain")
    assert new.read_text() == "new\n"
