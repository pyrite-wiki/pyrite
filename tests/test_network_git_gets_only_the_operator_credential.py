"""A network git command borrows nothing of the server account (ADR-0052 N2).

Properties, each through the surface a caller uses:

1. A git process started for a network caller gets an environment built from
   an allow-list: no ``SSH_AUTH_SOCK``, no account HOME or global config, no
   variable outside `NETWORK_ENV_ALLOWLIST` and the ones Pyrite sets. A planted
   global helper, askpass program, netrc entry and ssh agent are never used
   (T0 channel 5).
2. The only credential it gets is the caller's own GitHub token, else the
   operator's ``git_credentials`` entry for the host (the KB's own first),
   else none (D3). With an entry, clone, pull and push succeed using it alone.
3. Every network push is held to ``git_allowed_hosts`` before git runs,
   whether the remote was configured by the operator (REST and MCP push) or
   comes from a caller's URL (export); only the local CLI's own push is exempt
   (D2).
4. An ssh remote is used only with an operator ssh key, through an ssh command
   that reads no other identity, agent, ssh config or known_hosts.
5. Commits made for a network caller carry ``git_identity``; unset, the
   account's global identity, until 0.27.0 (D1).

The https cases run real git against a local smart-HTTP server (``git
http-backend`` behind Basic auth) whose certificate is trusted through
``GIT_SSL_CAINFO``, as an operator's private CA would be.
"""

from __future__ import annotations

import base64
import os
import socket
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from pyrite.config import KBConfig, Settings

try:
    from pyrite.config import GitCredential, GitIdentity
except ImportError:  # the tree before N2: each test fails on its own behaviour
    GitCredential = GitIdentity = None  # type: ignore[assignment,misc]
from pyrite.services import git_service
from pyrite.services.acting import local_process
from pyrite.services.git_service import GitService
from pyrite.utils import git_hardening

OPERATOR_TOKEN = "OPERATOR-TOKEN-0123456789"
CALLER_TOKEN = "CALLER-TOKEN-9876543210"
ACCOUNT_SECRET = "ACCOUNT-SECRET"


def _git(cwd: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "SSH_"))}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    ).stdout.strip()


# -- the git server ------------------------------------------------------------------


class _GitHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _answer(self):
        auth = self.headers.get("Authorization")
        if auth:
            self.server.auth.append(base64.b64decode(auth.split()[1]).decode())
        if self.headers.get("X-Account"):
            self.server.auth.append(self.headers["X-Account"])
        if auth is None or base64.b64decode(auth.split()[1]).decode() not in self.server.accept:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="git"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        path, _, query = self.path.partition("?")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        out = subprocess.run(
            ["git", "http-backend"],
            input=body,
            capture_output=True,
            timeout=60,
            env={
                "PATH": os.environ["PATH"],
                "GIT_PROJECT_ROOT": str(self.server.root),
                "GIT_HTTP_EXPORT_ALL": "1",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "PATH_INFO": path,
                "QUERY_STRING": query,
                "REQUEST_METHOD": self.command,
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": str(len(body)),
                "REMOTE_USER": "t",
            },
        ).stdout
        head, _, payload = out.partition(b"\r\n\r\n")
        status, headers = 200, []
        for line in head.decode().split("\r\n"):
            key, _, value = line.partition(": ")
            if key.lower() == "status":
                status = int(value.split()[0])
            elif key:
                headers.append((key, value))
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = _answer  # noqa: N815


