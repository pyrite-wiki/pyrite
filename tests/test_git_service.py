"""Tests for GitService — subprocess calls mocked."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrite.services.git_service import GitService


class TestParseGithubUrl:
    """Tests for URL parsing (no mocks needed)."""

    def test_https_url(self):
        result = GitService.parse_github_url("https://github.com/org/repo")
        assert result == ("org", "repo")

    def test_https_url_with_git(self):
        result = GitService.parse_github_url("https://github.com/org/repo.git")
        assert result == ("org", "repo")

    def test_ssh_url(self):
        result = GitService.parse_github_url("git@github.com:org/repo.git")
        assert result == ("org", "repo")

    def test_trailing_slash(self):
        result = GitService.parse_github_url("https://github.com/org/repo/")
        assert result == ("org", "repo")

    def test_invalid_url(self):
        result = GitService.parse_github_url("https://gitlab.com/org/repo")
        assert result is None

    def test_incomplete_url(self):
        result = GitService.parse_github_url("https://github.com/org")
        assert result is None


class TestCredentialsAreOfferedOnlyToGitHub:
    """`_credentials` hands git a token only for https GitHub URLs."""

    def test_https_github_gets_a_scoped_helper(self):
        creds = GitService._credentials("mytoken", ["https://github.com/org/repo.git"])
        assert creds.env["PYRITE_GIT_TOKEN"] == "mytoken"
        config = dict(creds.config)
        assert config["credential.helper"] == ""  # configured helpers reset
        assert "credential.https://github.com.helper" in config
        assert "mytoken" not in str(creds.config)

    # No token offered: for a network caller (the default, nothing bound) the
    # server account's helpers are still reset; the local operator keeps them.
    # (and the account's other sources are closed: see
    # tests/test_git_credentials_never_leak.py, "every credential source").
    _RESET = (("credential.helper", ""), ("core.askPass", ""))

    def test_a_non_https_url_gets_nothing(self):
        creds = GitService._credentials("mytoken", ["git@github.com:org/repo.git"])
        assert "PYRITE_GIT_TOKEN" not in creds.env and creds.config == self._RESET

    def test_no_token_gets_nothing(self):
        creds = GitService._credentials(None, ["https://github.com/org/repo.git"])
        assert "PYRITE_GIT_TOKEN" not in creds.env and creds.config == self._RESET

    @pytest.mark.control(reason="the operator keeps their helpers, as before")
    def test_no_token_for_the_local_operator_leaves_their_helpers(self):
        from pyrite.services.acting import local_process

        with local_process(True):
            creds = GitService._credentials(None, ["https://github.com/org/repo.git"])
        assert creds.env == {} and creds.config == ()

    def test_one_foreign_url_among_several_withholds_it(self):
        urls = ["https://github.com/o/r", "https://evil.example/o/r"]
        assert "PYRITE_GIT_TOKEN" not in GitService._credentials("mytoken", urls).env


class TestSanitizeOutput:
    """Tests for _sanitize_output."""

    def test_removes_token(self):
        output = GitService._sanitize_output("error with token abc123", "abc123")
        assert "abc123" not in output
        assert "***" in output

    def test_no_token_passthrough(self):
        output = GitService._sanitize_output("some error", None)
        assert output == "some error"


class TestClone:
    """Tests for clone() with subprocess mocked."""

    @pytest.fixture(autouse=True)
    def _urls_already_checked(self, monkeypatch):
        """These tests are about the git command; the host rule's own calls to
        git are tested in tests/test_git_destinations_are_allowed.py."""
        monkeypatch.setattr(GitService, "_effective_url", staticmethod(lambda url, cwd=None: url))
        monkeypatch.setattr(GitService, "_check_remote_urls", staticmethod(lambda *a, **k: None))
        monkeypatch.setattr(GitService, "_pull_remote", staticmethod(lambda path: "origin"))

    @patch("pyrite.services.git_service.subprocess.run")
    def test_clone_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        success, msg = GitService.clone("https://github.com/org/repo", Path("/tmp/test"))
        assert success is True
        assert "Cloned" in msg
        mock_run.assert_called_once()

    @patch("pyrite.services.git_service.subprocess.run")
    def test_clone_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="fatal: not found")
        success, msg = GitService.clone("https://github.com/org/repo", Path("/tmp/test"))
        assert success is False
        assert "failed" in msg.lower()

    @patch("pyrite.services.git_service.subprocess.run")
    def test_clone_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=120)
        success, msg = GitService.clone("https://github.com/org/repo", Path("/tmp/test"))
        assert success is False
        assert "timed out" in msg.lower()

    @patch("pyrite.services.git_service.subprocess.run")
    def test_clone_with_depth(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        GitService.clone("https://github.com/org/repo", Path("/tmp/test"), depth=1)
        cmd = mock_run.call_args[0][0]
        assert "--depth" in cmd
        assert "1" in cmd

    @patch("pyrite.services.git_service.subprocess.run")
    def test_clone_full(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        GitService.clone("https://github.com/org/repo", Path("/tmp/test"), depth=None)
        cmd = mock_run.call_args[0][0]
        assert "--depth" not in cmd


class TestPull:
    """Tests for pull() with subprocess mocked."""

    @pytest.fixture(autouse=True)
    def _urls_already_checked(self, monkeypatch):
        """These tests are about the git command; the host rule's own calls to
        git are tested in tests/test_git_destinations_are_allowed.py."""
        monkeypatch.setattr(GitService, "_effective_url", staticmethod(lambda url, cwd=None: url))
        monkeypatch.setattr(GitService, "_check_remote_urls", staticmethod(lambda *a, **k: None))
        monkeypatch.setattr(GitService, "_pull_remote", staticmethod(lambda path: "origin"))

    @patch("pyrite.services.git_service.subprocess.run")
    def test_pull_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="Already up to date")
        success, msg = GitService.pull(Path("/tmp/test"))
        assert success is True

    @patch("pyrite.services.git_service.subprocess.run")
    def test_pull_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="merge conflict")
        success, msg = GitService.pull(Path("/tmp/test"))
        assert success is False


class TestGetters:
    """Tests for git info getters."""

    @patch("pyrite.services.git_service.subprocess.run")
    def test_get_remote_url(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="https://github.com/org/repo.git\n")
        url = GitService.get_remote_url(Path("/tmp/test"))
        assert url == "https://github.com/org/repo.git"

    @patch("pyrite.services.git_service.subprocess.run")
    def test_get_remote_url_none(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        url = GitService.get_remote_url(Path("/tmp/test"))
        assert url is None

    @patch("pyrite.services.git_service.subprocess.run")
    def test_get_current_branch(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="feature-branch\n")
        branch = GitService.get_current_branch(Path("/tmp/test"))
        assert branch == "feature-branch"

    @patch("pyrite.services.git_service.subprocess.run")
    def test_get_head_commit(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="abc123def456\n")
        commit = GitService.get_head_commit(Path("/tmp/test"))
        assert commit == "abc123def456"

    @patch("pyrite.services.git_service.subprocess.run")
    def test_is_git_repo_true(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert GitService.is_git_repo(Path("/tmp/test")) is True

    @patch("pyrite.services.git_service.subprocess.run")
    def test_is_git_repo_false(self, mock_run):
        mock_run.return_value = MagicMock(returncode=128)
        assert GitService.is_git_repo(Path("/tmp/test")) is False


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout.strip()


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@test.com")
    _git(path, "config", "user.name", "Test")


class TestGetFileLog:
    """Tests for get_file_log, against real git repos -- the output shape
    (-z NUL-separated, quoted-path handling, rename detection thresholds)
    cannot be trusted from a hand-written mock (#432 cold read)."""

    def test_get_file_log(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "test.md").write_text("v1")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "Initial commit")
        c1 = _git(repo, "rev-parse", "HEAD")

        (repo / "test.md").write_text("v2")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "Update entry")
        c2 = _git(repo, "rev-parse", "HEAD")

        log = GitService.get_file_log(repo, "test.md")
        assert len(log) == 2
        assert log[0]["hash"] == c2
        assert log[0]["message"] == "Update entry"
        assert log[0]["file_path"] == "test.md"
        assert log[1]["hash"] == c1
        assert log[1]["file_path"] == "test.md"

    def test_get_file_log_reports_the_path_at_each_commit(self, tmp_path):
        """A commit before a rename reports the *old* path -- what
        get_entry_at_version needs to read at that commit's tree (#432),
        not the path passed in to follow the file's history."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "a.md").write_text("v1")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "Initial")
        c0 = _git(repo, "rev-parse", "HEAD")

        _git(repo, "mv", "a.md", "b.md")
        _git(repo, "commit", "-q", "-m", "Rename")
        c1 = _git(repo, "rev-parse", "HEAD")

        (repo / "b.md").write_text("v2")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "After rename")
        c2 = _git(repo, "rev-parse", "HEAD")

        log = GitService.get_file_log(repo, "b.md")
        assert [(entry["hash"], entry["file_path"]) for entry in log] == [
            (c2, "b.md"),
            (c1, "b.md"),
            (c0, "a.md"),
        ]

    def test_get_file_log_unquotes_a_unicode_filename(self, tmp_path):
        """core.quotePath quotes a non-ASCII name in git's plain output;
        -z must be used so the path comes back raw, not as the literal
        quoted-escaped string "caf\\303\\251.md" (#432 cold read)."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        name = "café.md"
        (repo / name).write_text("v1")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "add unicode")

        log = GitService.get_file_log(repo, name)
        assert len(log) == 1
        assert log[0]["file_path"] == name

    def test_get_file_log_unquotes_a_tab_in_filename(self, tmp_path):
        """A tab in a filename is quote-escaped by git's plain output too;
        -z must be used so field-splitting on a literal tab in
        --name-status cannot be confused by one embedded in the path."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        name = "a\tb.md"
        (repo / name).write_text("v1")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "add tab name")

        log = GitService.get_file_log(repo, name)
        assert len(log) == 1
        assert log[0]["file_path"] == name

    @pytest.mark.control(
        reason="get_file_log's empty-result behaviour for a nonexistent "
        "file predates #432 and is unchanged by it (converted off a mock "
        "to a real repo per the coordinator's cold read, not a new claim)."
    )
    def test_get_file_log_empty(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "other.md").write_text("v1")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "unrelated")

        log = GitService.get_file_log(repo, "nonexistent.md")
        assert log == []

    @pytest.mark.control(
        reason="get_file_log's since_commit range-filtering predates #432 "
        "and is unchanged by it (converted off a mock to a real repo per "
        "the coordinator's cold read, not a new claim)."
    )
    def test_get_file_log_with_since(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "test.md").write_text("v1")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "v1")
        c1 = _git(repo, "rev-parse", "HEAD")

        (repo / "test.md").write_text("v2")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "v2")

        log = GitService.get_file_log(repo, "test.md", since_commit=c1)
        assert [entry["message"] for entry in log] == ["v2"]


class TestGetCommitFiles:
    """Tests for get_commit_file_changes, against real repos (quoted-path handling
    cannot be trusted from a mock)."""

    def test_get_commit_files_plain(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "a.md").write_text("v1")
        (repo / "b.md").write_text("v1")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "add two")
        commit = _git(repo, "rev-parse", "HEAD")

        files = [c[1] for c in GitService.get_commit_file_changes(repo, commit)]
        assert sorted(files) == ["a.md", "b.md"]

    def test_get_commit_files_unquotes_unicode(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        name = "café.md"
        (repo / name).write_text("v1")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "add unicode")
        commit = _git(repo, "rev-parse", "HEAD")

        files = [c[1] for c in GitService.get_commit_file_changes(repo, commit)]
        assert files == [name]

    def test_get_commit_files_unquotes_tab(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        name = "a\tb.md"
        (repo / name).write_text("v1")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "add tab name")
        commit = _git(repo, "rev-parse", "HEAD")

        files = [c[1] for c in GitService.get_commit_file_changes(repo, commit)]
        assert files == [name]

    def test_get_commit_files_root_commit(self, tmp_path):
        """git show --name-only handles the root commit (no parent) the
        same way as any other."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "a.md").write_text("v1")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "root")
        commit = _git(repo, "rev-parse", "HEAD")

        assert GitService.get_commit_file_changes(repo, commit) == [("A", "a.md", None)]


