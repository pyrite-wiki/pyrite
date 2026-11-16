"""A network caller's git and GitHub requests use only its own token, or an
operator grant whose scope covers that destination and transport (ADR-0052 N2).

Properties, each through the surface a caller uses where one reaches it:

1. The operator's stored CLI GitHub token is the local operator's: a network
   subscribe, fork or unsubscribe never sends it to the GitHub API, and never
   records the caller's workspace rows under the operator's GitHub user.
2. A ``git_credentials`` entry serves remotes already configured in a
   registered KB's repository (push of a KB, sync of a subscribed clone). A URL
   a caller names (subscribe, fork, export) gets only the caller's own token,
   unless the entry lists ``url_prefixes`` that cover it (D-U1).
3. An entry is chosen by (host, kb, transport): an https remote never gets an
   ssh-key entry, an ssh remote never gets a token entry.
4. The local CLI ignores ``git_credentials`` and uses its own git setup (D-U6).
5. Network git uses no proxy unless the operator set ``git_proxy``; the GitHub
   API calls follow the same rule (D-U2).
6. A KB repository's own ``user.*`` wins over ``git_identity`` (D-U5).
"""

from __future__ import annotations

import shlex
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import httpx
import pytest

from pyrite.config import GitCredential, GitIdentity, KBConfig, Settings
from pyrite.exceptions import RemoteNotAllowedError
from pyrite.services import git_service
from pyrite.services.acting import local_process
from pyrite.services.git_service import GitService
from tests import test_network_git_gets_only_the_operator_credential as base
from tests.test_network_git_gets_only_the_operator_credential import (
    OPERATOR_TOKEN,
    _git,
    _operator,
    _used,
)

# The https git host and the server account's planted stores, shared with
# the base file.
git_host = base.git_host
account = base.account

PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")


def _plan(urls, credentials, *, kb=None, registered=True, token=None, hosts=None):
    return GitService._network_plan(
        urls,
        token=token,
        allowed_hosts=hosts or ["github.com", "git.example.org"],
        credentials=credentials,
        kb_name=kb,
        allow_userinfo=True,
        registered_remote=registered,
    )


# -- 1: the operator's stored CLI token never identifies a network caller ----------------


def _seed_clone(cmd, kb_name: str) -> subprocess.CompletedProcess:
    """Stand in for the network: a clone that lands a repository holding one KB."""
    dest = Path(cmd[-1])
    dest.mkdir(parents=True)
    _git(dest, "init", "-q", "-b", "main")
    (dest / "kb.yaml").write_text(f"name: {kb_name}\nkb_type: generic\n")
    (dest / "a.md").write_text("---\nid: a\ntitle: A\ntype: note\n---\nA\n")
    _git(dest, "add", ".")
    _git(dest, "commit", "-q", "-m", "a")
    return subprocess.CompletedProcess(cmd, 0, "", "")


def test_a_network_subscribe_and_unsubscribe_never_identify_the_caller_as_the_operator(
    tmp_path, monkeypatch
):
    """REST subscribe reaches the identity lookup after a clone that succeeds;
    unsubscribe reaches it again. Neither sends the stored CLI token, and the
    workspace row is the caller's (the local sentinel), not the operator's
    GitHub user."""
    from pyrite.github_auth import GitHubAuth, save_github_auth
    from tests._world import build_world

    w = build_world(
        tmp_path / "w",
        kbs=[KBConfig(name="k", path=tmp_path / "k", kb_type="generic")],
    )
    sent: list[tuple[str, str | None]] = []
    real = subprocess.run

    def run(cmd, *a, **k):
        if "clone" in cmd:
            return _seed_clone(cmd, "subscribed-kb")
        return real(cmd, *a, **k)

    def send(self, request, *a, **k):  # the GitHub API, at the transport edge
        sent.append((str(request.url), request.headers.get("authorization")))
        return httpx.Response(
            200, json={"login": "operator-gh", "id": 4242, "name": "Op"}, request=request
        )

    try:
        with w.pointed():
            save_github_auth(GitHubAuth(access_token="OPERATOR-CLI-TOKEN"))
            monkeypatch.setattr(git_service.subprocess, "run", run)
            monkeypatch.setattr(httpx.Client, "send", send)
            r = w.client.post("/api/repos/subscribe", json={"remote_url": "https://github.com/o/r"})
            assert r.status_code == 200, r.text
            repo = w.db.get_repo(name="o/r")
            local = w.db.get_local_user()
            # The floor: the subscribe ran through the identity lookup and
            # recorded a workspace row for whoever it decided the caller was.
            in_local_workspace = [x["name"] for x in w.db.get_workspace_repos(local["id"])]
            operator_user = w.db.get_user(github_login="operator-gh")
            d = w.client.delete("/api/repos/o/r")
            assert d.status_code == 200, d.text
    finally:
        w.close()
    assert repo is not None
    assert all(auth is None or "OPERATOR-CLI-TOKEN" not in auth for _, auth in sent), sent
    assert operator_user is None
    assert in_local_workspace == ["o/r"]


