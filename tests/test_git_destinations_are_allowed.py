"""A git operation Pyrite runs for a caller clones from and pushes to an
operator-allowed network host, and every path it creates or writes resolves
inside the directory Pyrite chose for it.

The allowed hosts are the `git_allowed_hosts` setting (default `github.com`,
https only). The CLI is bound by it as well as the server, and a refusal names
the setting so the operator knows what to change.
"""

import subprocess
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.services.git_service import GitService

try:
    from pyrite.services.git_service import RemoteNotAllowedError
except ImportError:  # pragma: no cover - lets the file collect before the fix
    RemoteNotAllowedError = ValueError
from pyrite.storage.database import PyriteDB

# git subcommands that contact a remote. No test here may reach the network:
# the autouse guard fails any such call a test did not intercept itself.
_NETWORK_SUBCOMMANDS = {"clone", "pull", "push", "fetch", "ls-remote"}


def _subcommand(cmd) -> str | None:
    if not isinstance(cmd, list) or cmd[:1] != ["git"]:
        return None
    i = 1
    while i < len(cmd) and cmd[i] == "-c":
        i += 2
    return cmd[i] if i < len(cmd) else None


def _contacts_remote(cmd) -> bool:
    sub = _subcommand(cmd)
    # `ls-remote --get-url` only expands a URL locally.
    return sub in _NETWORK_SUBCOMMANDS and not (sub == "ls-remote" and "--get-url" in cmd)


@contextmanager
def _intercept_network(returncode: int = 0, stderr: str = ""):
    """Answer every remote-contacting git call (recording cmd and env); run
    every local one for real."""
    real_run = subprocess.run
    calls: list[tuple[list[str], dict]] = []

    def run(cmd, *args, **kwargs):
        if _contacts_remote(cmd):
            calls.append((cmd, kwargs.get("env") or {}))
            return subprocess.CompletedProcess(cmd, returncode, "", stderr)
        return real_run(cmd, *args, **kwargs)

    with patch("pyrite.services.git_service.subprocess.run", side_effect=run):
        yield calls


@pytest.fixture(autouse=True)
def _no_network_git():
    real_run = subprocess.run

    def run(cmd, *args, **kwargs):
        if _contacts_remote(cmd):
            pytest.fail(f"a test reached for the network: {cmd[:4]}")
        return real_run(cmd, *args, **kwargs)

    with patch("pyrite.services.git_service.subprocess.run", side_effect=run):
        yield


