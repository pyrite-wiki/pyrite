"""Integration tests for KB git operations (commit_kb, push_kb).

These tests use real temporary git repos to verify end-to-end behavior
of KBService.commit_kb() and push_kb(), complementing the existing tests
in test_kb_commit.py with deeper integration verification: git log inspection,
multi-commit history, subdirectory handling, branch awareness, and push
content verification.
"""

import shutil
import subprocess

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import KBNotFoundError, PyriteError
from pyrite.services.export_service import ExportService
from pyrite.services.git_service import GitService
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB

# Skip all tests if git is not available
pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git not available",
)


def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.fixture
def git_kb(tmp_path):
    """Create a git-backed KB with an initial commit."""
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()

    _git(["init"], str(kb_path))
    _git(["config", "user.email", "test@pyrite.dev"], str(kb_path))
    _git(["config", "user.name", "Pyrite Test"], str(kb_path))

    # Initial commit so HEAD exists
    (kb_path / "README.md").write_text("# Test KB\n")
    _git(["add", "."], str(kb_path))
    _git(["commit", "-m", "Initial commit"], str(kb_path))

    kb = KBConfig(name="test-kb", path=kb_path, kb_type="generic")
    config = PyriteConfig(
        knowledge_bases=[kb],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(tmp_path / "index.db")
    svc = KBService(config, db)
    export_svc = ExportService(config, db)

    yield {"kb_path": kb_path, "svc": svc, "export_svc": export_svc, "config": config, "db": db}
    db.close()


# =============================================================================
# commit_kb — git log verification
# =============================================================================


class TestCommitKBCreatesCommit:
    """Verify that commit_kb creates a real git commit visible in git log."""

    def test_commit_appears_in_git_log(self, git_kb):
        export_svc, kb_path = git_kb["export_svc"], git_kb["kb_path"]
        (kb_path / "note.md").write_text("---\ntitle: Note\n---\n\nContent\n")

        result = export_svc.commit_kb("test-kb", message="Add note entry")
        assert result["success"]

        # Verify commit is in git log
        log = _git(["log", "--oneline", "-5"], str(kb_path))
        assert "Add note entry" in log.stdout

    def test_commit_hash_matches_head(self, git_kb):
        export_svc, kb_path = git_kb["export_svc"], git_kb["kb_path"]
        (kb_path / "entry.md").write_text("---\ntitle: Entry\n---\n\nBody\n")

        result = export_svc.commit_kb("test-kb", message="Hash check")
        assert result["success"]

        head = _git(["rev-parse", "HEAD"], str(kb_path))
        assert result["commit_hash"] == head.stdout.strip()


class TestCommitKBWithMessage:
    """Verify custom commit messages are recorded correctly."""

    def test_custom_message_in_full_log(self, git_kb):
        export_svc, kb_path = git_kb["export_svc"], git_kb["kb_path"]
        (kb_path / "custom.md").write_text("content")

        msg = "feat: add custom entry with special chars & symbols"
        result = export_svc.commit_kb("test-kb", message=msg)
        assert result["success"]

        log = _git(["log", "-1", "--format=%B"], str(kb_path))
        assert msg in log.stdout.strip()

    def test_multiline_message(self, git_kb):
        export_svc, kb_path = git_kb["export_svc"], git_kb["kb_path"]
        (kb_path / "multi.md").write_text("multiline test")

        msg = "Subject line\n\nDetailed body paragraph."
        result = export_svc.commit_kb("test-kb", message=msg)
        assert result["success"]

        log = _git(["log", "-1", "--format=%B"], str(kb_path))
        assert "Subject line" in log.stdout
        assert "Detailed body paragraph." in log.stdout


class TestCommitKBNoChanges:
    """Verify behavior when there is nothing to commit."""

    def test_no_changes_returns_failure(self, git_kb):
        export_svc = git_kb["export_svc"]
        result = export_svc.commit_kb("test-kb", message="Empty commit attempt")
        assert not result["success"]
        assert "error" in result

    def test_no_changes_does_not_create_commit(self, git_kb):
        export_svc, kb_path = git_kb["export_svc"], git_kb["kb_path"]

        # Record current HEAD
        before = _git(["rev-parse", "HEAD"], str(kb_path)).stdout.strip()

        result = export_svc.commit_kb("test-kb", message="Should not appear")
        assert not result["success"]

        after = _git(["rev-parse", "HEAD"], str(kb_path)).stdout.strip()
        assert before == after


class TestCommitKBEdgeCases:
    """Edge cases for commit_kb."""

    def test_commit_files_in_subdirectory(self, git_kb):
        export_svc, kb_path = git_kb["export_svc"], git_kb["kb_path"]
        subdir = kb_path / "topics"
        subdir.mkdir()
        (subdir / "topic-a.md").write_text("---\ntitle: Topic A\n---\n\nTopic A body\n")

        result = export_svc.commit_kb("test-kb", message="Add topic in subdir")
        assert result["success"]

        # Verify the file is tracked
        ls = _git(["ls-files"], str(kb_path))
        assert "topics/topic-a.md" in ls.stdout

    def test_commit_specific_paths_leaves_others_unstaged(self, git_kb):
        export_svc, kb_path = git_kb["export_svc"], git_kb["kb_path"]
        (kb_path / "staged.md").write_text("staged content")
        (kb_path / "unstaged.md").write_text("unstaged content")

        result = export_svc.commit_kb("test-kb", message="Partial commit", paths=["staged.md"])
        assert result["success"]
        assert result["files_changed"] >= 1

        # unstaged.md should still be untracked
        status = _git(["status", "--porcelain"], str(kb_path))
        assert "unstaged.md" in status.stdout

    def test_multiple_sequential_commits(self, git_kb):
        export_svc, kb_path = git_kb["export_svc"], git_kb["kb_path"]

        (kb_path / "first.md").write_text("first")
        r1 = export_svc.commit_kb("test-kb", message="First commit")
        assert r1["success"]

        (kb_path / "second.md").write_text("second")
        r2 = export_svc.commit_kb("test-kb", message="Second commit")
        assert r2["success"]

        # Both commits should be in log, with distinct hashes
        assert r1["commit_hash"] != r2["commit_hash"]

        log = _git(["log", "--oneline", "-3"], str(kb_path))
        assert "First commit" in log.stdout
        assert "Second commit" in log.stdout

    def test_commit_kb_not_found_raises(self, git_kb):
        export_svc = git_kb["export_svc"]
        with pytest.raises(KBNotFoundError):
            export_svc.commit_kb("nonexistent", message="fail")

    def test_commit_kb_not_git_repo_raises(self, tmp_path):
        """A KB that exists on disk but is not a git repo should raise PyriteError."""
        kb_path = tmp_path / "plain-kb"
        kb_path.mkdir()
        (kb_path / "note.md").write_text("note")

        kb = KBConfig(name="plain", path=kb_path, kb_type="generic")
        config = PyriteConfig(
            knowledge_bases=[kb],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        db = PyriteDB(tmp_path / "index.db")
        export_svc = ExportService(config, db)

        try:
            with pytest.raises(PyriteError, match="not in a git repository"):
                export_svc.commit_kb("plain", message="fail")
        finally:
            db.close()

    def test_commit_with_sign_off(self, git_kb):
        export_svc, kb_path = git_kb["export_svc"], git_kb["kb_path"]
        (kb_path / "signed.md").write_text("signed content")

        result = export_svc.commit_kb("test-kb", message="Signed commit", sign_off=True)
        assert result["success"]

        log = _git(["log", "-1", "--format=%B"], str(kb_path))
        assert "Signed-off-by:" in log.stdout


# =============================================================================
# push_kb
# =============================================================================


class TestPushKBNoRemote:
    """Verify graceful failure when no remote is configured."""

    def test_push_no_remote_returns_failure(self, git_kb):
        export_svc = git_kb["export_svc"]
        result = export_svc.push_kb("test-kb")
        assert not result["success"]
        assert "message" in result

    def test_push_kb_not_found_raises(self, git_kb):
        export_svc = git_kb["export_svc"]
        with pytest.raises(KBNotFoundError):
            export_svc.push_kb("nonexistent")


class TestPushKBWithRemote:
    """Test push_kb with a local bare repo as the remote."""

    def test_push_to_local_bare_repo(self, git_kb, tmp_path):
        export_svc, kb_path = git_kb["export_svc"], git_kb["kb_path"]

        # Create a bare remote repo
        remote_path = tmp_path / "remote.git"
        _git(["init", "--bare", str(remote_path)], str(tmp_path))
        _git(["remote", "add", "origin", str(remote_path)], str(kb_path))

        result = export_svc.push_kb("test-kb")
        assert result["success"]

    def test_push_delivers_commits_to_remote(self, git_kb, tmp_path):
        export_svc, kb_path = git_kb["export_svc"], git_kb["kb_path"]

        # Set up bare remote
        remote_path = tmp_path / "remote.git"
        _git(["init", "--bare", str(remote_path)], str(tmp_path))
        _git(["remote", "add", "origin", str(remote_path)], str(kb_path))

        # Push initial state
        export_svc.push_kb("test-kb")

        # Add a new commit and push
        (kb_path / "pushed.md").write_text("pushed content")
        export_svc.commit_kb("test-kb", message="Commit to push")
        result = export_svc.push_kb("test-kb")
        assert result["success"]

        # Clone the remote and verify the commit arrived
        clone_path = tmp_path / "clone"
        _git(["clone", str(remote_path), str(clone_path)], str(tmp_path))
        assert (clone_path / "pushed.md").exists()
        assert (clone_path / "pushed.md").read_text() == "pushed content"

    def test_push_custom_remote_name(self, git_kb, tmp_path):
        export_svc, kb_path = git_kb["export_svc"], git_kb["kb_path"]

        remote_path = tmp_path / "upstream.git"
        _git(["init", "--bare", str(remote_path)], str(tmp_path))
        _git(["remote", "add", "upstream", str(remote_path)], str(kb_path))

        result = export_svc.push_kb("test-kb", remote="upstream")
        assert result["success"]

    def test_push_not_git_repo_raises(self, tmp_path):
        """push_kb on a non-git KB should raise PyriteError."""
        kb_path = tmp_path / "plain-kb"
        kb_path.mkdir()

        kb = KBConfig(name="plain", path=kb_path, kb_type="generic")
        config = PyriteConfig(
            knowledge_bases=[kb],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        db = PyriteDB(tmp_path / "index.db")
        export_svc = ExportService(config, db)

        try:
            with pytest.raises(PyriteError, match="not in a git repository"):
                export_svc.push_kb("plain")
        finally:
            db.close()