@pytest.mark.control(reason="the operator's own CLI keeps its stored identity, as before")
def test_the_local_cli_is_identified_by_its_stored_token(tmp_path, monkeypatch):
    from pyrite.github_auth import GitHubAuth, save_github_auth
    from pyrite.services.user_service import UserService
    from tests._world import build_world

    sent: list[str | None] = []

    def send(self, request, *a, **k):
        sent.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"login": "me", "id": 7}, request=request)

    w = build_world(tmp_path / "w", rest=False)
    try:
        with w.pointed():
            save_github_auth(GitHubAuth(access_token="OPERATOR-CLI-TOKEN"))
            monkeypatch.setattr(httpx.Client, "send", send)
            with local_process(True):
                user = UserService(w.db).get_current_user()
    finally:
        w.close()
    assert user["github_login"] == "me"
    assert sent == ["Bearer OPERATOR-CLI-TOKEN"]


# -- 2: an entry's scope: registered remotes, or the URL prefixes it lists -----------------


def test_an_unscoped_entry_is_not_offered_to_a_url_a_caller_names(monkeypatch):
    monkeypatch.setenv("T_OP", "op-token")
    entry = GitCredential(host="github.com", token_env="T_OP")
    with local_process(False):
        plan = _plan(["https://github.com/operator/private"], [entry], registered=False)
    assert plan.credential == "none"
    assert "PYRITE_GIT_TOKEN" not in plan.env


def test_a_kb_entry_is_not_offered_to_a_url_a_caller_names(monkeypatch):
    monkeypatch.setenv("T_OP", "op-token")
    entry = GitCredential(host="github.com", kb="notes", token_env="T_OP")
    with local_process(False):
        plan = _plan(["https://github.com/o/r"], [entry], kb="notes", registered=False)
    assert plan.credential == "none"


def test_a_kb_entry_is_never_offered_to_another_kbs_remote(monkeypatch):
    """With no unscoped entry to fall back on, KB "notes"'s token is still not
    offered to the push of KB "other" on the same host."""
    monkeypatch.setenv("T_NOTES", "notes-token")
    entry = GitCredential(host="github.com", kb="notes", token_env="T_NOTES")
    with local_process(False):
        other = _plan(["https://github.com/o/other"], [entry], kb="other")
        unnamed = _plan(["https://github.com/o/other"], [entry], kb=None)
    assert (other.credential, unnamed.credential) == ("none", "none")


@pytest.mark.parametrize(
    ("url", "offered"),
    [
        ("https://github.com/operator/private", True),
        ("https://github.com/operator/private.git", True),
        ("https://github.com/operator", False),
        ("https://github.com/operator-evil/x", False),
        ("https://github.com/other/private", False),
        ("https://github.com:8443/operator/private", False),
    ],
)
def test_an_entry_with_url_prefixes_covers_only_the_urls_under_them(url, offered, monkeypatch):
    monkeypatch.setenv("T_OP", "op-token")
    entry = GitCredential(
        host="github.com", token_env="T_OP", url_prefixes=("https://github.com/operator/",)
    )
    with local_process(False):
        try:
            plan = _plan([url], [entry], registered=False, hosts=["github.com", "github.com:8443"])
        except RemoteNotAllowedError:
            plan = None
    assert (plan is not None and plan.credential == "operator-token") is offered