@pytest.fixture
def git_rewrites_github_to_a_local_path(tmp_path, monkeypatch):
    """The account's git config rewrites https://github.com/ to a local path.

    The account's global config applies only to the operator's own process: a
    network caller's command does not read it at all
    (``git_service._account_isolation_env``), so these run as the operator."""
    from pyrite.services.acting import local_process

    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(
        f'[url "file://{tmp_path}/mirror/"]\n\tinsteadOf = https://github.com/\n'
        f'[url "file://{tmp_path}/pushmirror/"]\n\tpushInsteadOf = https://github.com/\n'
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    with local_process(True):
        yield gitconfig


# ---------------------------------------------------------------------------
# The setting
# ---------------------------------------------------------------------------


class TestTheSetting:
    def test_default_allows_only_github(self):
        assert Settings().git_allowed_hosts == ["github.com"]

    def test_config_yaml_sets_it_and_it_survives_a_round_trip(self):
        data = {"settings": {"git_allowed_hosts": ["github.com", "git.example.org"]}}
        config = PyriteConfig.from_dict(data)
        assert config.settings.git_allowed_hosts == ["github.com", "git.example.org"]
        again = PyriteConfig.from_dict(config.to_dict())
        assert again.settings.git_allowed_hosts == ["github.com", "git.example.org"]

    def test_an_empty_list_allows_no_host_and_survives_a_round_trip(self):
        config = PyriteConfig.from_dict({"settings": {"git_allowed_hosts": []}})
        assert config.settings.git_allowed_hosts == []
        assert PyriteConfig.from_dict(config.to_dict()).settings.git_allowed_hosts == []

    def test_the_environment_overrides_it(self, monkeypatch):
        from pyrite.config import _apply_env_overrides

        config = PyriteConfig()
        monkeypatch.setenv("PYRITE_GIT_ALLOWED_HOSTS", "git.example.org, code.example.net")
        _apply_env_overrides(config)
        assert config.settings.git_allowed_hosts == ["git.example.org", "code.example.net"]


class TestTheSettingIsValidatedAtLoad:
    @pytest.mark.parametrize("value", [5, None, {"github.com": True}, True])
    def test_a_value_that_is_not_a_list_is_one_error_naming_the_setting(self, value):
        with pytest.raises(ValueError, match="git_allowed_hosts must be a list"):
            PyriteConfig.from_dict({"settings": {"git_allowed_hosts": value}})

    @pytest.mark.parametrize(
        "entry",
        [
            None,
            5,
            "https://github.com",
            "github.com/",
            "*.example.org",
            "git hub.com",
            "user@github.com",
            "github.com:0",
            "github.com:99999",
            "github.com.",
        ],
    )
    def test_an_entry_that_can_never_match_is_rejected_by_name(self, entry):
        with pytest.raises(ValueError, match="git_allowed_hosts entr"):
            Settings(git_allowed_hosts=["github.com", entry])

    def test_entries_are_normalised(self):
        settings = Settings(
            git_allowed_hosts=[" GitHub.com ", "github.com", "Git.Example.org:8443"]
        )
        assert settings.git_allowed_hosts == ["github.com", "git.example.org:8443"]

    def test_a_malformed_environment_value_names_the_variable(self, monkeypatch):
        from pyrite.config import _apply_env_overrides

        monkeypatch.setenv("PYRITE_GIT_ALLOWED_HOSTS", "https://git.example.org")
        with pytest.raises(ValueError, match="PYRITE_GIT_ALLOWED_HOSTS"):
            _apply_env_overrides(PyriteConfig())

    def test_an_empty_list_allows_nothing_and_the_refusal_says_so(self):
        with pytest.raises(RemoteNotAllowedError, match="git_allowed_hosts setting is empty"):
            GitService.check_remote_allowed("https://github.com/o/r", [])


# ---------------------------------------------------------------------------
# The rule, in one place
# ---------------------------------------------------------------------------

_REFUSED = [
    "/srv/some/repo",
    "./relative/repo",
    "file:///srv/some/repo",
    "http://github.com/o/r",
    "ssh://git@github.com/o/r.git",
    "git@github.com:o/r.git",
    "git://github.com/o/r.git",
    "ext::sh -c true",
    "https://gitlab.com/o/r",
    "https://github.com.example.net/o/r",
    "https://user@github.com/o/r",
    "https://github.com:8443/o/r",
    "https://github.com/o/r?x=1",
    "https:///o/r",
    "https://github.com/o/../x/r",
    "https://github.com/o/%2e%2e/x/r",
    "https://github.com/./o/r",
    "",
]


class TestTheRule:
    @pytest.mark.parametrize("url", _REFUSED)
    def test_only_https_on_an_allowed_host_is_accepted(self, url):
        with pytest.raises(RemoteNotAllowedError):
            GitService.check_remote_allowed(url, ["github.com"])

    @pytest.mark.parametrize(
        "url",
        ["https://github.com/o/r", "https://github.com/o/r.git", "https://GitHub.com/o/r/"],
    )
    def test_an_allowed_https_remote_passes(self, url):
        GitService.check_remote_allowed(url, ["github.com"])

    def test_another_host_passes_once_the_operator_lists_it(self):
        GitService.check_remote_allowed("https://git.example.org/o/r", ["git.example.org"])
        GitService.check_remote_allowed(
            "https://git.example.org:8443/o/r", ["git.example.org:8443"]
        )
        with pytest.raises(RemoteNotAllowedError):
            GitService.check_remote_allowed("https://github.com/o/r", ["git.example.org"])

    def test_the_refusal_names_the_setting_and_never_echoes_the_url(self):
        with pytest.raises(RemoteNotAllowedError) as exc:
            GitService.check_remote_allowed("https://tok3n@gitlab.com/o/r", ["github.com"])
        message = str(exc.value)
        assert "git_allowed_hosts" in message
        assert "tok3n" not in message

    def test_the_refusal_does_not_disclose_the_allowed_hosts(self):
        with pytest.raises(RemoteNotAllowedError) as exc:
            GitService.check_remote_allowed("https://gitlab.com/o/r", ["git.internal.example"])
        assert "git.internal.example" not in str(exc.value)


class TestCloneIsBound:
    @pytest.mark.parametrize("url", ["/srv/some/repo", "file:///srv/x", "https://gitlab.com/o/r"])
    def test_clone_refuses_before_git_runs(self, url, tmp_path):
        with patch("pyrite.services.git_service.subprocess.run") as run:
            ok, code, message = GitService.clone_with_code(url, tmp_path / "dest")
        assert ok is False
        assert code == "REMOTE_NOT_ALLOWED"
        assert "git_allowed_hosts" in message
        run.assert_not_called()

    def test_clone_follows_the_hosts_it_is_given(self, tmp_path):
        with _intercept_network() as calls:
            ok, _code, _msg = GitService.clone_with_code(
                "https://git.example.org/o/r", tmp_path / "d", allowed_hosts=["git.example.org"]
            )
        assert ok is True
        assert len(calls) == 1

    def test_every_network_guard_is_on_for_the_clone(self, tmp_path):
        with _intercept_network() as calls:
            GitService.clone_with_code("https://github.com/o/r", tmp_path / "d")
        ((cmd, env),) = calls
        assert env["GIT_ALLOW_PROTOCOL"] == "https"
        assert env["GIT_LFS_SKIP_SMUDGE"] == "1"
        assert cmd[:4] == ["git", "-c", "http.followRedirects=false", "clone"]

    def test_the_host_contacted_is_the_host_allowed_when_a_token_is_given(self, tmp_path):
        with _intercept_network() as calls:
            ok, _code, _msg = GitService.clone_with_code(
                "https://www.github.com/o/r",
                tmp_path / "d",
                token="tok",
                allowed_hosts=["www.github.com"],
            )
        assert ok is True
        ((cmd, env),) = calls
        assert cmd[cmd.index("--") + 1] == "https://www.github.com/o/r"
        assert "tok" not in " ".join(cmd)
        assert env["PYRITE_GIT_TOKEN"] == "tok"
        assert "tok" not in env["GIT_CONFIG_PARAMETERS"]

    def test_a_git_url_rewrite_to_another_transport_is_refused_and_says_so(
        self, tmp_path, git_rewrites_github_to_a_local_path
    ):
        with _intercept_network() as calls:
            ok, code, message = GitService.clone_with_code("https://github.com/o/r", tmp_path / "d")
        assert ok is False
        assert code == "REMOTE_NOT_ALLOWED"
        assert "insteadOf" in message
        assert "git_allowed_hosts" in message
        assert calls == []

    @pytest.mark.control(reason="a rewrite to an allowed https URL was always cloned; keeps it")
    def test_a_git_url_rewrite_to_an_allowed_https_url_still_clones(self, tmp_path, monkeypatch):
        gitconfig = tmp_path / "gitconfig"
        gitconfig.write_text(
            '[url "https://github.com/mirror-"]\n\tinsteadOf = https://github.com/\n'
        )
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
        with _intercept_network() as calls:
            result = GitService.clone_with_code("https://github.com/o/r", tmp_path / "d")
        assert result[0] is True
        assert len(calls) == 1

    def test_a_redirect_is_reported_as_one(self):
        code, message = GitService.classify_git_error(
            "fatal: unable to access 'https://github.com/o/r/': "
            "The requested URL returned error: 301\n"
        )
        assert code == "REMOTE_REDIRECTED"
        assert "redirect" in message


class TestPullIsBound:
    """sync pulls every subscription; each pull contacts only an allowed host."""

    def _work(self, tmp_path, origin: str) -> Path:
        work = tmp_path / "work"
        _git(tmp_path, "init", "-b", "main", str(work))
        (work / "a.md").write_text("a")
        _git(work, "add", "a.md")
        _git(work, "commit", "-m", "a")
        _git(work, "remote", "add", "origin", origin)
        _git(work, "config", "branch.main.remote", "origin")
        _git(work, "config", "branch.main.merge", "refs/heads/main")
        return work

    @pytest.mark.parametrize(
        "origin",
        ["git@github.com:o/r.git", "ssh://git@github.com/o/r", "LOCAL", "https://gitlab.com/o/r"],
    )
    def test_pull_refuses_a_remote_that_is_not_allowed_before_git_fetches(self, tmp_path, origin):
        if origin == "LOCAL":
            origin = str(tmp_path / "elsewhere.git")
        work = self._work(tmp_path, origin)
        with _intercept_network() as calls:
            ok, message = GitService.pull(work, allowed_hosts=["github.com"])
        assert ok is False
        assert "git_allowed_hosts" in message
        assert calls == []

    def test_pull_without_hosts_given_is_held_to_the_default(self, tmp_path):
        work = self._work(tmp_path, "git@github.com:o/r.git")
        with _intercept_network() as calls:
            ok, message = GitService.pull(work)
        assert ok is False
        assert calls == []

    def test_every_network_guard_is_on_for_an_allowed_pull(self, tmp_path):
        work = self._work(tmp_path, "https://github.com/o/r")
        with _intercept_network() as calls:
            ok, _ = GitService.pull(work, allowed_hosts=["github.com"])
        assert ok is True
        ((cmd, env),) = calls
        assert cmd[:4] == ["git", "-c", "http.followRedirects=false", "pull"]
        assert env["GIT_ALLOW_PROTOCOL"] == "https"
        assert env["GIT_LFS_SKIP_SMUDGE"] == "1"

    def test_every_fetch_url_of_the_remote_is_checked(self, tmp_path):
        work = self._work(tmp_path, "https://github.com/o/r")
        _git(work, "config", "--add", "remote.origin.url", str(tmp_path / "elsewhere.git"))
        with _intercept_network() as calls:
            ok, message = GitService.pull(work, allowed_hosts=["github.com"])
        assert ok is False
        assert calls == []

    def test_a_git_url_rewrite_on_pull_is_refused_and_says_so(
        self, tmp_path, git_rewrites_github_to_a_local_path
    ):
        work = self._work(tmp_path, "https://github.com/o/r")
        with _intercept_network() as calls:
            ok, message = GitService.pull(work, allowed_hosts=["github.com"])
        assert ok is False
        assert "insteadOf" in message
        assert calls == []

    def test_a_redirect_on_pull_says_what_happened(self, tmp_path):
        work = self._work(tmp_path, "https://github.com/o/r")
        stderr = "fatal: unable to access 'https://github.com/o/r/': The requested URL returned error: 301\n"
        with _intercept_network(returncode=128, stderr=stderr):
            ok, message = GitService.pull(work, allowed_hosts=["github.com"])
        assert ok is False
        assert "redirect" in message

    def test_sync_refuses_a_subscription_whose_remote_is_no_longer_allowed(self, tmp_path):
        from pyrite.services.repo_service import RepoService

        work = self._work(tmp_path, "git@github.com:o/r.git")
        config = PyriteConfig(
            settings=Settings(index_path=tmp_path / "index.db", workspace_path=tmp_path / "ws")
        )
        db = PyriteDB(tmp_path / "index.db")
        try:
            db.register_repo(name="o/r", local_path=str(work), remote_url="git@github.com:o/r.git")
            with (
                patch("pyrite.services.repo_service.get_github_token", return_value=None),
                _intercept_network() as calls,
            ):
                result = RepoService(config, db).sync("o/r")
        finally:
            db.close()
        outcome = result["repos"]["o/r"] if "repos" in result else result
        assert outcome["success"] is False
        assert "git_allowed_hosts" in outcome["error"]
        assert calls == []


class TestSyncFollowsTheSetting:
    def test_sync_refuses_a_host_since_removed_from_the_setting(self, tmp_path):
        from pyrite.services.repo_service import RepoService

        work = TestPullIsBound()._work(tmp_path, "https://github.com/o/r")
        config = PyriteConfig(
            settings=Settings(
                index_path=tmp_path / "index.db",
                workspace_path=tmp_path / "ws",
                git_allowed_hosts=["git.example.org"],
            )
        )
        db = PyriteDB(tmp_path / "index.db")
        try:
            db.register_repo(name="o/r", local_path=str(work), remote_url="https://github.com/o/r")
            with (
                patch("pyrite.services.repo_service.get_github_token", return_value=None),
                _intercept_network() as calls,
            ):
                result = RepoService(config, db).sync("o/r")
        finally:
            db.close()
        outcome = result["repos"]["o/r"]
        assert outcome["success"] is False
        assert "git_allowed_hosts" in outcome["error"]
        assert calls == []


class TestAddRemoteIsBound:
    def _work(self, tmp_path) -> Path:
        work = tmp_path / "work"
        _git(tmp_path, "init", "-b", "main", str(work))
        return work

    def _remotes(self, work) -> str:
        return subprocess.run(
            ["git", "remote", "-v"], cwd=work, capture_output=True, text=True, timeout=60
        ).stdout

    @pytest.mark.parametrize(
        "url", ["git@github.com:o/r.git", "/srv/repo", "https://gitlab.com/o/r"]
    )
    def test_add_remote_refuses_a_url_that_is_not_allowed(self, tmp_path, url):
        work = self._work(tmp_path)
        ok, message = GitService.add_remote(work, "upstream", url)
        assert ok is False
        assert "git_allowed_hosts" in message
        assert self._remotes(work) == ""

    def test_add_remote_never_reads_a_value_as_an_option(self, tmp_path):
        work = self._work(tmp_path)
        real_run = subprocess.run
        seen = []

        def run(cmd, *args, **kwargs):
            if cmd[:3] == ["git", "remote", "add"]:
                seen.append(cmd)
            return real_run(cmd, *args, **kwargs)

        with patch("pyrite.services.git_service.subprocess.run", side_effect=run):
            ok, _ = GitService.add_remote(work, "upstream", "https://github.com/o/r")
            refused, _ = GitService.add_remote(work, "--mirror=fetch", "https://github.com/o/r")
        assert ok is True
        assert seen[0][:5] == ["git", "remote", "add", "--", "upstream"]
        assert refused is False
        assert "mirror" not in self._remotes(work)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        timeout=60,
        env=GitService.subprocess_env(
            {
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@example.org",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@example.org",
            }
        ),
    )