class TestGetChangedFiles:
    """Tests for get_changed_files."""

    @patch("pyrite.services.git_service.subprocess.run")
    def test_get_changed_files_since_commit(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="actors/alice.md\nevents/2025-01-20.md\n",
        )
        files = GitService.get_changed_files(Path("/tmp/test"), since_commit="abc123")
        assert len(files) == 2
        assert "actors/alice.md" in files

    @patch("pyrite.services.git_service.subprocess.run")
    def test_get_changed_files_all(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="actors/alice.md\n",
        )
        files = GitService.get_changed_files(Path("/tmp/test"))
        assert len(files) == 1
        # Without since_commit, should use ls-files
        cmd = mock_run.call_args[0][0]
        assert "ls-files" in cmd


class TestAddRemote:
    """Tests for add_remote."""

    @patch("pyrite.services.git_service.subprocess.run")
    def test_add_remote_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        success, msg = GitService.add_remote(
            Path("/tmp/test"), "upstream", "https://github.com/org/repo"
        )
        assert success is True

    @patch("pyrite.services.git_service.subprocess.run")
    def test_add_remote_already_exists(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stderr="error: remote upstream already exists."
        )
        success, msg = GitService.add_remote(
            Path("/tmp/test"), "upstream", "https://github.com/org/repo"
        )
        assert success is False


