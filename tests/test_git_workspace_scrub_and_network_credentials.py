"""Two properties of the git commands Pyrite runs for workspaces and callers.

1. The workspace scrub (``GitService.scrub_remote_credentials``, run at every
   start of the REST server, the MCP server and the repo commands) changes
   only a repository whose top level is the registered workspace. A folder
   with no repository of its own is left alone; the enclosing repository git
   would find by walking up is never touched. A rewrite never leaves a remote
   with no URL.

2. A git network operation made for a network caller authenticates with that
   caller's token or with nothing: the server account's configured credential
   helpers are reset for it, whatever the host and whether or not a token is
   given. The local operator at the CLI (``acting.is_local_process``) keeps
   their own helpers.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pyrite.services import git_service
from pyrite.services.git_service import GitService, _git_env

DIRTY = "https://someone:tok3n@github.com/o/r.git"
CLEAN = "https://github.com/o/r.git"


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=path, check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()


def local_process(value: bool):
    """``pyrite.services.acting.local_process``, imported when used. Where the
    switch does not exist (before it was added) nothing was ever bound, which
    is the network caller's case, and git ran with the account's helpers
    unless a token was offered: a no-op binding states that tree truly."""
    try:
        from pyrite.services.acting import local_process as bind
    except ImportError:
        import contextlib

        return contextlib.nullcontext()
    return bind(value)


def _repo(path: Path, url: str = DIRTY) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "--local", "remote.origin.url", url)
    return path


# -- 1. the scrub -------------------------------------------------------------------


def test_a_workspace_nested_in_another_repo_leaves_the_outer_remote_alone(tmp_path):
    outer = _repo(tmp_path / "outer")
    workspace = outer / "workspaces" / "ws"
    workspace.mkdir(parents=True)
    assert GitService.scrub_remote_credentials(workspace) == 0
    assert _git(outer, "config", "--local", "remote.origin.url") == DIRTY


@pytest.mark.control(reason="the scrub still cleans the workspace's own repository")
def test_the_workspaces_own_repository_is_scrubbed(tmp_path):
    ws = _repo(tmp_path / "ws")
    assert GitService.scrub_remote_credentials(ws) == 1
    assert _git(ws, "config", "--local", "remote.origin.url") == CLEAN


def test_a_failed_rewrite_leaves_the_remote_with_a_url(tmp_path, monkeypatch):
    """Any second step of the rewrite failing (here every ``--add``) leaves
    the old URL or the new one, never none."""
    ws = _repo(tmp_path / "ws")
    real = subprocess.run

    def run(cmd, *a, **k):
        if "--add" in cmd:
            raise subprocess.CalledProcessError(1, cmd)
        return real(cmd, *a, **k)

    monkeypatch.setattr(git_service.subprocess, "run", run)
    GitService.scrub_remote_credentials(ws)
    monkeypatch.undo()
    assert _git(ws, "config", "--local", "--get-all", "remote.origin.url") in (DIRTY, CLEAN)


# -- 1b. every start runs the scrub ----------------------------------------------------


@pytest.fixture
def registered_workspace(tmp_path, monkeypatch):
    """A world with one registered workspace whose remote carries a token."""
    from pyrite.config import KBConfig
    from tests._world import build_world

    w = build_world(
        tmp_path / "world", kbs=[KBConfig(name="k", path=tmp_path / "k", kb_type="generic")]
    )
    ws = _repo(tmp_path / "workspaces" / "o" / "r")
    repo_id = w.db.register_repo(name="o/r", local_path=str(ws), remote_url=CLEAN)
    assert repo_id
    yield w, ws
    w.close()


@pytest.mark.control(reason="this entry point already ran the scrub; pinned beside the MCP start")
def test_the_rest_server_start_scrubs_the_workspace(registered_workspace):
    """The app's own startup hook (``api.create_app``), run as the server runs it."""
    import asyncio

    w, ws = registered_workspace
    [hook] = [h for h in w.app.router.on_startup if h.__name__ == "_scrub_workspace_remotes"]
    asyncio.run(hook())
    assert _git(ws, "config", "--local", "remote.origin.url") == CLEAN


def test_the_mcp_server_start_scrubs_the_workspace(registered_workspace):
    w, ws = registered_workspace
    w.mcp("read")
    assert _git(ws, "config", "--local", "remote.origin.url") == CLEAN