class TestPushIsBoundWhenAsked:
    def test_push_with_allowed_hosts_refuses_a_remote_that_is_not_allowed(self, tmp_path):
        target = tmp_path / "target.git"
        _git(tmp_path, "init", "--bare", "-b", "main", str(target))
        work = tmp_path / "work"
        _git(tmp_path, "init", "-b", "main", str(work))
        (work / "a.md").write_text("a")
        _git(work, "add", "a.md")
        _git(work, "commit", "-m", "a")
        _git(work, "remote", "add", "origin", str(target))

        ok, message = GitService.push(
            work, remote="origin", branch="main", allowed_hosts=["github.com"]
        )
        assert ok is False
        assert "git_allowed_hosts" in message
        # Nothing reached the local path.
        refs = subprocess.run(
            ["git", "for-each-ref"], cwd=target, capture_output=True, text=True, timeout=60
        ).stdout
        assert refs.strip() == ""

    def _work(self, tmp_path, origin: str) -> Path:
        work = tmp_path / "work"
        _git(tmp_path, "init", "-b", "main", str(work))
        (work / "a.md").write_text("a")
        _git(work, "add", "a.md")
        _git(work, "commit", "-m", "a")
        _git(work, "remote", "add", "origin", origin)
        return work

    def test_every_network_guard_is_on_for_an_allowed_push(self, tmp_path):
        work = self._work(tmp_path, "https://github.com/o/r")
        with _intercept_network() as calls:
            ok, _ = GitService.push(
                work, remote="origin", branch="main", allowed_hosts=["github.com"]
            )
        assert ok is True
        ((cmd, env),) = calls
        assert cmd[:4] == ["git", "-c", "http.followRedirects=false", "push"]
        assert env["GIT_ALLOW_PROTOCOL"] == "https"
        assert env["GIT_LFS_SKIP_PUSH"] == "1"

    def test_every_push_url_of_the_remote_is_checked(self, tmp_path):
        work = self._work(tmp_path, "https://github.com/o/r")
        _git(work, "remote", "set-url", "--add", "--push", "origin", "https://github.com/o/r")
        _git(work, "remote", "set-url", "--add", "--push", "origin", str(tmp_path / "t.git"))
        with _intercept_network() as calls:
            ok, message = GitService.push(
                work, remote="origin", branch="main", allowed_hosts=["github.com"]
            )
        assert ok is False
        assert "git_allowed_hosts" in message
        assert calls == []

    def test_a_git_url_rewrite_on_push_is_refused_and_says_so(
        self, tmp_path, git_rewrites_github_to_a_local_path
    ):
        work = self._work(tmp_path, "https://github.com/o/r")
        with _intercept_network() as calls:
            ok, message = GitService.push(
                work, remote="origin", branch="main", allowed_hosts=["github.com"]
            )
        assert ok is False
        assert "insteadOf" in message
        assert calls == []


