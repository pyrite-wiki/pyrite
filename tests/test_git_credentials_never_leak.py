"""A GitHub token reaches git only through an inline helper for this one command.

Property: the token is never in argv, never in a remote URL on disk, never in
a log line; clone, pull and push all authenticate; a remote that already
embeds a token is scrubbed on next use.

The tests run real git against a local HTTPS server that answers 401 until it
is sent HTTP Basic credentials, and records what arrived. The server stands in
for GitHub by being added to the GitHub host set for the test.
"""

from __future__ import annotations

import base64
import logging
import os
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from pyrite.services import git_service
from pyrite.services.git_service import GitService

TOKEN = "ghp_SECRETTOKENVALUE0123456789"


class _Recorder(HTTPServer):
    def __init__(self, addr, cert, key):
        super().__init__(addr, _Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        self.socket = ctx.wrap_socket(self.socket, server_side=True)
        self.auth_headers: list[str] = []
        self.extra_headers: list[str] = []


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def _answer(self):
        auth = self.headers.get("Authorization")
        if self.headers.get("X-Account"):
            self.server.extra_headers.append(self.headers["X-Account"])
        if not auth:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="git"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.server.auth_headers.append(auth)
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = do_HEAD = _answer  # noqa: N815


@pytest.fixture
def github_like(tmp_path, monkeypatch):
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key)]
        + ["-out", str(cert), "-days", "2", "-subj", "/CN=127.0.0.1"],
        check=True,
        capture_output=True,
        timeout=60,
    )
    server = _Recorder(("127.0.0.1", 0), str(cert), str(key))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host = f"127.0.0.1:{server.server_address[1]}"
    monkeypatch.setattr(git_service, "_GITHUB_HOSTS", frozenset({"127.0.0.1"}))
    monkeypatch.setenv("GIT_SSL_NO_VERIFY", "1")
    # A user/system-style git config that names a credential helper, as a
    # macOS system config names osxkeychain. It answers `get` with a stale
    # credential and records every call, `store` included.
    helper = tmp_path / "stale-helper.sh"
    helper_log = tmp_path / "helper.log"
    helper.write_text(
        f'#!/bin/sh\necho "$1" >> {helper_log}\n'
        'if [ "$1" = get ]; then echo username=oauth2; echo password=STALE_CREDENTIAL; fi\n'
    )
    helper.chmod(0o755)
    for name, var in (("system", "GIT_CONFIG_SYSTEM"), ("global", "GIT_CONFIG_GLOBAL")):
        conf = tmp_path / f"{name}.gitconfig"
        conf.write_text(f"[credential]\n\thelper = {helper}\n")
        monkeypatch.setenv(var, str(conf))
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)
    server.helper_log = helper_log
    yield server, host
    server.shutdown()
    server.server_close()


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def _spy_argv(monkeypatch) -> list[list[str]]:
    seen: list[list[str]] = []
    real = subprocess.run

    def run(cmd, *a, **kw):
        seen.append([str(c) for c in cmd])
        return real(cmd, *a, **kw)

    monkeypatch.setattr(git_service.subprocess, "run", run)
    return seen


def _local_repo(path: Path, url: str) -> Path:
    path.mkdir()
    for args in (["init", "-q"], ["remote", "add", "origin", url]):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, timeout=60)
    return path


def test_clone_authenticates_through_the_helper_not_the_url(github_like, tmp_path, monkeypatch):
    server, host = github_like
    argv = _spy_argv(monkeypatch)
    GitService.clone_with_code(
        f"https://{host}/o/r.git",
        tmp_path / "ws",
        token=TOKEN,
        allowed_hosts=[host],
    )
    assert _basic("oauth2", TOKEN) in server.auth_headers  # git really sent it
    assert not any(TOKEN in a for cmd in argv for a in cmd)  # never in argv


def test_pull_authenticates(github_like, tmp_path):
    server, host = github_like
    repo = _local_repo(tmp_path / "w", f"https://{host}/o/r.git")
    GitService.pull(repo, token=TOKEN, allowed_hosts=[host])
    assert _basic("oauth2", TOKEN) in server.auth_headers


def test_push_authenticates(github_like, tmp_path):
    server, host = github_like
    repo = _local_repo(tmp_path / "w", f"https://{host}/o/r.git")
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        timeout=60,
    )
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, check=True, timeout=60)
    GitService.push(repo, branch="main", token=TOKEN, allowed_hosts=[host])
    assert _basic("oauth2", TOKEN) in server.auth_headers