def test_a_url_a_caller_names_clones_with_no_operator_token(
    git_host, account, tmp_path, monkeypatch
):
    """Subscribe, fork and export clone a URL the caller named: real git, an
    unscoped entry for the host, and nothing is sent."""
    with local_process(False):
        ok, _code, _ = GitService.clone_with_code(
            git_host.url,
            tmp_path / "ws",
            allowed_hosts=[git_host.host],
            credentials=[_operator(git_host.host, monkeypatch)],
        )
    assert ok is False
    assert git_host.auth == []
    assert _used(account) == []


def test_a_url_under_the_entrys_prefixes_clones_with_the_operator_token(
    git_host, account, tmp_path, monkeypatch
):
    monkeypatch.setenv("PYRITE_T1A_TOKEN", OPERATOR_TOKEN)
    entry = GitCredential(
        host=git_host.host,
        token_env="PYRITE_T1A_TOKEN",
        url_prefixes=(f"https://{git_host.host}/o/",),
    )
    with local_process(False):
        ok, code, _ = GitService.clone_with_code(
            git_host.url, tmp_path / "ws", allowed_hosts=[git_host.host], credentials=[entry]
        )
    assert ok is True, code
    assert set(git_host.auth) == {f"oauth2:{OPERATOR_TOKEN}"}


@pytest.fixture
def notes_on_git_host(git_host, tmp_path, monkeypatch):
    """KB ``notes`` in a repository whose origin is the https git host; the
    operator allows the host and grants an unscoped token for it."""
    from tests._world import build_world

    kb_dir = tmp_path / "notes"
    _git(tmp_path, "clone", "-q", str(git_host.root / "o" / "r.git"), str(kb_dir))
    _git(kb_dir, "remote", "set-url", "origin", git_host.url)
    monkeypatch.setenv("PYRITE_T1A_TOKEN", OPERATOR_TOKEN)
    w = build_world(
        tmp_path / "world",
        kbs=[KBConfig(name="notes", path=kb_dir, kb_type="generic")],
        settings={
            "git_allowed_hosts": [git_host.host],
            "git_credentials": [{"host": git_host.host, "token_env": "PYRITE_T1A_TOKEN"}],
        },
    )
    yield w
    w.close()


@pytest.mark.control(
    reason="the other side of D-U1: a registered KB's remote keeps the entry (it had it before); fails if push_kb drops registered_remote"
)
def test_a_rest_push_of_a_kb_uses_the_operator_entry_for_its_configured_remote(
    notes_on_git_host, git_host
):
    kb = notes_on_git_host.kb_path("notes")
    (kb / "b.md").write_text("b\n")
    _git(kb, "add", "b.md")
    _git(kb, "commit", "-q", "-m", "b")
    r = notes_on_git_host.client.post("/api/kbs/notes/push", json={"remote": "origin"})
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True, r.text
    assert set(git_host.auth) == {f"oauth2:{OPERATOR_TOKEN}"}


def test_a_rest_export_to_a_url_the_caller_names_gets_no_operator_entry(
    notes_on_git_host, git_host
):
    (notes_on_git_host.kb_path("notes") / "c.md").write_text("---\nid: c\ntitle: C\n---\nC\n")
    notes_on_git_host.index("notes")
    r = notes_on_git_host.client.post(
        "/api/kbs/notes/export", json={"repo_url": git_host.url, "branch": "main"}
    )
    assert r.json().get("success") is not True, r.text
    assert git_host.auth == []


