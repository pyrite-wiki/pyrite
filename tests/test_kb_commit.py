"""Tests for KB commit and push operations (#55).

Tests git commit/push across all three interfaces: service layer, MCP, REST API.
"""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import KBNotFoundError, PyriteError
from pyrite.services.export_service import ExportService
from pyrite.services.git_service import GitService
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager


@pytest.fixture
def git_kb(tmp_path):
    """Create a git-backed KB with sample entries."""
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()

    # Init git repo
    subprocess.run(["git", "init"], cwd=str(kb_path), capture_output=True, timeout=120)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(kb_path),
        capture_output=True,
        timeout=120,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=str(kb_path),
        capture_output=True,
        timeout=120,
    )

    # Create initial commit
    (kb_path / "README.md").write_text("# Test KB\n")
    subprocess.run(["git", "add", "."], cwd=str(kb_path), capture_output=True, timeout=120)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=str(kb_path),
        capture_output=True,
        timeout=120,
    )

    kb = KBConfig(name="test-kb", path=kb_path, kb_type="generic")
    config = PyriteConfig(
        knowledge_bases=[kb],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(tmp_path / "index.db")
    svc = KBService(config, db)
    export_svc = ExportService(config, db)

    yield {
        "kb_path": kb_path,
        "kb": kb,
        "config": config,
        "db": db,
        "svc": svc,
        "export_svc": export_svc,
    }
    db.close()


@pytest.fixture
def non_git_kb(tmp_path):
    """Create a KB without git."""
    kb_path = tmp_path / "no-git-kb"
    kb_path.mkdir()

    kb = KBConfig(name="no-git", path=kb_path, kb_type="generic")
    config = PyriteConfig(
        knowledge_bases=[kb],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(tmp_path / "index.db")
    svc = KBService(config, db)
    export_svc = ExportService(config, db)

    yield {
        "kb_path": kb_path,
        "kb": kb,
        "config": config,
        "db": db,
        "svc": svc,
        "export_svc": export_svc,
    }
    db.close()


# =============================================================================
# GitService tests
# =============================================================================


class TestGitServiceCommit:
    """Test GitService.commit static method."""

    def test_commit_all_changes(self, git_kb):
        kb_path = git_kb["kb_path"]
        # Create an uncommitted file
        (kb_path / "note-1.md").write_text("---\ntitle: Note 1\n---\n\nHello")

        success, result = GitService.commit(kb_path, "Add note 1")
        assert success
        assert "commit_hash" in result
        assert result["files_changed"] >= 1

    def test_commit_specific_paths(self, git_kb):
        kb_path = git_kb["kb_path"]
        (kb_path / "note-a.md").write_text("---\ntitle: A\n---\n\nA")
        (kb_path / "note-b.md").write_text("---\ntitle: B\n---\n\nB")

        success, result = GitService.commit(kb_path, "Add note A only", paths=["note-a.md"])
        assert success
        assert result["files_changed"] >= 1

        # note-b should still be untracked
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(kb_path),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert "note-b.md" in status.stdout

    def test_commit_nothing_to_commit(self, git_kb):
        kb_path = git_kb["kb_path"]
        success, result = GitService.commit(kb_path, "Empty commit")
        assert not success
        assert (
            "nothing" in result.get("error", "").lower()
            or "no changes" in result.get("error", "").lower()
        )

    def test_commit_not_git_repo(self, non_git_kb):
        kb_path = non_git_kb["kb_path"]
        success, result = GitService.commit(kb_path, "Should fail")
        assert not success

    def test_commit_with_sign_off(self, git_kb):
        kb_path = git_kb["kb_path"]
        (kb_path / "signed.md").write_text("---\ntitle: Signed\n---\n\nSigned")
        success, result = GitService.commit(kb_path, "Signed commit", sign_off=True)
        assert success

        # Verify sign-off in commit message
        log = subprocess.run(
            ["git", "log", "-1", "--format=%B"],
            cwd=str(kb_path),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert "Signed-off-by:" in log.stdout


class TestGitServicePush:
    """Test GitService.push static method."""

    def test_push_no_remote(self, git_kb):
        kb_path = git_kb["kb_path"]
        success, msg = GitService.push(kb_path)
        assert not success
        # No remote configured, should fail

    def test_push_with_remote(self, git_kb, tmp_path):
        kb_path = git_kb["kb_path"]
        # Create a bare remote
        remote_path = tmp_path / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", str(remote_path)], capture_output=True, timeout=120
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote_path)],
            cwd=str(kb_path),
            capture_output=True,
            timeout=120,
        )

        success, msg = GitService.push(kb_path)
        assert success