def test_pull_scrubs_a_token_already_in_the_remote_url(github_like, tmp_path):
    server, host = github_like
    repo = _local_repo(tmp_path / "w", f"https://oauth2:{TOKEN}@{host}/o/r.git")
    GitService.pull(repo, token=TOKEN, allowed_hosts=[host])
    assert TOKEN not in (repo / ".git" / "config").read_text()
    # the scrubbed remote still names the same repository
    url = subprocess.run(
        ["git", "config", "remote.origin.url"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.strip()
    assert url == f"https://{host}/o/r.git"


def test_push_scrubs_a_token_already_in_the_remote_url(github_like, tmp_path):
    server, host = github_like
    repo = _local_repo(tmp_path / "w", f"https://x:{TOKEN}@{host}/o/r.git")
    GitService.push(repo, branch="main", token=TOKEN, allowed_hosts=[host])
    assert TOKEN not in (repo / ".git" / "config").read_text()


@pytest.mark.control(reason="round 1 already held this; kept as a guard")
def test_a_token_is_not_offered_to_a_host_that_is_not_github(github_like, tmp_path, monkeypatch):
    server, host = github_like
    monkeypatch.setattr(git_service, "_GITHUB_HOSTS", frozenset({"github.com"}))
    repo = _local_repo(tmp_path / "w", f"https://{host}/o/r.git")
    GitService.pull(repo, token=TOKEN, allowed_hosts=[host])
    assert _basic("oauth2", TOKEN) not in server.auth_headers


@pytest.mark.control(reason="git output was already redacted before logging; this keeps it so")
def test_no_log_line_carries_the_token(github_like, tmp_path, caplog):
    server, host = github_like
    caplog.set_level(logging.DEBUG)
    GitService.clone_with_code(
        f"https://{host}/o/r.git", tmp_path / "ws", token=TOKEN, allowed_hosts=[host]
    )
    repo = _local_repo(tmp_path / "w", f"https://{host}/o/r.git")
    GitService.pull(repo, token=TOKEN, allowed_hosts=[host])
    assert TOKEN not in caplog.text


def _helper_calls(server) -> list[str]:
    log = server.helper_log
    return log.read_text().split() if log.exists() else []


def test_no_configured_credential_helper_is_consulted_or_fed(github_like, tmp_path):
    server, host = github_like
    repo = _local_repo(tmp_path / "w", f"https://{host}/o/r.git")
    GitService.pull(repo, token=TOKEN, allowed_hosts=[host])
    GitService.clone_with_code(
        f"https://{host}/o/r.git", tmp_path / "ws", token=TOKEN, allowed_hosts=[host]
    )
    assert _helper_calls(server) == []  # neither `get` nor `store`
    assert _basic("oauth2", TOKEN) in server.auth_headers


def test_a_credential_stored_by_another_user_is_not_used(github_like, tmp_path):
    """The helper holds someone else's credential; this call's token wins."""
    server, host = github_like
    repo = _local_repo(tmp_path / "w", f"https://{host}/o/r.git")
    GitService.pull(repo, token=TOKEN, allowed_hosts=[host])
    assert _basic("oauth2", "STALE_CREDENTIAL") not in server.auth_headers
    assert _basic("oauth2", TOKEN) in server.auth_headers


def test_the_token_is_not_offered_to_the_host_an_insteadof_rule_rewrites_to(
    github_like, tmp_path, monkeypatch
):
    server, host = github_like
    monkeypatch.setattr(git_service, "_GITHUB_HOSTS", frozenset({"github.com"}))
    conf = tmp_path / "rewrite.gitconfig"
    conf.write_text(f'[url "https://{host}/"]\n\tinsteadOf = https://github.com/\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(conf))
    # no helper to answer first: the token could only come from Pyrite
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty))
    # The operator's process: a network caller's command reads no global
    # config at all, so the account's insteadOf rule never applies to it.
    with _bind(True):
        GitService.clone_with_code(
            "https://github.com/o/r.git",
            tmp_path / "ws",
            token=TOKEN,
            allowed_hosts=["github.com", host],
        )
    assert _basic("oauth2", TOKEN) not in server.auth_headers


@pytest.mark.control(reason="round 1 already held this; kept as a guard")
def test_the_token_is_in_the_environment_only(github_like, tmp_path, monkeypatch):
    server, host = github_like
    seen = []
    real = subprocess.run

    def run(cmd, *a, **kw):
        seen.append((list(cmd), dict(kw.get("env") or {})))
        return real(cmd, *a, **kw)

    monkeypatch.setattr(git_service.subprocess, "run", run)
    repo = _local_repo(tmp_path / "w", f"https://{host}/o/r.git")
    GitService.pull(repo, token=TOKEN, allowed_hosts=[host])
    carrying = [env for _cmd, env in seen if TOKEN in env.values()]
    assert carrying, "the token reached no git command"
    for cmd, env in seen:
        assert not any(TOKEN in str(a) for a in cmd)
        assert TOKEN not in env.get("GIT_CONFIG_PARAMETERS", "")  # names the helper only


@pytest.mark.control(reason="an operator's own remote on another host keeps its URL")
def test_a_remote_on_another_host_is_not_rewritten(github_like, tmp_path, monkeypatch):
    server, host = github_like
    monkeypatch.setattr(git_service, "_GITHUB_HOSTS", frozenset({"github.com"}))
    url = f"https://me:secret@{host}/o/r.git"
    repo = _local_repo(tmp_path / "w", url)
    GitService.pull(repo, token=TOKEN, allowed_hosts=[host])
    assert (
        subprocess.run(
            ["git", "config", "remote.origin.url"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()
        == url
    )


def test_every_registered_workspace_is_scrubbed_without_waiting_for_a_pull(tmp_path):
    from unittest.mock import MagicMock

    from pyrite.services.repo_service import RepoService

    dirty = _local_repo(tmp_path / "dirty", f"https://oauth2:{TOKEN}@github.com/o/r.git")
    clean = _local_repo(tmp_path / "clean", "https://github.com/o/s.git")
    db = MagicMock()
    db.list_repos.return_value = [
        {"local_path": str(dirty)},
        {"local_path": str(clean)},
        {"local_path": str(tmp_path / "gone")},
    ]
    assert RepoService(MagicMock(), db).scrub_workspace_remotes() == 1
    assert TOKEN not in (dirty / ".git" / "config").read_text()
    assert "https://github.com/o/r.git" in (dirty / ".git" / "config").read_text()


# -- every credential source of the server account, closed for a network caller --


@pytest.fixture
def account_sources(tmp_path, monkeypatch):
    """The server account offers a credential through every source git has:
    a global ``credential.helper``, a URL-scoped helper, ``core.askPass``,
    ``http.extraHeader``, ``~/.netrc``, a system config, and ``GIT_ASKPASS`` /
    ``SSH_ASKPASS`` in the environment. Each answers ``ACCOUNT_SECRET`` and
    records that it ran."""
    home = tmp_path / "account-home"
    home.mkdir()
    ran = tmp_path / "ran.log"
    prog = tmp_path / "answer.sh"
    prog.write_text(
        f'#!/bin/sh\necho "$0 $*" >> {ran}\n'
        'case "$1" in get) echo username=account; echo password=ACCOUNT_SECRET;; '
        "*) echo ACCOUNT_SECRET;; esac\n"
    )
    prog.chmod(0o755)
    conf = (
        f"[credential]\n\thelper = {prog}\n"
        f'[credential "https://127.0.0.1"]\n\thelper = {prog}\n'
        f"[core]\n\taskPass = {prog}\n"
        "[http]\n\textraHeader = X-Account: ACCOUNT_SECRET\n"
    )
    (home / ".gitconfig").write_text(conf)
    (home / ".netrc").write_text("machine 127.0.0.1 login account password ACCOUNT_SECRET\n")
    (home / ".netrc").chmod(0o600)
    system = tmp_path / "system.gitconfig"
    system.write_text(conf)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(system))
    monkeypatch.setenv("GIT_ASKPASS", str(prog))
    monkeypatch.setenv("SSH_ASKPASS", str(prog))
    return ran


def _bind(local: bool):
    try:
        from pyrite.services.acting import local_process
    except ImportError:  # the tree before the switch: nothing was bound
        import contextlib

        return contextlib.nullcontext()
    return local_process(local)


@pytest.mark.parametrize("token", [None, TOKEN], ids=["no-token", "token"])
def test_credential_fill_for_a_network_caller_reads_no_account_source(account_sources, token):
    url = "https://127.0.0.1:9/o/r.git"
    with _bind(False):
        creds = GitService._credentials(token, [url])
    env = git_service._git_env({**git_service._NETWORK_GUARD_ENV, **creds.env}, config=creds.config)
    r = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=127.0.0.1:9\npath=o/r.git\n\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    assert "ACCOUNT_SECRET" not in r.stdout
    assert not account_sources.exists(), account_sources.read_text()


def test_a_network_callers_clone_sends_no_account_credential(
    github_like, account_sources, tmp_path, monkeypatch
):
    server, host = github_like
    # github_like points the git config at its own files; the account's are the test here.
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    with _bind(False):
        GitService.clone_with_code(f"https://{host}/o/r.git", tmp_path / "ws", allowed_hosts=[host])
    sent = server.extra_headers + [
        base64.b64decode(h.split()[1]).decode() for h in server.auth_headers
    ]
    assert not any("ACCOUNT_SECRET" in h for h in sent), sent
    assert not account_sources.exists(), account_sources.read_text()


@pytest.mark.control(reason="the operator at the CLI keeps their own git setup")
def test_the_operators_clone_still_uses_their_own_helper(
    github_like, account_sources, tmp_path, monkeypatch
):
    server, host = github_like
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    with _bind(True):
        GitService.clone_with_code(f"https://{host}/o/r.git", tmp_path / "ws", allowed_hosts=[host])
    sent = server.extra_headers + [
        base64.b64decode(h.split()[1]).decode() for h in server.auth_headers
    ]
    assert any("ACCOUNT_SECRET" in h for h in sent)