@pytest.mark.control(
    reason="the other side of D-U1: a subscription's remote keeps the entry (it had it before); fails if sync drops registered_remote"
)
def test_a_rest_sync_of_a_subscription_uses_the_operator_entry_for_its_remote(
    git_host, account, tmp_path, monkeypatch
):
    """The subscribed clone's origin was configured when it was registered:
    an unscoped entry serves its pull (D-U1)."""
    from pyrite.services.repo_service import RepoService
    from tests._world import build_world

    monkeypatch.setenv("PYRITE_T1A_TOKEN", OPERATOR_TOKEN)
    w = build_world(
        tmp_path / "world",
        settings={
            "git_allowed_hosts": [git_host.host],
            "git_credentials": [{"host": git_host.host, "token_env": "PYRITE_T1A_TOKEN"}],
        },
    )
    try:
        clone = tmp_path / "subscribed"
        _git(tmp_path, "clone", "-q", str(git_host.root / "o" / "r.git"), str(clone))
        _git(clone, "remote", "set-url", "origin", git_host.url)
        (clone / "kb.yaml").write_text("name: subscribed\nkb_type: generic\n")
        registered = RepoService(w.config, w.db)._register_clone(
            clone,
            full_name="o/r",
            remote_url=git_host.url,
            owner="o",
            branch="main",
            read_only=True,
            workspace_role="subscriber",
        )
        assert registered["success"], registered
        r = w.client.post("/api/repos/o/r/sync")
    finally:
        w.close()
    assert r.status_code == 200, r.text
    assert set(git_host.auth) == {f"oauth2:{OPERATOR_TOKEN}"}, r.text


# -- 3: chosen by transport as well as host and KB ----------------------------------------


def _docs_entries(tmp_path):
    """The example in docs/configuration.md, for git.example.org."""
    (tmp_path / "deploy_key").write_text("k\n")
    return [
        GitCredential(host="git.example.org", kb="notes", token_file=tmp_path / "notes-token"),
        GitCredential(
            host="git.example.org", ssh_key=tmp_path / "deploy_key", known_hosts=tmp_path / "kh"
        ),
    ]


def test_an_https_remote_never_gets_an_ssh_key_entry(tmp_path):
    (tmp_path / "notes-token").write_text("notes-token\n")
    with local_process(False):
        plan = _plan(["https://git.example.org/o/other.git"], _docs_entries(tmp_path), kb="other")
    assert plan.credential == "none"
    assert "deploy_key" not in plan.env.get("GIT_SSH_COMMAND", "")


def test_an_ssh_remote_never_gets_a_token_entry(tmp_path):
    (tmp_path / "notes-token").write_text("notes-token\n")
    with local_process(False):
        plan = _plan(["git@git.example.org:o/notes.git"], _docs_entries(tmp_path), kb="notes")
    assert plan.credential == "operator-ssh-key"
    assert "PYRITE_GIT_TOKEN" not in plan.env
    assert "deploy_key" in plan.env["GIT_SSH_COMMAND"]


@pytest.mark.control(reason="the KB's https entry for its own https remote, as before")
def test_an_https_remote_of_the_kb_gets_the_kbs_token(tmp_path):
    (tmp_path / "notes-token").write_text("notes-token\n")
    with local_process(False):
        plan = _plan(["https://git.example.org/o/notes.git"], _docs_entries(tmp_path), kb="notes")
    assert plan.credential == "operator-token"
    assert plan.env["PYRITE_GIT_TOKEN"] == "notes-token"


# -- 4: the local CLI ignores git_credentials --------------------------------------------


def test_the_local_cli_ignores_git_credentials(git_host, account, tmp_path, monkeypatch):
    with local_process(True):
        plan = _plan(
            [git_host.url],
            [_operator(git_host.host, monkeypatch)],
            hosts=[git_host.host],
        )
        GitService.clone_with_code(
            git_host.url,
            tmp_path / "ws",
            allowed_hosts=[git_host.host],
            credentials=[_operator(git_host.host, monkeypatch)],
        )
    assert plan.credential == "none"
    assert f"oauth2:{OPERATOR_TOKEN}" not in git_host.auth