class TestGitHubHostMatching:
    """The host must *be* github.com. Matching it as a substring let a URL like
    https://evil.example/github.com/a/b parse as a GitHub repo and -- worse --
    receive the caller's OAuth token in its userinfo."""

    HOSTILE = [
        "https://evil.example/github.com/org/repo",
        "https://github.com.evil.example/org/repo",
        "https://evilgithub.com/org/repo",
        "https://evil.example/?x=github.com/org/repo",
        "https://github.com@evil.example/org/repo",
        "https://evil.example/org/repo#github.com",
        "git@evil.example:github.com/org/repo.git",
    ]

    @pytest.mark.parametrize("url", HOSTILE)
    def test_token_never_offered_for_non_github_host(self, url):
        assert "PYRITE_GIT_TOKEN" not in GitService._credentials("SECRET", [url]).env

    @pytest.mark.parametrize("url", HOSTILE)
    def test_non_github_host_does_not_parse(self, url):
        assert GitService.parse_github_url(url) is None

    def test_http_scheme_never_gets_token(self):
        assert (
            "PYRITE_GIT_TOKEN"
            not in GitService._credentials("SECRET", ["http://github.com/org/repo"]).env
        )

    def test_www_and_case_variants_still_work(self):
        assert GitService.parse_github_url("https://GitHub.com/org/repo") == ("org", "repo")
        assert GitService.parse_github_url("https://www.github.com/org/repo") == ("org", "repo")

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/-org/repo",
            "https://github.com/org/--upload-pack=touch${IFS}pwned",
            "https://github.com/org/re po",
            "https://github.com/org/..",
        ],
    )
    def test_owner_and_repo_names_are_validated(self, url):
        assert GitService.parse_github_url(url) is None


