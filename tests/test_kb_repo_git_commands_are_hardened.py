"""Git commands Pyrite runs in a KB repository never run that repository's programs.

Property: a repository's own config (``core.fsmonitor``) and hooks
(``.git/hooks``, ``core.hooksPath``) execute nothing when Pyrite runs git in it
-- through every entry point that starts a git process, found by search and
checked structurally so a new call site cannot skip the rule.
"""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest

from pyrite.services.git_service import GitService

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git(cwd: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env=env,
        timeout=60,
    )


@pytest.fixture
def booby_trapped(tmp_path):
    """A repository whose own config and hooks would write marker files."""
    repo = tmp_path / "kb"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.md").write_text("a\n")
    _git(repo, "add", "a.md")
    _git(repo, "commit", "-q", "-m", "init")

    markers = tmp_path / "markers"
    markers.mkdir()
    fsmonitor = tmp_path / "fsmonitor.sh"
    fsmonitor.write_text(f"#!/bin/sh\ntouch {markers}/fsmonitor\nexit 1\n")
    fsmonitor.chmod(0o755)
    _git(repo, "config", "core.fsmonitor", str(fsmonitor))

    hooks = tmp_path / "hooks"
    hooks.mkdir()
    for name in (
        "pre-commit",
        "post-commit",
        "commit-msg",
        "post-checkout",
        "reference-transaction",
    ):
        hook = hooks / name
        hook.write_text(f"#!/bin/sh\ntouch {markers}/{name}\n")
        hook.chmod(0o755)
    _git(repo, "config", "core.hooksPath", str(hooks))
    legacy = repo / ".git" / "hooks" / "pre-commit"
    legacy.write_text(f"#!/bin/sh\ntouch {markers}/legacy-pre-commit\n")
    legacy.chmod(0o755)
    return repo, markers


def _fired(markers: Path) -> list[str]:
    return sorted(p.name for p in markers.iterdir())


def test_status_runs_no_fsmonitor(booby_trapped):
    repo, markers = booby_trapped
    (repo / "b.md").write_text("b\n")
    assert GitService.get_status(repo)["untracked"] == ["b.md"]
    assert _fired(markers) == []


def test_commit_runs_no_hook_and_no_fsmonitor(booby_trapped):
    repo, markers = booby_trapped
    (repo / "b.md").write_text("b\n")
    ok, result = GitService.commit(repo, "add b")
    assert ok, result
    assert _fired(markers) == []


def test_the_operators_own_commit_runs_their_hooks_but_not_fsmonitor(booby_trapped):
    """`pyrite kb commit` at the operator's terminal keeps git's commit-hook
    contract (a hook that rejects the commit is the operator's rule); the
    repository's fsmonitor program still does not run."""
    repo, markers = booby_trapped
    (repo / "b.md").write_text("b\n")
    ok, result = GitService.commit(repo, "add b", run_hooks=True)
    assert ok, result
    fired = _fired(markers)
    assert "pre-commit" in fired
    assert "fsmonitor" not in fired


def test_a_remote_callers_commit_path_runs_no_hooks(booby_trapped):
    """The service every REST and MCP commit goes through defaults to no hooks."""
    import inspect

    from pyrite.services.export_service import ExportService

    default = inspect.signature(ExportService.commit_kb).parameters["run_hooks"].default
    assert default is False


@pytest.mark.control(
    reason="cat-file and rev-parse never consult fsmonitor or hooks; kept as a guard against that changing"
)
def test_reading_a_version_runs_nothing(booby_trapped):
    repo, markers = booby_trapped
    assert GitService.show(repo, "a.md") == "a\n"
    assert GitService.get_kb_prefix(repo) == ""
    assert _fired(markers) == []


def test_document_manager_git_rm_runs_nothing(booby_trapped):
    from pyrite.storage.document_manager import DocumentManager

    repo, markers = booby_trapped
    DocumentManager._remove_old_file(object.__new__(DocumentManager), repo / "a.md", repo)
    assert not (repo / "a.md").exists()
    assert _fired(markers) == []