# ---------------------------------------------------------------------------
# Export to repo
# ---------------------------------------------------------------------------


@pytest.fixture
def export_env(tmp_path):
    from pyrite.services.export_service import ExportService
    from pyrite.storage.index import IndexManager

    kb_path = tmp_path / "kb"
    (kb_path / "note").mkdir(parents=True)
    (kb_path / "kb.yaml").write_text("name: test-kb\nkb_type: generic\n")
    (kb_path / "note" / "hello.md").write_text("---\ntitle: Hello\ntype: note\n---\nHello")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="test-kb", path=kb_path, kb_type=KBType.GENERIC)],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    with PyriteDB(tmp_path / "index.db") as db:
        IndexManager(db, config).sync_incremental("test-kb")
        yield ExportService(config, db), config, tmp_path


class TestExportDestination:
    @pytest.mark.parametrize(
        "url",
        ["/srv/target", "file:///srv/target", "git@github.com:o/r.git", "https://x.example/r"],
    )
    def test_export_refuses_a_destination_that_is_not_allowed_before_any_git_call(
        self, url, export_env
    ):
        service, _, _ = export_env
        with patch("pyrite.services.git_service.subprocess.run") as run:
            result = service.export_kb_to_repo("test-kb", url)
        assert result["success"] is False
        assert result["error_code"] == "REMOTE_NOT_ALLOWED"
        assert "git_allowed_hosts" in result["error"]
        run.assert_not_called()

    def test_export_uses_the_operator_setting(self, export_env):
        service, config, _ = export_env
        config.settings.git_allowed_hosts = ["git.example.org"]
        seen = {}

        def fake_clone(url, path, **kwargs):
            seen.update(kwargs)
            return False, "nope"

        with patch.object(GitService, "clone", side_effect=fake_clone):
            result = service.export_kb_to_repo("test-kb", "https://git.example.org/o/r")
        assert result["success"] is False
        assert seen["allowed_hosts"] == ["git.example.org"]