class TestGitArgumentInjection:
    """Caller-supplied values must never be parsed by git as options."""

    def test_clone_separates_positionals_with_double_dash(self, tmp_path):
        with patch("pyrite.services.git_service.subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            GitService.clone("https://github.com/org/repo", tmp_path / "dest")
        cmd = run.call_args[0][0]
        assert "--" in cmd
        assert cmd.index("--") < cmd.index("https://github.com/org/repo")

    @pytest.mark.parametrize("url", ["--upload-pack=touch /tmp/pwned", "-oProxyCommand=x"])
    def test_clone_refuses_option_shaped_url(self, url, tmp_path):
        with patch("pyrite.services.git_service.subprocess.run") as run:
            ok, msg = GitService.clone(url, tmp_path / "dest")
        assert not ok
        run.assert_not_called()

    def test_clone_refuses_option_shaped_branch(self, tmp_path):
        with patch("pyrite.services.git_service.subprocess.run") as run:
            ok, _ = GitService.clone(
                "https://github.com/org/repo", tmp_path / "dest", branch="--upload-pack=x"
            )
        assert not ok
        run.assert_not_called()

    def test_commit_stages_paths_after_double_dash(self, tmp_path):
        with (
            patch.object(GitService, "is_git_repo", return_value=True),
            patch("pyrite.services.git_service.subprocess.run") as run,
        ):
            run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            GitService.commit(tmp_path, "msg", paths=["--force"])
        add_cmd = next(c[0][0] for c in run.call_args_list if c[0][0][:2] == ["git", "add"])
        assert add_cmd == ["git", "add", "--", "--force"]


class TestShow:
    """#1049: a path at a revision is read in GitService, not by its callers."""

    def test_show_returns_the_committed_text_and_none_for_an_uncommitted_path(self, tmp_path):
        from tests.test_worktree_service import _clean_git_env, _init_git_repo

        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        env = _clean_git_env()
        (repo / "note.md").write_text("first\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "note.md"],
            cwd=repo,
            check=True,
            capture_output=True,
            env=env,
            timeout=30,
        )
        subprocess.run(
            ["git", "commit", "-m", "add the note"],
            cwd=repo,
            check=True,
            capture_output=True,
            env=env,
            timeout=30,
        )
        (repo / "note.md").write_text("second\n", encoding="utf-8")
        (repo / "untracked.md").write_text("never committed\n", encoding="utf-8")

        assert GitService.show(repo, "note.md") == "first\n"
        assert GitService.show(repo, "untracked.md") is None