class TestGitServiceStatus:
    """Test GitService.get_status static method."""

    def test_status_clean(self, git_kb):
        kb_path = git_kb["kb_path"]
        status = GitService.get_status(kb_path)
        assert status["clean"]
        assert status["staged"] == []
        assert status["unstaged"] == []
        assert status["untracked"] == []

    def test_status_with_changes(self, git_kb):
        kb_path = git_kb["kb_path"]
        (kb_path / "new.md").write_text("new content")
        (kb_path / "README.md").write_text("# Modified\n")

        status = GitService.get_status(kb_path)
        assert not status["clean"]
        assert "new.md" in status["untracked"]
        assert any("README.md" in f for f in status["unstaged"])


# =============================================================================
# KBService tests
# =============================================================================


class TestExportServiceCommit:
    """Test ExportService.commit_kb method."""

    def test_commit_kb(self, git_kb):
        export_svc = git_kb["export_svc"]
        kb_path = git_kb["kb_path"]
        (kb_path / "entry.md").write_text("---\ntitle: Entry\n---\n\nContent")

        result = export_svc.commit_kb("test-kb", message="Add entry")
        assert result["success"]
        assert result["commit_hash"]
        assert result["files_changed"] >= 1

    def test_commit_kb_records_a_readable_version(self, git_kb):
        """#432: a server write (ExportService.commit_kb, reached from REST
        POST /kbs/{kb}/commit, MCP kb_commit and KBService.publish) records
        a version, readable immediately -- no reindex in between."""
        export_svc = git_kb["export_svc"]
        db = git_kb["db"]
        config = git_kb["config"]
        kb_path = git_kb["kb_path"]

        entry_file = kb_path / "entry-1.md"
        entry_file.write_text("---\nid: entry-1\ntitle: Entry\ntype: note\n---\n\nContent")

        # The entry must be indexed for record_commit to resolve its path
        # to an entry id -- the same way a server write flow indexes on
        # save, before ever calling commit.
        IndexManager(db, config).index_all()

        result = export_svc.commit_kb("test-kb", message="Add entry-1")
        assert result["success"]
        commit_hash = result["commit_hash"]

        from pyrite.services.version_service import VersionService

        version_svc = VersionService(config, db)
        versions = version_svc.get_entry_versions("entry-1", "test-kb")
        assert len(versions) == 1
        assert versions[0]["commit_hash"] == commit_hash

        content = version_svc.get_entry_at_version("entry-1", "test-kb", commit_hash)
        assert content is not None
        assert "Content" in content

    @pytest.mark.control(
        reason="On origin/dev, commit_kb never calls record_commit at all "
        "(that recording is #432's own root feature), so under lock "
        "contention there is no INSERT to fail and nothing to poison the "
        "session -- this passes on the merge base for a reason unrelated "
        "to the rollback fix. Against round 1 of this PR (which added "
        "recording without a rollback) this was red; the mutation check "
        "(removing self.db.session.rollback()) is what proves it is "
        "load-bearing against the code as it stands now."
    )
    def test_commit_kb_recovers_the_session_when_recording_fails_under_lock(self, git_kb, caplog):
        """A concurrent writer (another index sync, another request) can
        hold the sqlite write lock right when record_commit tries to
        insert -- that insert then raises OperationalError, and commit_kb's
        except must roll the ORM session back. Without a rollback, the
        session stays poisoned (PendingRollbackError) for every later call
        on it in the same process -- the MCP server and the CLI both reuse
        one long-lived session (coordinator cold read on #432)."""
        import logging
        import sqlite3

        export_svc = git_kb["export_svc"]
        db = git_kb["db"]
        config = git_kb["config"]
        kb_path = git_kb["kb_path"]
        db_path = config.settings.index_path

        entry_file = kb_path / "entry-1.md"
        entry_file.write_text("---\nid: entry-1\ntitle: Entry\ntype: note\n---\n\nContent")
        IndexManager(db, config).index_all()

        # Hold the write lock from a second connection while commit_kb runs,
        # so record_commit's INSERT hits "database is locked".
        blocker = sqlite3.connect(str(db_path), timeout=0.1)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            with caplog.at_level(logging.WARNING, logger="pyrite.services.export_service"):
                result = export_svc.commit_kb("test-kb", message="Add entry-1")
            # commit_kb must still report the commit succeeded -- the git
            # commit itself is not undone by a recording failure.
            assert result["success"]
            # Structural, not timed: the recording really failed on the lock
            # and went through the recovery branch this test is about.
            assert "Failed to record entry_version rows" in caplog.text
        finally:
            blocker.rollback()
            blocker.close()

        # The session must be usable again -- not raising PendingRollbackError.
        db.get_entry("entry-1", "test-kb")
        db.get_entry_versions("entry-1", "test-kb")
        db.upsert_entry_version(
            entry_id="entry-1",
            kb_name="test-kb",
            commit_hash="0" * 40,
            author_name="x",
            author_email="x@example.com",
            commit_date="2026-01-01T00:00:00+00:00",
        )

    def test_commit_kb_not_found(self, git_kb):
        export_svc = git_kb["export_svc"]
        with pytest.raises(KBNotFoundError):
            export_svc.commit_kb("nonexistent-kb", message="Fail")

    def test_commit_kb_not_git_repo(self, non_git_kb):
        export_svc = non_git_kb["export_svc"]
        (non_git_kb["kb_path"] / "test.md").write_text("test")
        with pytest.raises(PyriteError):
            export_svc.commit_kb("no-git", message="Fail")

    def test_commit_kb_specific_paths(self, git_kb):
        export_svc = git_kb["export_svc"]
        kb_path = git_kb["kb_path"]
        (kb_path / "keep.md").write_text("keep")
        (kb_path / "skip.md").write_text("skip")

        result = export_svc.commit_kb("test-kb", message="Partial", paths=["keep.md"])
        assert result["success"]

        # skip.md should still be uncommitted
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(kb_path),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert "skip.md" in status.stdout

    def test_push_kb(self, git_kb, tmp_path):
        export_svc = git_kb["export_svc"]
        kb_path = git_kb["kb_path"]

        # Create a bare remote
        remote_path = tmp_path / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", str(remote_path)], capture_output=True, timeout=120
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote_path)],
            cwd=str(kb_path),
            capture_output=True,
            timeout=120,
        )

        result = export_svc.push_kb("test-kb")
        assert result["success"]

    def test_push_kb_no_remote(self, git_kb):
        export_svc = git_kb["export_svc"]
        result = export_svc.push_kb("test-kb")
        assert not result["success"]