def _fake_clone_with(layout):
    """A GitService.clone stand-in producing a checkout shaped by `layout`."""

    def fake_clone(url, path, **kwargs):
        path.mkdir(parents=True)
        (path / ".git").mkdir()
        layout(path)
        return True, "Cloned"

    return fake_clone


class TestExportWritesStayInsideTheClone:
    def _run(self, service, layout):
        with (
            patch.object(GitService, "clone", side_effect=_fake_clone_with(layout)),
            patch.object(GitService, "commit") as commit,
            patch.object(GitService, "push") as push,
        ):
            result = service.export_kb_to_repo("test-kb", "https://github.com/o/r")
        return result, commit, push

    @pytest.mark.parametrize("where", ["kb", "type"])
    def test_a_link_in_the_clone_is_never_written_through(self, export_env, where):
        service, _, tmp_path = export_env
        outside = tmp_path / "outside"
        outside.mkdir()

        def layout(clone):
            if where == "kb":
                (clone / "test-kb").symlink_to(outside, target_is_directory=True)
            else:
                (clone / "test-kb").mkdir()
                (clone / "test-kb" / "note").symlink_to(outside, target_is_directory=True)

        result, commit, push = self._run(service, layout)
        assert result["success"] is False
        assert result["error_code"] == "UNSAFE_DESTINATION"
        assert list(outside.iterdir()) == []
        commit.assert_not_called()
        push.assert_not_called()

    def test_a_dangling_link_in_the_clone_is_refused_not_followed(self, export_env):
        service, _, tmp_path = export_env
        missing = tmp_path / "not-there"

        def layout(clone):
            (clone / "test-kb").mkdir()
            (clone / "test-kb" / "note").symlink_to(missing, target_is_directory=True)

        result, commit, push = self._run(service, layout)
        assert result["success"] is False
        assert result["error_code"] == "UNSAFE_DESTINATION"
        assert not missing.exists()
        push.assert_not_called()

    def test_a_linked_kb_yaml_in_the_clone_is_never_written_through(self, export_env):
        service, _, tmp_path = export_env
        victim = tmp_path / "victim.yaml"
        victim.write_text("unchanged")

        def layout(clone):
            (clone / "test-kb").mkdir()
            (clone / "test-kb" / "kb.yaml").symlink_to(victim)

        result, commit, push = self._run(service, layout)
        assert result["success"] is False
        assert result["error_code"] == "UNSAFE_DESTINATION"
        assert victim.read_text() == "unchanged"
        push.assert_not_called()

    def test_a_linked_file_in_the_clone_is_never_written_through(self, export_env):
        service, _, tmp_path = export_env
        victim = tmp_path / "victim.txt"
        victim.write_text("unchanged")

        def layout(clone):
            (clone / "test-kb" / "note").mkdir(parents=True)
            (clone / "test-kb" / "note" / "hello.md").symlink_to(victim)

        result, commit, push = self._run(service, layout)
        assert result["success"] is False
        assert victim.read_text() == "unchanged"
        push.assert_not_called()

    @pytest.mark.control(reason="a plain clone already exported correctly; keeps it working")
    def test_a_plain_clone_still_exports(self, export_env):
        service, _, _ = export_env
        written = {}

        def layout(clone):
            written["root"] = clone

        with (
            patch.object(GitService, "clone", side_effect=_fake_clone_with(layout)),
            patch.object(GitService, "commit", return_value=(True, {"commit_hash": "abc"})),
            patch.object(GitService, "push", return_value=(True, "ok")),
        ):
            result = service.export_kb_to_repo("test-kb", "https://github.com/o/r")
        assert result["success"] is True
        assert result["entries_exported"] == 1


