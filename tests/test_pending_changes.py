"""Tests for pending changes API (review & publish workflow)."""

import shutil
import subprocess

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.git_service import GitService
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git not available",
)


def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120)


@pytest.fixture
def git_kb(tmp_path):
    """Git-backed KB with initial committed entry."""
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()

    _git(["init"], str(kb_path))
    _git(["config", "user.email", "test@pyrite.dev"], str(kb_path))
    _git(["config", "user.name", "Pyrite Test"], str(kb_path))

    # Create and commit an initial entry
    entry_file = kb_path / "hello.md"
    entry_file.write_text(
        "---\nid: hello\ntype: note\ntitle: Hello\ntags: []\n---\n\nOriginal body.\n"
    )
    _git(["add", "."], str(kb_path))
    _git(["commit", "-m", "Initial commit"], str(kb_path))

    kb = KBConfig(name="test-kb", path=kb_path, kb_type="generic")
    config = PyriteConfig(
        knowledge_bases=[kb],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(tmp_path / "index.db")
    db.register_kb("test-kb", "generic", str(kb_path), "Test KB")

    svc = KBService(config, db)
    yield {"kb_path": kb_path, "svc": svc, "config": config, "db": db}
    db.close()


class TestGetPendingChanges:
    """Test KBService.get_pending_changes()."""

    def test_no_changes_returns_empty(self, git_kb):
        result = git_kb["svc"].get_pending_changes("test-kb")
        assert result["changes"] == []
        assert result["summary"]["total"] == 0

    def test_modified_entry_detected(self, git_kb):
        # Modify the existing entry
        entry_file = git_kb["kb_path"] / "hello.md"
        entry_file.write_text(
            "---\nid: hello\ntype: note\ntitle: Hello\ntags: []\n---\n\nUpdated body.\n"
        )

        result = git_kb["svc"].get_pending_changes("test-kb")
        assert result["summary"]["total"] == 1
        assert result["summary"]["modified"] == 1
        changes = result["changes"]
        assert len(changes) == 1
        assert changes[0]["change_type"] == "modified"
        assert changes[0]["title"] == "Hello"
        assert "Original body" in changes[0]["previous_body"]
        assert "Updated body" in changes[0]["current_body"]

    def test_new_entry_detected(self, git_kb):
        # Create a new entry
        new_file = git_kb["kb_path"] / "world.md"
        new_file.write_text(
            "---\nid: world\ntype: note\ntitle: World\ntags: []\n---\n\nNew entry.\n"
        )

        result = git_kb["svc"].get_pending_changes("test-kb")
        assert result["summary"]["total"] == 1
        assert result["summary"]["created"] == 1
        changes = result["changes"]
        assert len(changes) == 1
        assert changes[0]["change_type"] == "created"
        assert changes[0]["title"] == "World"
        assert changes[0]["previous_body"] is None

    def test_deleted_entry_detected(self, git_kb):
        # Delete the entry
        (git_kb["kb_path"] / "hello.md").unlink()

        result = git_kb["svc"].get_pending_changes("test-kb")
        assert result["summary"]["total"] == 1
        assert result["summary"]["deleted"] == 1
        changes = result["changes"]
        assert len(changes) == 1
        assert changes[0]["change_type"] == "deleted"

    def test_multiple_changes(self, git_kb):
        # Modify existing + add new
        entry_file = git_kb["kb_path"] / "hello.md"
        entry_file.write_text(
            "---\nid: hello\ntype: note\ntitle: Hello\ntags: []\n---\n\nChanged.\n"
        )
        new_file = git_kb["kb_path"] / "new.md"
        new_file.write_text("---\nid: new\ntype: note\ntitle: New One\ntags: []\n---\n\nFresh.\n")

        result = git_kb["svc"].get_pending_changes("test-kb")
        assert result["summary"]["total"] == 2
        types = {c["change_type"] for c in result["changes"]}
        assert types == {"modified", "created"}


class TestPublishChanges:
    """Test KBService.publish_changes()."""

    def test_publish_commits_and_reports(self, git_kb):
        # Make a change
        (git_kb["kb_path"] / "hello.md").write_text(
            "---\nid: hello\ntype: note\ntitle: Hello\ntags: []\n---\n\nPublished.\n"
        )

        result = git_kb["svc"].publish_changes("test-kb", summary="Test publish")
        assert result["success"] is True
        assert result["commit_hash"]
        assert result["entries_published"] >= 1

        # Verify git is clean after publish
        status = GitService.get_status(git_kb["kb_path"])
        assert status["clean"] is True

    def test_publish_no_changes(self, git_kb):
        result = git_kb["svc"].publish_changes("test-kb")
        assert result["success"] is True
        assert result["entries_published"] == 0

    def test_publish_records_a_readable_version(self, git_kb):
        """#432: KBService.publish (used by the review/publish workflow)
        goes through ExportService.commit_kb, so it must record a version
        too, readable immediately."""
        from pyrite.services.version_service import VersionService
        from pyrite.storage.index import IndexManager

        IndexManager(git_kb["db"], git_kb["config"]).index_all()

        (git_kb["kb_path"] / "hello.md").write_text(
            "---\nid: hello\ntype: note\ntitle: Hello\ntags: []\n---\n\nPublished.\n"
        )

        result = git_kb["svc"].publish_changes("test-kb", summary="Test publish")
        assert result["success"] is True
        commit_hash = result["commit_hash"]

        version_svc = VersionService(git_kb["config"], git_kb["db"])
        versions = version_svc.get_entry_versions("hello", "test-kb")
        assert any(v["commit_hash"] == commit_hash for v in versions), versions

        content = version_svc.get_entry_at_version("hello", "test-kb", commit_hash)
        assert content is not None
        assert "Published." in content

    def test_publish_auto_generates_message(self, git_kb):
        (git_kb["kb_path"] / "hello.md").write_text(
            "---\nid: hello\ntype: note\ntitle: Hello\ntags: []\n---\n\nAuto msg.\n"
        )
        (git_kb["kb_path"] / "new.md").write_text(
            "---\nid: new\ntype: note\ntitle: New\ntags: []\n---\n\nBody.\n"
        )

        result = git_kb["svc"].publish_changes("test-kb")
        assert result["success"] is True
        # Check the commit message in git log
        log = _git(["log", "-1", "--format=%s"], str(git_kb["kb_path"]))
        assert "Updated 1" in log.stdout or "Created 1" in log.stdout or "Published" in log.stdout

    def test_publish_no_remote_reports_real_git_error(self, git_kb):
        """fail-open-exception-sweep site #3: publish_changes wrapped the
        push in a bare `except Exception: push_error = "No remote
        configured"`. GitService.push() never raises for a real no-remote
        failure -- it captures the subprocess error and returns
        (False, "Push failed: ..."), which push_kb already surfaces via
        push_result["message"]. The try/except's fallback was unreachable
        for the real no-remote case and, if it ever did trigger (e.g. a
        future push_kb change that raises), would mask whatever the actual
        error was behind a generic, wrong label. The real git error must
        surface, not a canned string."""
        (git_kb["kb_path"] / "hello.md").write_text(
            "---\nid: hello\ntype: note\ntitle: Hello\ntags: []\n---\n\nPublished.\n"
        )

        result = git_kb["svc"].publish_changes("test-kb", summary="Test publish")

        assert result["success"] is True  # commit succeeds regardless of push
        assert result["push_error"], "expected a push_error since no remote is configured"
        assert result["push_error"] != "No remote configured", (
            "push_error must be the real git failure message, not the generic "
            f"mislabel that could hide the actual cause: {result['push_error']!r}"
        )
        # The stated intent, now enforced: any canned stand-in fails this.
        # Error redaction (CodeQL #51/#52/#53) must not turn every non-clone
        # git failure into "Git operation failed -- see the server log", which
        # the CLI user -- who *is* the operator -- cannot act on, and which the
        # web UI renders verbatim (web/src/routes/changes/+page.svelte).
        assert "see the server log" not in result["push_error"], (
            f"push_error is a canned string, not git's own words: {result['push_error']!r}"
        )
        # The real cause. A repository with no remotes is detected before git
        # runs (push accepts only a configured remote's name), so the message
        # is Pyrite's, and it names the cause. It must NOT be reported as
        # "Repository not found, or the configured credentials cannot see
        # it" -- false on both halves for a KB with no remote.
        assert "no remotes configured" in result["push_error"], (
            f"expected the no-remote cause: {result['push_error']!r}"
        )
        assert "credentials cannot see it" not in result["push_error"]

    def test_publish_push_raising_surfaces_real_exception(self, git_kb, monkeypatch):
        """The narrower regression: if push_kb ever does raise (a future
        change, a genuinely unexpected error), the message must reflect
        that exception -- not be silently relabeled "No remote
        configured", which would be actively misleading (e.g. an auth
        failure told to the user as a missing-remote problem)."""
        (git_kb["kb_path"] / "hello.md").write_text(
            "---\nid: hello\ntype: note\ntitle: Hello\ntags: []\n---\n\nPublished.\n"
        )

        def _raise(*args, **kwargs):
            raise RuntimeError("simulated auth failure")

        monkeypatch.setattr(git_kb["svc"]._export_svc, "push_kb", _raise)

        result = git_kb["svc"].publish_changes("test-kb", summary="Test publish")

        assert result["success"] is True
        assert "simulated auth failure" in (result["push_error"] or ""), (
            f"expected the real exception message surfaced, got {result['push_error']!r}"
        )