@pytest.fixture
def git_host(tmp_path, monkeypatch):
    """An https git host at 127.0.0.1:<port> serving ``/o/r.git``; it serves
    only a request carrying an accepted credential and records every
    credential it is sent."""
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key)]
        + ["-out", str(cert), "-days", "2", "-subj", "/CN=127.0.0.1"]
        + ["-addext", "subjectAltName=IP:127.0.0.1"],
        check=True,
        capture_output=True,
        timeout=60,
    )
    root = tmp_path / "srv"
    (root / "o").mkdir(parents=True)
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q", "-b", "main")
    (src / "a.md").write_text("a\n")
    _git(src, "add", "a.md")
    _git(src, "commit", "-q", "-m", "a")
    _git(tmp_path, "clone", "-q", "--bare", str(src), str(root / "o" / "r.git"))
    _git(root / "o" / "r.git", "config", "http.receivepack", "true")

    server = ThreadingHTTPServer(("127.0.0.1", 0), _GitHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    server.root, server.auth = root, []
    server.accept = {f"oauth2:{OPERATOR_TOKEN}", f"oauth2:{CALLER_TOKEN}"}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("GIT_SSL_CAINFO", str(cert))
    host = f"127.0.0.1:{server.server_address[1]}"
    server.host, server.url = host, f"https://{host}/o/r.git"
    yield server
    server.shutdown()
    server.server_close()


# -- the server account's stores -------------------------------------------------------


@pytest.fixture
def account(tmp_path, monkeypatch):
    """The server account offers a credential through every store it has, and
    each leaves a sentinel when used: a global and an XDG credential helper,
    ``core.askPass`` and ``http.extraHeader``, ``~/.netrc``, ``GIT_ASKPASS`` and
    ``SSH_ASKPASS``, an ssh agent socket, plus variables a network command has
    no use for."""
    home = tmp_path / "account-home"
    (home / ".config" / "git").mkdir(parents=True)
    sentinels = tmp_path / "sentinels"
    sentinels.mkdir()
    prog = tmp_path / "account-store.sh"
    prog.write_text(
        f'#!/bin/sh\ntouch "{sentinels}/$(basename "$0")-$1"\n'
        f"echo username=account\necho password={ACCOUNT_SECRET}\n"
    )
    prog.chmod(0o755)
    for name in ("helper", "xdg-helper", "askpass", "git-askpass", "ssh-askpass"):
        (tmp_path / name).symlink_to(prog)
    (home / ".gitconfig").write_text(
        f"[credential]\n\thelper = {tmp_path / 'helper'}\n"
        f"[core]\n\taskPass = {tmp_path / 'askpass'}\n"
        f"[http]\n\textraHeader = X-Account: {ACCOUNT_SECRET}\n"
        "[user]\n\tname = Account Global\n\temail = global@account.example\n"
    )
    (home / ".config" / "git" / "config").write_text(
        f"[credential]\n\thelper = {tmp_path / 'xdg-helper'}\n"
    )
    (home / ".netrc").write_text(f"machine 127.0.0.1 login account password {ACCOUNT_SECRET}\n")
    (home / ".netrc").chmod(0o600)

    agent = socket.socket(socket.AF_UNIX)
    sock_path = Path("/tmp") / f"pyrite-t1a-agent-{os.getpid()}-{id(agent)}.sock"
    agent.bind(str(sock_path))
    agent.listen(4)
    agent.settimeout(0.2)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = agent.accept()
            except OSError:
                continue
            (sentinels / "ssh-agent").touch()
            conn.close()

    threading.Thread(target=serve, daemon=True).start()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)
    monkeypatch.setenv("GIT_ASKPASS", str(tmp_path / "git-askpass"))
    monkeypatch.setenv("SSH_ASKPASS", str(tmp_path / "ssh-askpass"))
    monkeypatch.setenv("SSH_ASKPASS_REQUIRE", "force")
    monkeypatch.setenv("SSH_AUTH_SOCK", str(sock_path))
    for name, value in {
        "OPENAI_API_KEY": ACCOUNT_SECRET,
        "AWS_SECRET_ACCESS_KEY": ACCOUNT_SECRET,
        "HTTPS_PROXY": f"http://u:{ACCOUNT_SECRET}@127.0.0.1:9",
        "GIT_SSH": str(prog),
        "GIT_SSH_COMMAND": str(prog),
        "GIT_PROXY_COMMAND": str(prog),
        "GIT_SSL_NO_VERIFY": "1",
        "GIT_SSL_KEY": str(prog),
    }.items():
        monkeypatch.setenv(name, value)
    yield sentinels
    stop.set()
    agent.close()
    sock_path.unlink(missing_ok=True)