# ---------------------------------------------------------------------------
# Subscribe / fork: owner and repo segments, and the workspace path
# ---------------------------------------------------------------------------


class TestGithubSegments:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/../repo",
            "https://github.com/./repo",
            "https://github.com/.hidden/repo",
            "https://github.com/owner/.hidden",
            "https://github.com/a/b/../../c/d",
            "https://github.com/a/b/./c",
            "https://github.com/a/b/tree/main",
        ],
    )
    def test_owner_and_repo_refuse_dot_segments_and_leading_dots(self, url):
        assert GitService.parse_github_url(url) is None

    @pytest.mark.control(reason="the repo segment already refused . and ..; keeps it refused")
    @pytest.mark.parametrize("url", ["https://github.com/owner/..", "https://github.com/owner/."])
    def test_repo_still_refuses_dot_segments(self, url):
        assert GitService.parse_github_url(url) is None


@pytest.fixture
def repo_env(tmp_path):
    from pyrite.services.repo_service import RepoService

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = PyriteConfig(
        settings=Settings(index_path=tmp_path / "index.db", workspace_path=workspace)
    )
    db = PyriteDB(tmp_path / "index.db")
    yield RepoService(config, db), config, tmp_path
    db.close()


class TestSubscribeIsBound:
    @patch("pyrite.services.repo_service.get_github_token", return_value=None)
    def test_subscribe_refuses_a_remote_that_is_not_allowed_before_anything_is_created(
        self, _tok, repo_env
    ):
        svc, config, _ = repo_env
        with patch("pyrite.services.git_service.subprocess.run") as run:
            result = svc.subscribe("git@github.com:owner/repo.git")
        assert result["success"] is False
        assert result["error_code"] == "REMOTE_NOT_ALLOWED"
        assert "git_allowed_hosts" in result["error"]
        run.assert_not_called()
        assert list(config.settings.workspace_path.iterdir()) == []

    @patch("pyrite.services.repo_service.get_github_token", return_value=None)
    def test_subscribe_follows_the_setting(self, _tok, repo_env):
        svc, config, _ = repo_env
        config.settings.git_allowed_hosts = ["git.example.org"]
        result = svc.subscribe("https://github.com/owner/repo")
        assert result["success"] is False
        assert result["error_code"] == "REMOTE_NOT_ALLOWED"

    @patch("pyrite.services.repo_service.get_github_token", return_value="tok")
    def test_fork_refuses_before_calling_github(self, _tok, repo_env):
        svc, config, _ = repo_env
        config.settings.git_allowed_hosts = ["git.example.org"]
        with patch.object(GitService, "fork_repo") as fork:
            result = svc.fork_and_subscribe("https://github.com/owner/repo")
        assert result["success"] is False
        assert result["error_code"] == "REMOTE_NOT_ALLOWED"
        fork.assert_not_called()

    @patch("pyrite.services.repo_service.get_github_token", return_value="tok")
    def test_the_fork_clone_url_is_held_to_the_rule_too(self, _tok, repo_env):
        svc, config, _ = repo_env
        fork = (True, {"clone_url": "git@github.com:me/repo.git", "full_name": "me/repo"})
        with (
            patch.object(GitService, "fork_repo", return_value=fork),
            patch("pyrite.services.git_service.subprocess.run") as run,
        ):
            result = svc.fork_and_subscribe("https://github.com/owner/repo")
        assert result["success"] is False
        assert result["error_code"] == "REMOTE_NOT_ALLOWED"
        run.assert_not_called()
        assert list(config.settings.workspace_path.iterdir()) == []

    def test_the_clone_path_must_resolve_inside_the_workspace(self, repo_env):
        svc, config, tmp_path = repo_env
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        # An owner directory that already leads out of the workspace root.
        (config.settings.workspace_path / "owner").symlink_to(outside, target_is_directory=True)
        with patch.object(GitService, "clone_with_code") as clone:
            result = svc.subscribe("https://github.com/owner/repo")
        assert result["success"] is False
        assert result["error_code"] == "INVALID_REQUEST"
        clone.assert_not_called()
        assert list(outside.iterdir()) == []