def test_the_local_cli_never_uses_an_operator_ssh_key(tmp_path):
    entry = GitCredential(
        host="git.example.org", ssh_key=tmp_path / "deploy_key", known_hosts=tmp_path / "kh"
    )
    with local_process(True), pytest.raises(RemoteNotAllowedError):
        _plan(["git@git.example.org:o/r.git"], [entry])


# -- 5: no proxy without git_proxy ---------------------------------------------------------


@pytest.fixture
def proxy_listener():
    """A TCP listener standing in for a proxy: records each connection."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    sock.settimeout(0.2)
    seen: list[bytes] = []
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                continue
            conn.settimeout(2)
            try:
                seen.append(conn.recv(256))
            except OSError:
                seen.append(b"")
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    yield url, seen
    stop.set()
    sock.close()


def _registered_clone(git_host, tmp_path) -> Path:
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(git_host.root / "o" / "r.git"), str(work))
    _git(work, "remote", "set-url", "origin", git_host.url)
    return work


def test_network_git_ignores_a_repositorys_own_http_proxy(
    git_host, account, tmp_path, monkeypatch, proxy_listener
):
    url, seen = proxy_listener
    work = _registered_clone(git_host, tmp_path)
    _git(work, "config", "http.proxy", url)
    with local_process(False):
        ok, msg = GitService.pull(
            work,
            allowed_hosts=[git_host.host],
            credentials=[_operator(git_host.host, monkeypatch)],
            registered_remote=True,
        )
    assert seen == []
    assert ok is True, msg


def test_network_git_goes_through_git_proxy_when_set(
    git_host, account, tmp_path, monkeypatch, proxy_listener
):
    url, seen = proxy_listener
    work = _registered_clone(git_host, tmp_path)
    with local_process(False):
        GitService.pull(
            work,
            allowed_hosts=[git_host.host],
            credentials=[],
            registered_remote=True,
            proxy=url,
        )
    assert seen and seen[0].startswith(b"CONNECT 127.0.0.1:")


def test_git_proxy_is_checked_at_load_and_survives_a_round_trip(tmp_path):
    from tests._world import build_world

    with pytest.raises(ValueError, match="git_proxy"):
        Settings(git_proxy="socks5 nowhere")
    w = build_world(
        tmp_path / "w", settings={"git_proxy": "http://proxy.internal:3128"}, rest=False
    )
    try:
        assert w.config.settings.git_proxy == "http://proxy.internal:3128"
    finally:
        w.close()


def _proxy_of(client: httpx.Client) -> str | None:
    pool = client._transport_for_url(httpx.URL("https://api.github.com/user"))._pool
    proxy = getattr(pool, "_proxy_url", None)
    return None if proxy is None else f"{proxy.host.decode()}:{proxy.port}"


@pytest.fixture
def github_api(monkeypatch):
    """The GitHub API at the transport edge; records the proxy each request
    would go through."""
    for var in PROXY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://u:account-secret@127.0.0.1:9")
    proxies: list[str | None] = []

    def send(self, request, *a, **k):
        proxies.append(_proxy_of(self))
        if request.url.path.endswith("/forks"):
            return httpx.Response(202, json={"full_name": "me/r"}, request=request)
        if request.url.path.endswith("/pulls"):
            return httpx.Response(201, json={"html_url": "u", "number": 1}, request=request)
        if request.url.path.endswith("/user/repos"):
            return httpx.Response(200, json=[], request=request)
        return httpx.Response(200, json={"login": "me", "id": 7}, request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    return proxies


def _github_calls(proxy):
    from pyrite.github_auth import get_github_user_info

    GitService.fork_repo("o", "r", "CALLER", proxy=proxy)
    GitService.create_pull_request("o", "r", "t", "b", "me:x", "main", "CALLER", proxy=proxy)
    get_github_user_info("CALLER", proxy=proxy)


def test_a_network_callers_github_api_calls_use_no_account_proxy(github_api):
    with local_process(False):
        _github_calls(None)
    assert github_api == [None, None, None]


def test_a_network_callers_github_api_calls_use_git_proxy(github_api):
    with local_process(False):
        _github_calls("http://127.0.0.1:3128")
    assert github_api == ["127.0.0.1:3128"] * 3


@pytest.mark.control(reason="the operator's own CLI keeps its own environment's proxy")
def test_the_local_clis_github_api_calls_keep_its_proxy(github_api):
    with local_process(True):
        _github_calls(None)
    assert github_api == ["127.0.0.1:9"] * 3


def test_the_rest_github_repo_listing_uses_no_account_proxy(github_api, tmp_path):
    from pyrite.server.api import get_repo_service
    from pyrite.services.repo_service import RepoService
    from tests._world import build_world

    w = build_world(tmp_path / "w")
    try:
        svc = RepoService(w.config, w.db, github_token="CALLER")
        w.app.dependency_overrides[get_repo_service] = lambda: svc
        r = w.client.get("/api/github/repos")
    finally:
        w.close()
    assert r.status_code == 200, r.text
    assert github_api == [None]


# -- 6: a repository's own identity wins -------------------------------------------------


def test_a_repositorys_own_identity_wins_over_git_identity(account, tmp_path):
    repo = tmp_path / "kb"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Repo Own")
    _git(repo, "config", "user.email", "own@repo.example")
    (repo / "a.md").write_text("a\n")
    with local_process(False):
        ok, result = GitService.commit(
            repo, "m", identity=GitIdentity(name="Pyrite Bot", email="bot@pyrite.example")
        )
    assert ok, result
    assert _git(repo, "log", "-1", "--format=%an <%ae>") == "Repo Own <own@repo.example>"


# -- round 2: a grant that cannot be used gives no credential, not the account's ---------


def _ssh_entry(key: Path, tmp_path: Path) -> GitCredential:
    return GitCredential(
        host="git.example.org",
        ssh_key=key,
        known_hosts=tmp_path / "known_hosts",
        url_prefixes=("git@git.example.org:o/",),
    )


@pytest.mark.parametrize("shape", ["missing", "a directory"])
def test_an_operator_ssh_key_that_cannot_be_read_is_refused_before_ssh_runs(
    shape, tmp_path, monkeypatch
):
    key = tmp_path / "deploy_key"
    if shape == "a directory":
        key.mkdir()
    with local_process(False):
        ok, code, message = GitService.clone_with_code(
            "git@git.example.org:o/r.git",
            tmp_path / "ws",
            allowed_hosts=["git.example.org"],
            credentials=[_ssh_entry(key, tmp_path)],
        )
    assert (ok, code) == (False, "REMOTE_NOT_ALLOWED")
    assert "ssh_key" in message and str(key) not in message


@pytest.mark.skipif(shutil.which("ssh") is None, reason="needs the OpenSSH client")
def test_the_operator_key_command_reads_no_default_identity(tmp_path):
    """If the key goes missing between the plan and the command, ssh still
    offers no identity of the account (``-o IdentityFile=none``): `ssh -G`
    resolves the command's own options."""
    key = tmp_path / "deploy_key"
    key.write_text("k\n")
    with local_process(False):
        plan = _plan(["git@git.example.org:o/r.git"], [_ssh_entry(key, tmp_path)])
    key.unlink()
    argv = shlex.split(plan.env["GIT_SSH_COMMAND"])
    resolved = subprocess.run(
        [*argv, "-G", "git.example.org"], capture_output=True, text=True, timeout=30
    ).stdout
    identities = [
        line.split(" ", 1)[1] for line in resolved.splitlines() if line.startswith("identityfile ")
    ]
    assert identities and all(i in (str(key), "none") for i in identities), identities


