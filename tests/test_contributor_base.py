"""Contributor scripts agree on the project's integration ref (#665)."""

import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(SCRIPTS / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.delenv("PYRITE_BASE", raising=False)
    git(tmp_path, "init", "-q", "-b", "dev")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    (tmp_path / "file").write_text("base")
    git(tmp_path, "add", "file")
    git(tmp_path, "commit", "-qm", "base")
    git(tmp_path, "update-ref", "refs/remotes/origin/dev", "HEAD")
    git(tmp_path, "update-ref", "refs/remotes/upstream/dev", "HEAD")
    return tmp_path


@pytest.mark.parametrize("script", ["test-affected", "verify_red_ci.py"])
def test_fork_compares_to_upstream(script, repo):
    module = load(script)
    result = (
        module._default_base(repo, None)
        if script == "test-affected"
        else module._default_base(repo)
    )
    assert result == "upstream/dev"


def test_default_changed_files_uses_project_not_fork_tip(repo):
    git(repo, "checkout", "-qb", "feature")
    (repo / "file").write_text("changed")
    git(repo, "commit", "-qam", "feature")
    # A fork tip can include the feature while the project still does not.
    git(repo, "update-ref", "refs/remotes/origin/dev", "HEAD")
    assert load("test-affected").changed_files(repo, committed_only=True) == ["file"]


@pytest.mark.parametrize("script", ["test-affected", "verify_red_ci.py"])
def test_environment_base_overrides_remote(script, repo, monkeypatch):
    monkeypatch.setenv("PYRITE_BASE", "dev")
    module = load(script)
    result = (
        module._default_base(repo, None)
        if script == "test-affected"
        else module._default_base(repo)
    )
    assert result == "dev"


def test_explicit_invalid_origin_ref_never_falls_back(repo):
    git(repo, "update-ref", "-d", "refs/remotes/origin/dev")
    module = load("test-affected")
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        module._default_base(repo, "origin/dev")


@pytest.mark.parametrize("script", ["test-affected", "verify_red_ci.py"])
def test_missing_base_has_actionable_message(script, repo):
    git(repo, "checkout", "-qb", "feature")
    git(repo, "branch", "-D", "dev")
    git(repo, "update-ref", "-d", "refs/remotes/origin/dev")
    git(repo, "update-ref", "-d", "refs/remotes/upstream/dev")
    args = ["--root", str(repo), "--list"] if script == "test-affected" else []
    env = dict(os.environ)
    env.pop("PYRITE_PUSH_FULL", None)
    env.pop("PRE_COMMIT_TO_REF", None)
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert out.returncode != 0
    assert "--base" in out.stderr
    assert "git remote add upstream" in out.stderr


@pytest.mark.parametrize(("remaining", "expected"), [(["origin"], "origin/dev"), ([], "dev")])
def test_resolver_fallbacks(repo, remaining, expected):
    helper = load("base_ref.py")
    git(repo, "update-ref", "-d", "refs/remotes/upstream/dev")
    if not remaining:
        git(repo, "update-ref", "-d", "refs/remotes/origin/dev")
    assert helper.resolve_base(repo) == expected


def test_explicit_commit_wins_over_environment_and_needs_no_remote(repo, monkeypatch):
    helper = load("base_ref.py")
    monkeypatch.setenv("PYRITE_BASE", "missing")
    assert helper.resolve_base(repo, "HEAD", fetch=True) == "HEAD"


def test_invalid_environment_base_fails_closed(repo, monkeypatch):
    helper = load("base_ref.py")
    monkeypatch.setenv("PYRITE_BASE", "missing")
    with pytest.raises(helper.BaseRefError, match="missing"):
        helper.resolve_base(repo)


def test_offline_fetch_keeps_existing_upstream_and_reports_it(repo, capsys):
    helper = load("base_ref.py")
    git(repo, "remote", "add", "upstream", str(repo / "missing.git"))
    assert helper.resolve_base(repo, fetch=True) == "upstream/dev"
    assert "could not fetch upstream/dev" in capsys.readouterr().err


def test_detached_head_still_uses_project_dev(repo):
    helper = load("base_ref.py")
    git(repo, "checkout", "--detach", "HEAD")
    assert helper.resolve_base(repo) == "upstream/dev"


@pytest.mark.skipif(
    os.name == "nt" or not shutil.which("bash"), reason="POSIX Bash workflow; Linux CI exercises it"
)
def test_worktree_explicit_start_without_remote_and_hooks_outlive_it(repo):
    scripts = repo / "scripts"
    scripts.mkdir()
    for name in ["base_ref.py", "new-worktree.sh"]:
        shutil.copyfile(SCRIPTS / name, scripts / name)
        (scripts / name).chmod(0o755)
    # Only expensive installs are stubbed: real git worktree creation and
    # the script's setup/hook routing are exercised.
    (scripts / "setup-checkout.sh").write_text(
        '#!/bin/sh\nset -eu\nd="$1"\n'
        'echo "$d" >> "$(dirname "$0")/calls"\n'
        'mkdir -p "$d/.venv/bin"\n'
        'printf "#!/bin/sh\\nexit 0\\n" > "$d/.venv/bin/pre-commit"\n'
        'chmod +x "$d/.venv/bin/pre-commit"\n'
    )
    (scripts / "setup-checkout.sh").chmod(0o755)
    git(repo, "add", "scripts")
    git(repo, "commit", "-qm", "scripts")
    out = subprocess.run(
        ["bash", str(scripts / "new-worktree.sh"), "fix/check", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    worktree = repo.parent / "pyrite-wt/fix-check"
    assert git(worktree, "rev-parse", "HEAD").stdout == git(repo, "rev-parse", "HEAD").stdout
    assert (scripts / "calls").read_text().splitlines() == [str(repo), str(worktree)]
    assert "hooks installed from this worktree" not in out.stderr
