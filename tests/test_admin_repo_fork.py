"""``pyrite-admin repo fork`` runs the fork-and-subscribe service and reports its outcome.

The command used to call a method ``RepoService`` does not have (#785). These
tests drive the real admin CLI over a real ``RepoService``: the token is a
saved ``github_auth.yaml``, the config a real ``config.yaml``, and the clone a
real ``git clone`` of a local bare repository (a ``url.insteadOf`` rewrite in a
git config file points the fork's GitHub URL at it). Only the GitHub API call
that makes the fork is replaced (``GitService.fork_repo``).
"""

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import pyrite.config as config_module
from pyrite import admin_cli
from pyrite.config import GitHubAuth, save_config
from pyrite.github_auth import save_github_auth
from pyrite.services.git_service import GitService, _git_env

TOKEN = "fake-token-4f9a"
UPSTREAM_URL = "https://github.com/upstream/project"
FORK_CLONE_URL = "https://github.com/test/project.git"


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        env=_git_env(),
        timeout=120,
    )
    return done.stdout.strip()


def _bare_fork(tmp_path: Path) -> Path:
    """A bare repository holding one KB, standing in for the fork on GitHub."""
    work = tmp_path / "fork-work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    (work / "kb.yaml").write_text("name: forked-kb\nkb_type: generic\n")
    (work / "note.md").write_text("---\nid: note\ntype: note\ntitle: A note\n---\nbody\n")
    _git(work, "add", "kb.yaml", "note.md")
    _git(work, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "init")
    bare = tmp_path / "fork.git"
    _git(tmp_path, "clone", "-q", "--bare", str(work), str(bare))
    return bare


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A config directory with a saved token and an empty config, and git
    told to fetch the fork's GitHub URL (token included) from a local bare repo."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_dir / "config.yaml")
    monkeypatch.setenv("PYRITE_DATA_DIR", str(config_dir))
    config = config_module.load_config()
    save_config(config)
    save_github_auth(GitHubAuth(access_token=TOKEN))

    bare = _bare_fork(tmp_path)
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(
        f'[url "{bare}"]\n\tinsteadOf = https://oauth2:{TOKEN}@github.com/test/project.git\n'
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    return config


def _fork_api(monkeypatch, *, success: bool):
    calls = []

    def fork_repo(owner, repo, token):
        calls.append((owner, repo, token))
        if not success:
            return False, {"error": "fork refused"}
        return True, {"clone_url": FORK_CLONE_URL, "full_name": "test/project"}

    monkeypatch.setattr(GitService, "fork_repo", staticmethod(fork_repo))
    return calls


def test_admin_fork_clones_the_fork_and_links_upstream(world, monkeypatch):
    calls = _fork_api(monkeypatch, success=True)

    result = CliRunner().invoke(admin_cli.app, ["repo", "fork", UPSTREAM_URL])

    assert result.exit_code == 0, result.output
    assert calls == [("upstream", "project", TOKEN)]
    assert "Forked and cloned: test/project" in result.output
    assert TOKEN not in result.output
    clone = world.settings.workspace_path / "test" / "project"
    assert (clone / "note.md").is_file()
    assert _git(clone, "remote", "get-url", "upstream") == UPSTREAM_URL
    saved = config_module.load_config()
    assert [kb.name for kb in saved.knowledge_bases] == ["forked-kb"]


def test_admin_fork_failure_exits_1_and_clones_nothing(world, monkeypatch):
    calls = _fork_api(monkeypatch, success=False)

    result = CliRunner().invoke(admin_cli.app, ["repo", "fork", UPSTREAM_URL])

    assert result.exit_code == 1, result.output
    assert calls == [("upstream", "project", TOKEN)]
    assert "fork refused" in result.output
    assert "Forked and cloned" not in result.output
    assert not (world.settings.workspace_path / "test").exists()
    assert config_module.load_config().knowledge_bases == []