def _used(sentinels: Path) -> list[str]:
    return sorted(p.name for p in sentinels.iterdir())


def _record_git(monkeypatch) -> list[tuple[list[str], dict]]:
    """Every git process Pyrite starts (its environment carries the hardened
    command-line config), with that environment; the test's own git and the
    server's ``http-backend`` are not Pyrite's."""
    seen: list[tuple[list[str], dict]] = []
    real = subprocess.run

    def run(cmd, *a, **k):
        env = dict(k.get("env") or {})
        if "core.hooksPath" in env.get("GIT_CONFIG_PARAMETERS", ""):
            seen.append((list(cmd), env))
        return real(cmd, *a, **k)

    monkeypatch.setattr(git_service.subprocess, "run", run)
    return seen


#: What Pyrite itself puts into a network-reach git environment.
_PYRITE_SET = {
    "HOME",
    "XDG_CONFIG_HOME",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_PARAMETERS",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
    "GIT_TERMINAL_PROMPT",
    "GIT_SSH_COMMAND",
    "GIT_ALLOW_PROTOCOL",
    "GIT_LFS_SKIP_SMUDGE",
    "GIT_LFS_SKIP_PUSH",
    "PYRITE_GIT_TOKEN",
    "PYRITE_GIT_USERNAME",
}


def _outside_the_allowlist(env: dict) -> list[str]:
    return sorted(
        k
        for k in env
        if k not in _PYRITE_SET
        and k not in git_hardening.NETWORK_ENV_ALLOWLIST
        and not k.startswith(git_hardening.NETWORK_ENV_ALLOWED_PREFIXES)
    )


def _operator(
    host: str, monkeypatch, *, kb: str | None = None, var="PYRITE_T1A_TOKEN", url_prefixes=()
):
    monkeypatch.setenv(var, OPERATOR_TOKEN)
    return GitCredential(host=host, kb=kb, token_env=var, url_prefixes=tuple(url_prefixes))


def _run_op(op: str, git_host, tmp_path: Path, *, credentials, token=None):
    """Clone, pull or push through GitService, as a network caller. A clone's
    URL is one a caller named; a pull or push goes to the remote already
    configured in the repository (a registered KB's, D-U1)."""
    allowed = [git_host.host]
    if op == "clone":
        return GitService.clone_with_code(
            git_host.url,
            tmp_path / "ws",
            token=token,
            allowed_hosts=allowed,
            credentials=credentials,
        )[0]
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(git_host.root / "o" / "r.git"), str(work))
    _git(work, "remote", "set-url", "origin", git_host.url)
    if op == "pull":
        return GitService.pull(
            work,
            token=token,
            allowed_hosts=allowed,
            credentials=credentials,
            registered_remote=True,
        )[0]
    (work / "b.md").write_text("b\n")
    _git(work, "add", "b.md")
    _git(work, "commit", "-q", "-m", "b")
    return GitService.push(
        work,
        branch="main",
        token=token,
        allowed_hosts=allowed,
        credentials=credentials,
        registered_remote=True,
    )[0]


# -- 1 and 2: the environment, the stores, the operator credential ---------------------


@pytest.mark.parametrize("op", ["clone", "pull", "push"])
def test_with_an_operator_credential_the_command_succeeds_on_it_alone(
    op, git_host, account, tmp_path, monkeypatch
):
    seen = _record_git(monkeypatch)
    # The entry covers the clone's URL through its prefixes (a URL a caller
    # names, D-U1) and the pull's and push's configured remote by scope.
    entry = _operator(git_host.host, monkeypatch, url_prefixes=[f"https://{git_host.host}/o/"])
    with local_process(False):
        ok = _run_op(op, git_host, tmp_path, credentials=[entry])
    assert ok is True
    assert git_host.auth and set(git_host.auth) == {f"oauth2:{OPERATOR_TOKEN}"}
    assert _used(account) == []
    assert seen
    for cmd, env in seen:
        assert _outside_the_allowlist(env) == [], cmd
        assert "SSH_AUTH_SOCK" not in env
        assert env["HOME"] != os.environ["HOME"]
        assert ACCOUNT_SECRET not in " ".join(env.values())