_ENCODED = [
    "https://github.com/team/x%2f..%2fother/r",
    "https://github.com/team/x%2F..%2Fother/r",
    "https://github.com/team/x%5c..%5cother/r",
    "https://github.com/team/x%5C..%5Cother/r",
    pytest.param(
        "https://github.com/team/%2e%2e/other/r",
        marks=pytest.mark.control(
            reason="an encoded dot segment was already refused by the host rule"
        ),
    ),
    "https://github.com/team/x/%2E%2e%2fother",
    # Decoded twice, or as overlong UTF-8, by some server: still not plainly
    # under the prefix. Any "%" in the path is refused.
    "https://github.com/team/x%252f..%252fother/r",
    "https://github.com/team/%252e%252e/other/r",
    "https://github.com/team/%c0%ae%c0%ae/other/r",
    "https://github.com/team/x%41/r",
    pytest.param(
        "https://github.com/team/.%2e/other/r",
        marks=pytest.mark.control(
            reason="an encoded dot segment was already refused by the host rule"
        ),
    ),
]


@pytest.mark.parametrize("url", _ENCODED)
def test_an_encoded_separator_or_dot_segment_is_never_under_a_prefix(url, monkeypatch):
    monkeypatch.setenv("T_OP", "op-token")
    entry = GitCredential(
        host="github.com", token_env="T_OP", url_prefixes=("https://github.com/team/",)
    )
    with local_process(False):
        try:
            plan = _plan([url], [entry], registered=False)
        except RemoteNotAllowedError:
            plan = None
    assert plan is None or plan.credential == "none"