# =============================================================================
# MCP tool tests
# =============================================================================


class TestMCPCommitTools:
    """Test kb_commit and kb_push MCP tools at admin tier."""

    def test_commit_tool_registered_at_admin(self, git_kb):
        from pyrite.server.mcp_server import PyriteMCPServer

        server = PyriteMCPServer(config=git_kb["config"], tier="admin")
        assert "kb_commit" in server.tools
        assert "kb_push" in server.tools
        server.close()

    def test_commit_tool_not_at_write(self, git_kb):
        from pyrite.server.mcp_server import PyriteMCPServer

        server = PyriteMCPServer(config=git_kb["config"], tier="write")
        assert "kb_commit" not in server.tools
        assert "kb_push" not in server.tools
        server.close()

    def test_commit_tool_not_at_read(self, git_kb):
        from pyrite.server.mcp_server import PyriteMCPServer

        server = PyriteMCPServer(config=git_kb["config"], tier="read")
        assert "kb_commit" not in server.tools
        assert "kb_push" not in server.tools
        server.close()

    def test_commit_tool_handler(self, git_kb):
        from pyrite.server.mcp_server import PyriteMCPServer

        kb_path = git_kb["kb_path"]
        (kb_path / "mcp-entry.md").write_text("---\ntitle: MCP\n---\n\nMCP entry")

        server = PyriteMCPServer(config=git_kb["config"], tier="admin")
        handler = server.tools["kb_commit"]["handler"]
        result = handler({"kb": "test-kb", "message": "MCP commit"})
        assert result.get("success") or result.get("commit_hash")
        server.close()