@pytest.mark.parametrize("op", ["clone", "pull", "push"])
def test_with_no_credential_the_command_has_none(op, git_host, account, tmp_path):
    with local_process(False):
        ok = _run_op(op, git_host, tmp_path, credentials=[])
    assert ok is False
    assert git_host.auth == []  # not the netrc entry, not a helper's, not an extraHeader
    assert _used(account) == []


def test_a_caller_token_on_github_comes_before_the_operator_entry(
    git_host, account, tmp_path, monkeypatch
):
    monkeypatch.setattr(git_service, "_GITHUB_HOSTS", frozenset({"127.0.0.1"}))
    with local_process(False):
        ok = _run_op(
            "clone",
            git_host,
            tmp_path,
            credentials=[_operator(git_host.host, monkeypatch)],
            token=CALLER_TOKEN,
        )
    assert ok is True
    assert set(git_host.auth) == {f"oauth2:{CALLER_TOKEN}"}


def test_a_credential_for_another_host_is_not_offered(git_host, account, tmp_path, monkeypatch):
    with local_process(False):
        ok = _run_op(
            "clone", git_host, tmp_path, credentials=[_operator("git.example.org", monkeypatch)]
        )
    assert ok is False
    assert git_host.auth == []


def test_the_kbs_own_entry_comes_before_the_hosts(monkeypatch):
    monkeypatch.setenv("T_HOST", "host-token")
    monkeypatch.setenv("T_KB", "kb-token")
    entries = [
        GitCredential(host="github.com", token_env="T_HOST"),
        GitCredential(host="github.com", kb="notes", token_env="T_KB"),
    ]
    url = ["https://github.com/o/r.git"]

    def plan(kb):
        return GitService._network_plan(
            url,
            token=None,
            allowed_hosts=None,
            credentials=entries,
            kb_name=kb,
            allow_userinfo=True,
            registered_remote=True,  # the push of a KB to its configured remote
        )

    with local_process(False):
        assert plan("notes").env["PYRITE_GIT_TOKEN"] == "kb-token"
        assert plan("other").env["PYRITE_GIT_TOKEN"] == "host-token"
        assert plan(None).env["PYRITE_GIT_TOKEN"] == "host-token"


def test_a_network_callers_local_git_commands_find_no_account_store(account, tmp_path):
    """status, log, commit for a network caller: the allow-list too."""
    repo = tmp_path / "kb"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.md").write_text("a\n")
    with local_process(False):
        env = git_hardening.git_env()
        GitService.get_status(repo)
    assert _outside_the_allowlist(env) == []
    assert "SSH_AUTH_SOCK" not in env
    # ssh, should any git process start it, reads no identity, agent or config
    for option in ("'-F' '/dev/null'", "'IdentityAgent=none'", "'IdentityFile=none'"):
        assert option in env["GIT_SSH_COMMAND"]
    assert _used(account) == []


def test_a_helper_in_the_repositorys_own_config_is_not_consulted(git_host, account, tmp_path):
    """The repository's local config is still read (ADR-0052 Limits), but no
    credential helper or askpass from any scope runs for a network caller."""
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(git_host.root / "o" / "r.git"), str(work))
    _git(work, "remote", "set-url", "origin", git_host.url)
    _git(work, "config", "credential.helper", str(tmp_path / "helper"))
    _git(work, "config", "core.askPass", str(tmp_path / "askpass"))
    with local_process(False):
        ok, _ = GitService.pull(work, allowed_hosts=[git_host.host], credentials=[])
    assert ok is False
    assert _used(account) == []
    assert git_host.auth == []