@pytest.mark.parametrize(
    "url", ["git@github.com:team/x%2f..%2fother/r", "ssh://git@github.com/team/x%5C..%5Cother/r"]
)
def test_an_encoded_separator_in_an_ssh_url_is_never_under_a_prefix(url, tmp_path):
    entry = GitCredential(
        host="github.com",
        ssh_key=tmp_path / "k",
        known_hosts=tmp_path / "kh",
        url_prefixes=("git@github.com:team/",),
    )
    (tmp_path / "k").write_text("k\n")
    with local_process(False), pytest.raises(RemoteNotAllowedError):
        # An ssh remote needs an operator key that serves it; none does.
        _plan([url], [entry], registered=False)


@pytest.mark.parametrize(
    ("entry", "says"),
    [
        (
            {"host": "github.com", "token_env": "A", "url_prefixes": ["https://github.com"]},
            "no repository path",
        ),
        (
            {"host": "github.com", "token_env": "A", "url_prefixes": ["https://github.com/"]},
            "no repository path",
        ),
        (
            {
                "host": "github.com",
                "ssh_key": "/k",
                "known_hosts": "/kh",
                "url_prefixes": ["git@github.com:"],
            },
            "no repository path",
        ),
        (
            {"host": "github.com", "token_env": "A", "url_prefixes": ["https://github.com/a%2fb/"]},
            "encoded",
        ),
        (
            {
                "host": "github.com",
                "token_env": "A",
                "url_prefixes": ["https://github.com/a/../b/"],
            },
            "dot segment",
        ),
        (
            {"host": "github.com", "token_env": "A", "url_prefixes": ["https://github.com/%252f/"]},
            "encoded",
        ),
        (
            {
                "host": "github.com",
                "token_env": "A",
                "url_prefixes": ["https://github.com/%c0%ae/"],
            },
            "encoded",
        ),
        (
            {"host": "github.com", "token_env": "A", "url_prefixes": ["https://github.com/t%41m/"]},
            "encoded",
        ),
    ],
)
def test_a_prefix_that_names_no_path_is_refused_at_load(entry, says):
    with pytest.raises(ValueError, match=says):
        Settings(git_credentials=[entry])


def test_a_network_callers_github_api_client_trusts_the_operators_ca_bundle(
    git_host, tmp_path, monkeypatch
):
    """``trust_env=False`` drops the proxy variables, not the CA-bundle ones
    network git keeps (U3): a server whose certificate only SSL_CERT_FILE
    vouches for is still reached."""
    from pyrite.utils.egress import github_client

    for var in PROXY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "cert.pem"))
    with local_process(False), github_client() as client:
        response = client.get(f"https://{git_host.host}/o/r.git/info/refs")
    assert response.status_code == 401  # the TLS handshake succeeded; the server asked for auth
