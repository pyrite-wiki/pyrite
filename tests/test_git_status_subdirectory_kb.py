"""Tests for GitService.get_status and KBService.get_pending_changes on subdirectory KBs (#1223)."""

import subprocess
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.git_service import GitService
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB


@pytest.fixture
def subdir_git_kb(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    # Git init
    subprocess.run(["git", "init"], cwd=str(repo_root), capture_output=True, check=True, timeout=30)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo_root),
        capture_output=True,
        check=True,
        timeout=30,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=str(repo_root),
        capture_output=True,
        check=True,
        timeout=30,
    )

    kb_path = repo_root / "kbs" / "docs"
    kb_path.mkdir(parents=True)

    # Initial files in the subdirectory KB
    (kb_path / "modified.md").write_text(
        "---\ntitle: Mod\n---\n\nOriginal body\n", encoding="utf-8"
    )
    (kb_path / "to_delete.md").write_text(
        "---\ntitle: Del\n---\n\nTo be deleted\n", encoding="utf-8"
    )
    (repo_root / "outside.txt").write_text("root file\n", encoding="utf-8")

    subprocess.run(
        ["git", "add", "."], cwd=str(repo_root), capture_output=True, check=True, timeout=30
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=str(repo_root),
        capture_output=True,
        check=True,
        timeout=30,
    )

    # Now make the changes:
    # 1. Modify
    (kb_path / "modified.md").write_text("---\ntitle: Mod\n---\n\nUpdated body\n", encoding="utf-8")
    # 2. Add
    (kb_path / "created.md").write_text("---\ntitle: Created\n---\n\nNew body\n", encoding="utf-8")
    # 3. Delete
    (kb_path / "to_delete.md").unlink()
    # 4. Outside modification
    (repo_root / "outside.txt").write_text("root file edited\n", encoding="utf-8")

    kb = KBConfig(name="docs", path=kb_path, kb_type="generic")
    config = PyriteConfig(
        knowledge_bases=[kb],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(tmp_path / "index.db")
    svc = KBService(config, db)

    yield {"kb_path": kb_path, "repo_root": repo_root, "svc": svc}
    db.close()


def test_git_status_and_pending_changes_for_subdirectory_kb(subdir_git_kb):
    kb_path = subdir_git_kb["kb_path"]
    svc = subdir_git_kb["svc"]

    status = GitService.get_status(kb_path)
    assert not status["clean"]
    assert "modified.md" in status["unstaged"]
    assert "to_delete.md" in status["unstaged"]
    assert "created.md" in status["untracked"]
    assert "outside.txt" not in status["unstaged"]

    pending = svc.get_pending_changes("docs")
    assert pending["summary"]["total"] == 3
    assert pending["summary"]["modified"] == 1
    assert pending["summary"]["created"] == 1
    assert pending["summary"]["deleted"] == 1

    by_type = {c["change_type"]: c for c in pending["changes"]}
    assert "Updated body" in (by_type["modified"]["current_body"] or "")
    assert "New body" in (by_type["created"]["current_body"] or "")
    assert by_type["deleted"]["current_body"] is None