# =============================================================================
# REST API tests
# =============================================================================


class TestRESTCommitEndpoints:
    """Test REST API commit/push endpoints."""

    def _make_app(self, config, db):
        from pyrite.server.api import create_app, get_config, get_db

        app = create_app(config)
        app.dependency_overrides[get_config] = lambda: config
        app.dependency_overrides[get_db] = lambda: db
        return app

    def test_commit_endpoint(self, git_kb):
        from starlette.testclient import TestClient

        kb_path = git_kb["kb_path"]
        (kb_path / "api-entry.md").write_text("---\ntitle: API\n---\n\nAPI entry")

        app = self._make_app(git_kb["config"], git_kb["db"])
        client = TestClient(app)
        resp = client.post(
            "/api/kbs/test-kb/commit",
            json={"message": "API commit"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"]

    def test_commit_endpoint_records_readable_version(self, git_kb):
        """#432 over HTTP: POST /kbs/{kb}/commit then GET the entry's
        versions and content immediately, no reindex in between."""
        from starlette.testclient import TestClient

        kb_path = git_kb["kb_path"]
        config = git_kb["config"]
        db = git_kb["db"]

        entry_file = kb_path / "entry-1.md"
        entry_file.write_text("---\nid: entry-1\ntitle: API\ntype: note\n---\n\nAPI entry")
        IndexManager(db, config).index_all()

        app = self._make_app(config, db)
        client = TestClient(app)
        resp = client.post(
            "/api/kbs/test-kb/commit",
            json={"message": "API commit"},
        )
        assert resp.status_code == 200
        commit_hash = resp.json()["commit_hash"]

        versions_resp = client.get("/api/entries/entry-1/versions", params={"kb": "test-kb"})
        assert versions_resp.status_code == 200
        versions = versions_resp.json()["versions"]
        assert any(v["commit_hash"] == commit_hash for v in versions), versions

        content_resp = client.get(
            f"/api/entries/entry-1/versions/{commit_hash}", params={"kb": "test-kb"}
        )
        assert content_resp.status_code == 200
        assert "API entry" in content_resp.json()["content"]

    def test_commit_endpoint_kb_not_found(self, git_kb):
        from starlette.testclient import TestClient

        app = self._make_app(git_kb["config"], git_kb["db"])
        client = TestClient(app)
        resp = client.post(
            "/api/kbs/nonexistent/commit",
            json={"message": "Should 404"},
        )
        assert resp.status_code == 404

    def test_push_endpoint_no_remote(self, git_kb):
        from starlette.testclient import TestClient

        app = self._make_app(git_kb["config"], git_kb["db"])
        client = TestClient(app)
        resp = client.post("/api/kbs/test-kb/push")
        assert resp.status_code == 200
        data = resp.json()
        assert not data["success"]