@pytest.mark.control(reason="the operator's own process keeps their whole environment, as before")
def test_the_local_process_keeps_its_own_environment(account):
    with local_process(True):
        env = git_hardening.git_env()
    assert env["SSH_AUTH_SOCK"] == os.environ["SSH_AUTH_SOCK"]
    assert env["HOME"] == os.environ["HOME"]


# -- 3: every network push is held to the host rule ---------------------------------------


@pytest.fixture
def kb_with_a_remote(tmp_path):
    """A KB in a git repository whose `origin` the operator configured on a
    host outside git_allowed_hosts (a local bare repository standing behind
    an https URL)."""
    from tests._world import build_world

    kb_dir = tmp_path / "notes"
    kb_dir.mkdir()
    _git(kb_dir, "init", "-q", "-b", "main")
    (kb_dir / "a.md").write_text("a\n")
    _git(kb_dir, "add", "a.md")
    _git(kb_dir, "commit", "-q", "-m", "a")
    _git(kb_dir, "remote", "add", "origin", "https://git.example.org/o/notes.git")
    w = build_world(
        tmp_path / "world", kbs=[KBConfig(name="notes", path=kb_dir, kb_type="generic")]
    )
    yield w
    w.close()


def _pushes(seen) -> list[list[str]]:
    return [cmd for cmd, _ in seen if "push" in cmd and "remote" not in cmd]


def test_a_rest_push_to_an_operator_remote_off_the_list_is_refused(kb_with_a_remote, monkeypatch):
    seen = _record_git(monkeypatch)
    r = kb_with_a_remote.client.post("/api/kbs/notes/push", json={"remote": "origin"})
    assert r.status_code == 200, r.text
    assert r.json()["success"] is False
    assert "git_allowed_hosts" in r.json()["message"]
    assert _pushes(seen) == []


def test_an_mcp_push_to_an_operator_remote_off_the_list_is_refused(kb_with_a_remote, monkeypatch):
    seen = _record_git(monkeypatch)
    result = kb_with_a_remote.mcp("admin")._dispatch_tool("kb_push", {"kb": "notes"})
    assert result.get("success") is not True, result
    assert _pushes(seen) == []


def test_a_rest_publish_to_an_operator_remote_off_the_list_is_refused(
    kb_with_a_remote, monkeypatch
):
    """Publish (commit, then push) reaches the same push over REST."""
    seen = _record_git(monkeypatch)
    (kb_with_a_remote.kb_path("notes") / "b.md").write_text("b\n")
    r = kb_with_a_remote.client.post("/api/kbs/notes/publish", json={})
    assert _pushes(seen) == [], r.text


@pytest.mark.control(
    reason="export already checked a caller's URL; pinned beside the operator-remote push now held"
)
def test_an_export_push_to_a_caller_url_off_the_list_is_refused(kb_with_a_remote, monkeypatch):
    from pyrite.services.export_service import ExportService

    seen = _record_git(monkeypatch)
    w = kb_with_a_remote
    with local_process(False):
        result = ExportService(w.config, w.db).export_kb_to_repo(
            "notes", "https://git.example.org/o/dest.git"
        )
    assert result["success"] is False
    assert result.get("error_code") == "REMOTE_NOT_ALLOWED"
    assert [cmd for cmd, _ in seen if "clone" in cmd or "push" in cmd] == []


@pytest.mark.control(reason="the CLI push was already unrestricted; the exemption D2 keeps")
def test_the_cli_push_of_the_operators_own_kb_is_not_held_to_the_list(
    kb_with_a_remote, monkeypatch
):
    """``pyrite kb push`` at the operator's terminal: the one exempt push. It
    runs (here it fails to reach the made-up host; it is not refused)."""
    seen = _record_git(monkeypatch)
    out = kb_with_a_remote.cli("kb", "push", "-k", "notes")
    assert "git_allowed_hosts" not in out.output
    assert _pushes(seen), out.output


# -- 4: ssh only with an operator key, and then only that key -------------------------


