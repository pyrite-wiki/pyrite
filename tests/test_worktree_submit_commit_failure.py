"""A real REST submission must not claim an uncommitted edit was submitted."""

from pathlib import Path
import subprocess

import pytest

from pyrite.config import AuthConfig, KBConfig
from pyrite.services.worktree_service import WorktreeService
from tests._world import build_world
from tests.auth_seed import SESSION_COOKIE, seed_and_sign_in, seed_user, sign_in
from tests.test_worktree_service import _clean_git_env, _init_git_repo


@pytest.fixture
def worktree_world(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-config"))
    repo = tmp_path / "repo"
    kb = repo / "kb"
    kb.mkdir(parents=True)
    (kb / "kb.yaml").write_text("name: test-kb\ntypes: {}\n", encoding="utf-8")
    _init_git_repo(repo)
    world = build_world(
        tmp_path / "world",
        kbs=[KBConfig(name="test-kb", path=kb, kb_type="generic")],
        settings={"auth": AuthConfig(enabled=True)},
    )
    try:
        seed_user(world.db, "operator", role="admin")
        user = seed_and_sign_in(world.client, "alice", role="write")
        service = WorktreeService(world.config, world.db)
        wt = service.ensure_worktree("test-kb", user["id"], "alice")
        yield world, repo, service, wt
    finally:
        world.close()


def _git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=_clean_git_env(),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()


@pytest.mark.parametrize("identity", [False, True])
def test_rest_submit_and_merge_report_the_real_commit_outcome(worktree_world, identity):
    world, repo, service, wt = worktree_world
    edited = Path(wt.worktree_path) / "kb" / "alice-note.md"
    text = "---\nid: alice-note\ntitle: Alice note\ntype: note\n---\nKeep this edit\n"
    edited.write_text(text, encoding="utf-8")
    if not identity:
        _git(repo, "config", "--unset", "user.name")
        _git(repo, "config", "--unset", "user.email")
        _git(repo, "config", "user.useConfigOnly", "true")
    response = world.client.post("/api/worktree/submit", json={"kb": "test-kb"})
    if identity:
        assert response.status_code == 200, response.text
        assert response.json()["submitted"] is True
        assert service.get_worktree("test-kb", wt.user_id).status == "submitted"
    else:
        assert response.status_code == 500, response.text
        assert response.json()["detail"]["code"] == "STORAGE_ERROR"
        assert service.get_worktree("test-kb", wt.user_id).status == "active"
        assert edited.read_text(encoding="utf-8") == text
        assert "alice-note.md" in _git(Path(wt.worktree_path), "diff", "--cached", "--name-only")
    world.client.cookies.set(SESSION_COOKIE, sign_in(world.app, "operator"))
    merged = world.client.post("/api/admin/merge-queue/alice/merge", json={"kb": "test-kb"})
    if identity:
        assert merged.status_code == 200, merged.text
        assert merged.json()["merged"] is True
        assert (repo / "kb" / "alice-note.md").read_text(encoding="utf-8") == text
    else:
        assert merged.status_code == 400, merged.text
        assert "not 'submitted'" in merged.json()["detail"]
        assert not (repo / "kb" / "alice-note.md").exists()


def test_rest_merge_does_not_report_an_empty_branch_as_merged(worktree_world):
    world, _, service, wt = worktree_world
    submitted = world.client.post("/api/worktree/submit", json={"kb": "test-kb"})
    assert submitted.status_code == 200, submitted.text
    world.client.cookies.set(SESSION_COOKIE, sign_in(world.app, "operator"))
    response = world.client.post("/api/admin/merge-queue/alice/merge", json={"kb": "test-kb"})
    assert response.status_code == 200, response.text
    assert response.json()["merged"] is False
    assert service.get_worktree("test-kb", wt.user_id).status == "submitted"

    assert "reject" in response.json()["message"].lower()