def test_changed_files_listing_runs_nothing(booby_trapped, monkeypatch):
    from pyrite.cli.schema_commands import _get_git_changed_md_files

    repo, markers = booby_trapped
    (repo / "b.md").write_text("b\n")
    monkeypatch.chdir(repo)
    _get_git_changed_md_files(None)
    assert _fired(markers) == []


def test_an_inherited_git_config_environment_cannot_undo_the_hardening(booby_trapped, monkeypatch):
    """GIT_CONFIG_PARAMETERS and GIT_CONFIG_COUNT in Pyrite's own environment
    are dropped, so they can neither re-enable hooks nor add programs."""
    repo, markers = booby_trapped
    hooks = repo.parent / "hooks"
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(hooks))
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", str(repo.parent / "fsmonitor.sh"))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", f"'core.hooksPath'='{hooks}'")
    (repo / "b.md").write_text("b\n")
    GitService.get_status(repo)
    ok, result = GitService.commit(repo, "add b")
    assert ok, result
    assert _fired(markers) == []


def test_diffs_run_no_external_diff_driver_or_textconv(booby_trapped):
    repo, markers = booby_trapped
    driver = repo.parent / "driver.sh"
    driver.write_text(f"#!/bin/sh\ntouch {markers}/ext-diff\n")
    driver.chmod(0o755)
    textconv = repo.parent / "textconv.sh"
    textconv.write_text(f'#!/bin/sh\ntouch {markers}/textconv\ncat "$1"\n')
    textconv.chmod(0o755)
    _git(repo, "config", "diff.external", str(driver))
    _git(repo, "config", "diff.t.textconv", str(textconv))
    (repo / ".gitattributes").write_text("*.md diff=t\n")
    _git(repo, "checkout", "-q", "-b", "other")
    (repo / "a.md").write_text("changed\n")
    _git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-am", "change")
    _git(repo, "checkout", "-q", "main")
    for fired in markers.iterdir():  # the setup above ran the traps itself
        fired.unlink()
    GitService.diff_branches(repo, "main", "other")
    assert _fired(markers) == []


def test_the_environment_carries_the_settings_as_command_line_config():
    from pyrite.utils.git_hardening import git_env

    env = git_env({"GIT_CONFIG_COUNT": "1"})
    assert "GIT_CONFIG_COUNT" not in env
    params = env["GIT_CONFIG_PARAMETERS"]
    assert "'core.fsmonitor'='false'" in params and "'core.hooksPath'='/dev/null'" in params
    assert "core.hooksPath" not in git_env(hooks=True)["GIT_CONFIG_PARAMETERS"]


# --- every call site, by search ------------------------------------------------

_RUNNERS = {"run", "Popen", "check_output", "check_call", "call"}
_HELPERS = ("_git_env", "subprocess_env", "git_env")


def _git_call_sites() -> list[tuple[str, int, str | None]]:
    """(file, line, env source) of every subprocess call that starts git."""
    sites = []
    for root in ("pyrite", "extensions"):
        for path in (REPO_ROOT / root).rglob("*.py"):
            rel = path.relative_to(REPO_ROOT)
            if "tests" in rel.parts or ".venv" in rel.parts or "node_modules" in rel.parts:
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _RUNNERS
                    and node.args
                ):
                    continue
                first = ast.unparse(node.args[0])
                runs_git = "'git'" in first or "READ_BLOB_ARGV" in first
                # Every subprocess in the git service is a git command, whatever
                # name its argv list goes by.
                if not runs_git and path.name != "git_service.py":
                    continue
                env = next((ast.unparse(k.value) for k in node.keywords if k.arg == "env"), None)
                sites.append((str(rel), node.lineno, env))
    return sites


@pytest.mark.control(reason="the scan itself: it must keep finding the sites the next test judges")
def test_the_search_finds_the_known_git_call_sites():
    files = {f for f, _, _ in _git_call_sites()}
    assert "pyrite/services/git_service.py" in files
    assert "pyrite/storage/document_manager.py" in files
    assert "pyrite/cli/schema_commands.py" in files


def test_every_git_subprocess_takes_the_hardened_environment():
    bad = [
        f"{f}:{line}"
        for f, line, env in _git_call_sites()
        if env is None or not any(h in env for h in _HELPERS)
    ]
    assert bad == []