@pytest.fixture
def fake_ssh(tmp_path, monkeypatch):
    """An ``ssh`` first on PATH that records its arguments and environment."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "ssh.log"
    (bindir / "ssh").write_text(
        f'#!/bin/sh\n{{ echo "ARGV $*"; env | sed "s/^/ENV /"; }} >> {log}\nexit 255\n'
    )
    (bindir / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return log


def test_an_ssh_remote_uses_the_operator_key_and_nothing_else(
    fake_ssh, account, tmp_path, monkeypatch
):
    key, known = tmp_path / "op_key", tmp_path / "known_hosts"
    key.write_text("not a real key\n")
    known.write_text("")
    entry = GitCredential(
        host="git.example.org",
        ssh_key=key,
        known_hosts=known,
        url_prefixes=("git@git.example.org:o/",),
    )
    with local_process(False):
        ok, code, _ = GitService.clone_with_code(
            "git@git.example.org:o/r.git",
            tmp_path / "ws",
            allowed_hosts=["git.example.org"],
            credentials=[entry],
        )
    assert ok is False and code != "REMOTE_NOT_ALLOWED"
    log = fake_ssh.read_text()
    argv = next(line for line in log.splitlines() if line.startswith("ARGV "))
    for option in (
        f"-i {key}",
        "-F /dev/null",
        "-o IdentitiesOnly=yes",
        "-o IdentityAgent=none",
        "-o IdentityFile=none",
        f"-o UserKnownHostsFile={known}",
        "-o BatchMode=yes",
    ):
        assert option in argv, argv
    assert "ENV SSH_AUTH_SOCK=" not in log
    assert "ENV GIT_ALLOW_PROTOCOL=ssh" in log
    assert _used(account) == []


@pytest.mark.parametrize("entry", ["none", "token"], ids=["no-entry", "a-token-entry"])
def test_an_ssh_remote_without_an_operator_key_is_refused_before_ssh_runs(
    fake_ssh, tmp_path, entry
):
    credentials = (
        [GitCredential(host="git.example.org", token_env="PYRITE_T1A_TOKEN")]
        if entry == "token"
        else []
    )
    with local_process(False):
        ok, code, message = GitService.clone_with_code(
            "ssh://git@git.example.org/o/r.git",
            tmp_path / "ws",
            allowed_hosts=["git.example.org"],
            credentials=credentials,
        )
    assert (ok, code) == (False, "REMOTE_NOT_ALLOWED")
    assert "ssh_key" in message
    assert not fake_ssh.exists()


@pytest.mark.control(
    reason="already held; round 2 gives the entry a readable key so the refusal is the URL's, not the key's"
)
@pytest.mark.parametrize(
    "url",
    [
        "git@-oProxyCommand=x:o/r.git",
        "ssh://-oProxyCommand=x/o/r",
        "git@git.example.org:-o/r",
        "ssh://git@git.example.org/../r",
        "us er@git.example.org:o/r",
        "git@[::1]:o/r",
    ],
)
def test_an_ssh_remote_that_ssh_could_read_as_an_option_is_refused(url, tmp_path, monkeypatch):
    (tmp_path / "k").write_text("k\n")  # a readable key: the refusal must be the URL's
    entry = GitCredential(host="git.example.org", ssh_key=tmp_path / "k", known_hosts=tmp_path)
    assert GitService._endpoint(url, True) is None or GitService._endpoint(url, True)[1] in (
        "git.example.org",
    )
    with local_process(False), pytest.raises(git_service.RemoteNotAllowedError):
        GitService._network_plan(
            [url],
            token=None,
            allowed_hosts=["git.example.org"],
            credentials=[entry],
            kb_name=None,
            allow_userinfo=True,
            registered_remote=True,
        )


# -- 5: the identity of a network caller's commits -------------------------------------


def _committed_as(repo: Path) -> str:
    return _git(repo, "log", "-1", "--format=%an <%ae>|%cn <%ce>")


@pytest.fixture
def repo_to_commit(tmp_path):
    repo = tmp_path / "kb"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.md").write_text("a\n")
    return repo


def test_a_network_commit_carries_git_identity(account, repo_to_commit):
    with local_process(False):
        ok, result = GitService.commit(
            repo_to_commit, "m", identity=GitIdentity(name="Pyrite Bot", email="bot@pyrite.example")
        )
    assert ok, result
    assert _committed_as(repo_to_commit) == (
        "Pyrite Bot <bot@pyrite.example>|Pyrite Bot <bot@pyrite.example>"
    )


def test_unset_a_network_commit_falls_back_to_the_global_identity(account, repo_to_commit):
    """Until 0.27.0: the account's global user.name/email, carried alone into
    Pyrite's own global config (no helper, askpass or header with it)."""
    with local_process(False):
        ok, result = GitService.commit(repo_to_commit, "m")
    assert ok, result
    assert _committed_as(repo_to_commit).startswith("Account Global <global@account.example>")
    config = Path(git_hardening.network_home(), "gitconfig").read_text()
    assert "helper" not in config and "askPass" not in config and ACCOUNT_SECRET not in config