@pytest.mark.control(reason="this entry point already ran the scrub; pinned beside the MCP start")
def test_the_repo_commands_scrub_the_workspace(registered_workspace):
    w, ws = registered_workspace
    from pyrite.cli import repo_commands

    with w.pointed():
        repo_commands._get_db_and_services()[1].close()
    assert _git(ws, "config", "--local", "remote.origin.url") == CLEAN


# -- 2. whose credential helpers ------------------------------------------------------


@pytest.fixture
def server_account_helper(tmp_path, monkeypatch):
    """The server account's global git config has a helper that answers any
    prompt with a password: ``FROM-THE-SERVER-ACCOUNT``."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        "[credential]\n"
        '\thelper = "!f() { echo username=server; echo password=FROM-THE-SERVER-ACCOUNT; }; f"\n'
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _fill(creds, url: str) -> str:
    """What git's credential machinery answers for ``url`` under ``creds``:
    the same lookup a clone, pull or push makes."""
    host = url.split("/")[2]
    env = _git_env({**git_service._NETWORK_GUARD_ENV, **creds.env}, config=creds.config)
    r = subprocess.run(
        ["git", "credential", "fill"],
        input=f"protocol=https\nhost={host}\n\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    return r.stdout


@pytest.mark.parametrize(
    ("token", "url"),
    [
        (None, "https://github.com/o/r.git"),
        (None, "https://git.example.org/o/r.git"),
        ("caller-token", "https://git.example.org/o/r.git"),
    ],
    ids=["no-token", "no-token-other-host", "token-other-host"],
)
def test_a_network_callers_command_never_gets_the_server_accounts_credential(
    server_account_helper, token, url
):
    with local_process(False):
        creds = GitService._credentials(token, [url])
    assert "FROM-THE-SERVER-ACCOUNT" not in _fill(creds, url)


@pytest.mark.control(reason="a caller's own token on GitHub is what it authenticates with")
def test_a_network_callers_token_is_its_credential(server_account_helper):
    url = "https://github.com/o/r.git"
    with local_process(False):
        creds = GitService._credentials("caller-token", [url])
    out = _fill(creds, url)
    assert "caller-token" in out and "FROM-THE-SERVER-ACCOUNT" not in out


@pytest.mark.control(reason="the operator at the CLI keeps their helpers, as before")
def test_the_operator_at_the_cli_keeps_their_own_helpers(server_account_helper):
    url = "https://github.com/o/r.git"
    with local_process(True):
        creds = GitService._credentials(None, [url])
    assert "FROM-THE-SERVER-ACCOUNT" in _fill(creds, url)


@pytest.mark.parametrize("op", ["clone", "pull", "push"])
def test_clone_pull_and_push_for_a_network_caller_reset_the_helpers(tmp_path, monkeypatch, op):
    """Each network command carries the reset (``credential.helper=``) in the
    config git reads last, with no token given."""
    seen: list[dict] = []
    real = subprocess.run

    def run(cmd, *a, **k):
        if any(x in cmd for x in ("clone", "pull", "push")) and "config" not in cmd:
            seen.append(k.get("env") or {})
            return subprocess.CompletedProcess(cmd, 1, "", "fatal: stopped by the test")
        return real(cmd, *a, **k)

    ws = _repo(tmp_path / "ws", CLEAN)
    _git(
        ws, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "0"
    )
    monkeypatch.setattr(git_service.subprocess, "run", run)
    with local_process(False):
        if op == "clone":
            GitService.clone(CLEAN, tmp_path / "dest", branch="main")
        elif op == "pull":
            GitService.pull(ws)
        else:
            GitService.push(ws, branch="main")
    assert seen, "the network command did not run"
    params = seen[-1].get("GIT_CONFIG_PARAMETERS", "")
    assert "'credential.helper'=''" in params, params


def test_a_workspace_path_spelled_in_another_case_is_still_scrubbed(tmp_path):
    """On a case-insensitive filesystem (macOS by default) the registered path
    may differ in case from what git reports; the same folder is the same
    repository (``os.path.samefile``)."""
    ws = _repo(tmp_path / "Workspace")
    other_case = Path(str(ws).replace("Workspace", "wORKSPACE"))
    if not other_case.exists():
        pytest.skip("the filesystem here is case-sensitive")
    assert GitService.scrub_remote_credentials(other_case) == 1
    assert _git(ws, "config", "--local", "remote.origin.url") == CLEAN
