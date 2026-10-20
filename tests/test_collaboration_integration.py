"""Integration tests for Phase 7 collaboration features."""

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models.core_types import PersonEntry
from pyrite.services.git_service import GitService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository


def _git(args, cwd):
    """Run a git command isolated from any parent repo (e.g. during pre-commit)."""
    env = os.environ.copy()
    # Prevent inheriting the parent repo's git state or author overrides
    for var in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "GIT_COMMITTER_DATE",
    ):
        env.pop(var, None)
    return subprocess.run(args, cwd=str(cwd), capture_output=True, env=env, timeout=120)


class TestIndexWithAttribution:
    """Integration tests for index_with_attribution."""

    @pytest.fixture(autouse=True)
    def _clean_git_env(self, monkeypatch):
        """Remove parent-repo git env vars set by pre-commit hooks."""
        for var in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_AUTHOR_DATE",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
            "GIT_COMMITTER_DATE",
        ):
            monkeypatch.delenv(var, raising=False)

    @pytest.fixture
    def git_kb(self):
        """Create a temp git repo with a KB and some commits."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            kb_path = tmpdir / "research-kb"
            kb_path.mkdir()
            actors_path = kb_path / "actors"
            actors_path.mkdir()

            # Init git repo (isolated from parent repo)
            _git(["git", "init"], kb_path)
            _git(["git", "config", "user.name", "Test User"], kb_path)
            _git(["git", "config", "user.email", "test@example.com"], kb_path)

            # Create kb.yaml
            (kb_path / "kb.yaml").write_text(
                "name: test-research\nkb_type: research\ndescription: Test\n"
            )
            _git(["git", "add", "kb.yaml"], kb_path)
            _git(["git", "commit", "-m", "Add kb.yaml"], kb_path)

            # Create config
            kb_config = KBConfig(
                name="test-research",
                path=kb_path,
                kb_type=KBType.RESEARCH,
                description="Test KB",
            )

            # Create a research entry and commit
            repo = KBRepository(kb_config)
            import re as _re

            entry_id = _re.sub(r"[^a-z0-9]+", "-", "Alice".lower()).strip("-")
            entry = PersonEntry(id=entry_id, title="Alice", role="researcher", importance=5)
            entry.body = "Alice is a researcher."
            entry.tags = ["test"]
            repo.save(entry)

            _git(["git", "add", "."], kb_path)
            _git(["git", "commit", "-m", "Add Alice actor"], kb_path)

            # Create DB
            db_path = tmpdir / "index.db"
            db = PyriteDB(db_path)
            config = PyriteConfig(
                knowledge_bases=[kb_config],
                settings=Settings(index_path=db_path),
            )

            yield {
                "db": db,
                "config": config,
                "kb_config": kb_config,
                "kb_path": kb_path,
                "entry_id": entry.id,
            }

            db.close()

    def test_index_with_attribution_populates_created_by(self, git_kb):
        """index_with_attribution sets created_by from git log."""
        index_mgr = IndexManager(git_kb["db"], git_kb["config"])
        git_service = GitService()

        count = index_mgr.index_with_attribution("test-research", git_service)
        assert count >= 1

        entry = git_kb["db"].get_entry(git_kb["entry_id"], "test-research")
        assert entry is not None
        assert entry["created_by"] == "Test User"
        assert entry["modified_by"] == "Test User"

    def test_index_with_attribution_populates_entry_version(self, git_kb):
        """index_with_attribution creates entry_version records."""
        index_mgr = IndexManager(git_kb["db"], git_kb["config"])
        git_service = GitService()

        index_mgr.index_with_attribution("test-research", git_service)

        versions = git_kb["db"].get_entry_versions(git_kb["entry_id"], "test-research")
        assert len(versions) >= 1
        assert versions[0]["author_name"] == "Test User"
        assert versions[0]["author_email"] == "test@example.com"

    def test_regular_index_still_works(self, git_kb):
        """Regular index_kb works without attribution (backward compat)."""
        index_mgr = IndexManager(git_kb["db"], git_kb["config"])

        count = index_mgr.index_kb("test-research")
        assert count >= 1

        entry = git_kb["db"].get_entry(git_kb["entry_id"], "test-research")
        assert entry is not None
        assert entry["created_by"] is None  # No attribution
        assert entry["modified_by"] is None


class TestEndToEndWorkflow:
    """End-to-end workflow tests."""

    def test_full_collaboration_workflow(self):
        """Test: register user -> register repo -> add workspace -> index -> query."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "index.db"
            db = PyriteDB(db_path)

            # 1. User management
            user = db.upsert_user("alice", 100, "Alice")
            assert user["github_login"] == "alice"

            # 2. Repo management
            repo = db.register_repo(
                "org/research",
                str(Path(tmpdir) / "research"),
                remote_url="https://github.com/org/research",
                owner="org",
            )

            # 3. Workspace membership
            db.add_workspace_repo(user["id"], repo["id"], "subscriber")
            workspace = db.get_workspace_repos(user["id"])
            assert len(workspace) == 1
            assert workspace[0]["role"] == "subscriber"

            # 4. KB registration with repo association
            db.register_kb("test-kb", KBType.RESEARCH, str(Path(tmpdir) / "research"))
            db._raw_conn.execute(
                "UPDATE kb SET repo_id = ? WHERE name = ?",
                (repo["id"], "test-kb"),
            )
            db._raw_conn.commit()

            # 5. Entry with attribution
            db.upsert_entry(
                {
                    "id": "entry-1",
                    "kb_name": "test-kb",
                    "entry_type": "actor",
                    "title": "Test Actor",
                    "body": "Test body",
                    "created_by": "alice",
                    "modified_by": "alice",
                }
            )

            # 6. Entry version
            db.upsert_entry_version(
                entry_id="entry-1",
                kb_name="test-kb",
                commit_hash="a" * 40,
                author_name="Alice",
                author_email="alice@example.com",
                commit_date="2025-01-20T10:00:00",
                message="Add actor",
                change_type="created",
                author_github_login="alice",
            )

            # 7. Query everything
            entry = db.get_entry("entry-1", "test-kb")
            assert entry["created_by"] == "alice"

            versions = db.get_entry_versions("entry-1", "test-kb")
            assert len(versions) == 1

            contributors = db.get_contributors("test-kb")
            assert len(contributors) == 1
            assert contributors[0]["author_github_login"] == "alice"

            # 8. Sync tracking
            db.update_repo_synced("org/research", "abc123")
            repo = db.get_repo(name="org/research")
            assert repo["last_synced_commit"] == "abc123"

            db.close()

    def test_backward_compat_no_auth(self):
        """Test that everything works without GitHub auth (local user)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "index.db"
            db = PyriteDB(db_path)

            # Local user should exist
            local = db.get_local_user()
            assert local["github_login"] == "local"

            # Entries without attribution work
            db.register_kb("test-kb", KBType.RESEARCH, "/tmp/test")
            db.upsert_entry(
                {
                    "id": "entry-1",
                    "kb_name": "test-kb",
                    "entry_type": "actor",
                    "title": "Test",
                    "body": "Body",
                }
            )

            entry = db.get_entry("entry-1", "test-kb")
            assert entry is not None
            assert entry["created_by"] is None
            assert entry["modified_by"] is None

            # Search still works
            results = db.search("Test")
            assert len(results) == 1

            db.close()