@pytest.mark.control(reason="the operator's own commits keep their own git setup")
def test_the_local_process_commits_as_itself(account, repo_to_commit):
    with local_process(True):
        ok, result = GitService.commit(
            repo_to_commit, "m", identity=GitIdentity(name="Pyrite Bot", email="bot@pyrite.example")
        )
    assert ok, result
    assert _committed_as(repo_to_commit).startswith("Account Global")


def test_the_accounts_safe_directory_exceptions_are_carried(account, tmp_path):
    """A KB on a volume owned by another uid stays usable: ``safe.directory``
    is honoured from global config only, so the account's entries are carried
    into Pyrite's own (and nothing else of that config is)."""
    gitconfig = Path(os.environ["GIT_CONFIG_GLOBAL"])
    gitconfig.write_text(gitconfig.read_text() + f"[safe]\n\tdirectory = {tmp_path / 'vol'}\n")
    with local_process(False):
        home = git_hardening.network_home()
    text = Path(home, "gitconfig").read_text()
    assert f'directory = "{tmp_path / "vol"}"' in text


# -- the settings ---------------------------------------------------------------------------


def test_the_settings_survive_a_round_trip(tmp_path):
    from tests._world import build_world

    key = tmp_path / "id"
    w = build_world(
        tmp_path / "w",
        settings={
            "git_credentials": [
                {"host": "github.com", "token_env": "GH_TOKEN"},
                {"host": "git.example.org", "kb": "notes", "token_file": str(tmp_path / "t")},
                {"host": "git.example.org:2222", "ssh_key": str(key), "known_hosts": "/kh"},
                {
                    "host": "github.com",
                    "token_env": "GH_TOKEN",
                    "url_prefixes": ["https://github.com/team/"],
                },
            ],
            "git_identity": {"name": "Pyrite Bot", "email": "bot@pyrite.example"},
        },
        rest=False,
    )
    try:
        s = w.config.settings
        assert [c.host for c in s.git_credentials] == [
            "github.com",
            "git.example.org",
            "git.example.org:2222",
            "github.com",
        ]
        assert s.git_credentials[3].url_prefixes == ("https://github.com/team/",)
        assert s.git_credentials[1].kb == "notes"
        assert s.git_credentials[2].ssh_key == key
        assert s.git_identity == GitIdentity(name="Pyrite Bot", email="bot@pyrite.example")
    finally:
        w.close()


@pytest.mark.parametrize(
    ("entry", "says"),
    [
        ({"host": "github.com"}, "exactly one"),
        ({"host": "github.com", "token_env": "A", "ssh_key": "/k"}, "exactly one"),
        ({"host": "https://github.com", "token_env": "A"}, "host"),
        ({"host": "github.com", "ssh_key": "/k"}, "known_hosts"),
        ({"host": "github.com", "ssh_key": "k", "known_hosts": "/kh"}, "absolute"),
        ({"host": "github.com", "token_env": "A B"}, "token_env"),
        ({"host": "github.com", "token_env": "A", "password": "x"}, "unknown"),
        ({"host": "github.com", "token_env": "A", "username": "a:b"}, "username"),
    ],
)
def test_a_bad_credential_entry_is_one_error_at_load(entry, says):
    with pytest.raises(ValueError, match=says):
        Settings(git_credentials=[entry])


@pytest.mark.parametrize(
    ("entry", "says"),
    [
        (
            {"host": "github.com", "token_env": "A", "url_prefixes": "https://github.com/o/"},
            "must be a list",
        ),
        (
            {"host": "github.com", "token_env": "A", "url_prefixes": ["https://evil.example/o/"]},
            "not an https URL on github.com",
        ),
        (
            {"host": "github.com", "token_env": "A", "url_prefixes": ["git@github.com:o/"]},
            "not an https URL",
        ),
        (
            {
                "host": "github.com",
                "ssh_key": "/k",
                "known_hosts": "/kh",
                "url_prefixes": ["https://github.com/o/"],
            },
            "not an ssh URL",
        ),
        (
            {"host": "github.com", "token_env": "A", "url_prefixes": ["https://u@github.com/o/"]},
            "not an https URL on github.com",
        ),
    ],
)
def test_a_bad_url_prefix_is_one_error_at_load(entry, says):
    """An entry's url_prefixes name its own host over its own transport."""
    with pytest.raises(ValueError, match=says):
        Settings(git_credentials=[entry])


@pytest.mark.parametrize(
    "value", [{"name": "a"}, {"name": "a", "email": ""}, {"name": "a\nb", "email": "e"}, "x"]
)
def test_a_bad_identity_is_one_error_at_load(value):
    with pytest.raises(ValueError, match="git_identity"):
        Settings(git_identity=value)


def test_a_network_environment_is_built_only_from_a_plan():
    """The network constructor takes a plan `_network_plan` built, never a
    mapping a call site put together."""
    with pytest.raises(TypeError):
        git_service._network_git_env({"GIT_ALLOW_PROTOCOL": "https:ssh:file"})


# -- the operator's stored CLI GitHub token is the operator's ---------------------------


def test_a_network_subscribe_never_gets_the_operators_stored_github_token(tmp_path, monkeypatch):
    """``pyrite auth github-login`` stores a token for the operator's CLI. A
    network caller with no GitHub account of its own clones with no token,
    not with that one (D3: the caller's own token, else the operator's
    git_credentials entry, else none)."""
    from pyrite.github_auth import GitHubAuth, save_github_auth
    from tests._world import build_world

    w = build_world(
        tmp_path / "w", kbs=[KBConfig(name="k", path=tmp_path / "k", kb_type="generic")]
    )
    tokens: list[str | None] = []
    real = subprocess.run

    def run(cmd, *a, **k):
        if "clone" in cmd:  # the network edge: answered here, never reached
            tokens.append((k.get("env") or {}).get("PYRITE_GIT_TOKEN"))
            return subprocess.CompletedProcess(cmd, 128, "", "remote: Repository not found.")
        return real(cmd, *a, **k)

    try:
        with w.pointed():
            save_github_auth(GitHubAuth(access_token="OPERATOR-CLI-TOKEN"))
            monkeypatch.setattr(git_service.subprocess, "run", run)
            r = w.client.post("/api/repos/subscribe", json={"remote_url": "https://github.com/o/r"})
    finally:
        w.close()
    assert r.status_code in (400, 404), r.text
    assert tokens == [None]


@pytest.mark.control(reason="the operator's own CLI keeps its stored token, as before")
def test_the_cli_keeps_the_operators_stored_github_token(tmp_path):
    from pyrite.github_auth import GitHubAuth, save_github_auth
    from pyrite.services.repo_service import RepoService
    from tests._world import build_world

    w = build_world(tmp_path / "w", rest=False)
    try:
        with w.pointed():
            save_github_auth(GitHubAuth(access_token="OPERATOR-CLI-TOKEN"))
            with local_process(True):
                assert RepoService(w.config, w.db)._get_token() == "OPERATOR-CLI-TOKEN"
    finally:
        w.close()
