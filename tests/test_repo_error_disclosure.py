"""Repo endpoints must not leak git stderr or absolute server paths.

CodeQL py/stack-trace-exposure alerts #51 (subscribe), #52 (fork), #53 (pr).
The reproduced leak was a 400 body reading::

    Clone failed: Cloning into '/Users/<user>/.pyrite/repos/owner/repo'...
    remote: Repository not found.
    fatal: repository 'https://github.com/owner/repo/' not found

— raw git stderr plus the server's absolute filesystem layout, handed to a
write-tier caller. The token was already stripped; the paths were not.

Every git call here is stubbed with *recorded* stderr shapes (captured from
real git 2.x on 2026-09-18); nothing in this file clones a real repository or
writes outside ``tmp_path``.
"""

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrite.config import AuthConfig, PyriteConfig, Settings
from pyrite.services.git_service import GitService
from pyrite.services.repo_service import RepoService
from pyrite.storage.database import PyriteDB

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")

from pyrite.server.api import get_repo_service  # noqa: E402

# --- Recorded git stderr shapes -------------------------------------------
# Captured verbatim from git; the leading "Cloning into '<abs path>'" line is
# what git always emits first, and is the path disclosure itself.

STDERR_REPO_NOT_FOUND = (
    "Cloning into '{dest}'...\n"
    "remote: Repository not found.\n"
    "fatal: repository 'https://github.com/owner/repo/' not found\n"
)

STDERR_AUTH_FAILED = (
    "Cloning into '{dest}'...\n"
    "remote: Invalid username or token. Password authentication is not supported.\n"
    "fatal: Authentication failed for 'https://github.com/owner/repo/'\n"
)

STDERR_AUTH_NO_USERNAME = (
    "Cloning into '{dest}'...\n"
    "fatal: could not read Username for 'https://github.com': terminal prompts disabled\n"
)

STDERR_BRANCH_NOT_FOUND = (
    "Cloning into '{dest}'...\n"
    "warning: Could not find remote branch no-such-branch to clone.\n"
    "fatal: Remote branch no-such-branch not found in upstream origin\n"
)

# An unclassified clone failure: matches none of the patterns, so the response
# depends entirely on redaction. The destination is quoted and contains a
# space, which the first pass redacted only partly ("'<path> Docs<path>'").
STDERR_UNCLASSIFIED_SPACED_PATH = (
    "Cloning into '{dest}'...\nfatal: could not create work tree dir '{dest}': Permission denied\n"
)

# Real, non-clone git failures. Before this branch both returned git's own
# words to the CLI operator and the web UI; classification must not eat them.
STDERR_PULL_MERGE_CONFLICT = (
    "error: Your local changes to the following files would be overwritten by merge:\n"
    "\tnotes/inbox.md\n"
    "Please commit your changes or stash them before you merge.\n"
    "Aborting\n"
)

STDERR_PUSH_REJECTED = (
    "To https://github.com/owner/repo.git\n"
    " ! [rejected]        main -> main (fetch first)\n"
    "error: failed to push some refs to 'https://github.com/owner/repo.git'\n"
    "hint: Updates were rejected because the tip of your current branch is behind\n"
    "hint: its remote counterpart. Integrate the remote changes (e.g.\n"
    "hint: 'git pull ...') before pushing again.\n"
    "hint: See the 'Note about fast-forwards' in 'git push --help' for details.\n"
)

# git's wording when the *local* remote is missing — nothing to do with a
# remote repository being absent or the credentials being wrong.
STDERR_NO_REMOTE = (
    "fatal: 'origin' does not appear to be a git repository\n"
    "fatal: Could not read from remote repository.\n"
    "\n"
    "Please make sure you have the correct access rights\n"
    "and the repository exists.\n"
)


def _fake_clone_run(stderr_template: str):
    """A subprocess.run stub that fails a `git clone` with `stderr_template`,
    filling in whatever destination path the real call passed."""

    def _run(cmd, *args, **kwargs):
        dest = cmd[-1]
        result = MagicMock(spec=subprocess.CompletedProcess)
        result.returncode = 128
        result.stdout = ""
        result.stderr = stderr_template.format(dest=dest)
        return result

    return _run


def _fake_failing_run(stderr: str):
    """A subprocess.run stub that fails whatever git command it is given."""

    def _run(cmd, *args, **kwargs):
        result = MagicMock(spec=subprocess.CompletedProcess)
        result.returncode = 1
        result.stdout = ""
        result.stderr = stderr
        return result

    return _run


@pytest.fixture
def git_url_lookups_answered_directly(monkeypatch):
    """For the classes below whose subprocess stubs fail every git command:
    the host rule's own local lookups (insteadOf expansion, a remote's
    configured URLs) are answered from the URL itself, so the stubbed failure
    is the clone/pull/push's. The rule still checks every URL string; its
    lookups are tested in tests/test_git_destinations_are_allowed.py."""
    monkeypatch.setattr(GitService, "_effective_url", staticmethod(lambda url, cwd=None: url))
    monkeypatch.setattr(GitService, "_check_remote_urls", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(GitService, "_pull_remote", staticmethod(lambda path: "origin"))


@pytest.fixture
def workspace(tmp_path):
    """A workspace root under tmp_path — never ``~/.pyrite``."""
    path = tmp_path / "workspace"
    path.mkdir()
    return path


@pytest.fixture
def repo_service(tmp_path, workspace):
    db = PyriteDB(tmp_path / "index.db")
    config = PyriteConfig(
        settings=Settings(index_path=tmp_path / "index.db", workspace_path=workspace)
    )
    yield RepoService(config, db)
    db.close()


class TestSanitiser:
    """Criterion 1: a path-redacting step additive to _sanitize_output."""

    def test_absolute_path_is_replaced_with_placeholder(self, tmp_path):
        dest = tmp_path / "owner" / "repo"
        raw = STDERR_REPO_NOT_FOUND.format(dest=dest)

        safe = GitService.sanitize_error(raw, token=None)

        assert str(tmp_path) not in safe
        assert str(dest) not in safe
        assert "Cloning into" not in safe

    def test_token_redaction_is_preserved(self, tmp_path):
        raw = f"Cloning into '{tmp_path}/o/r'...\nfatal: bad token ghp_secret123\n"

        safe = GitService.sanitize_error(raw, token="ghp_secret123")

        assert "ghp_secret123" not in safe
        assert str(tmp_path) not in safe

    def test_windows_style_absolute_path_is_replaced(self):
        # NOT a "Cloning into" line: the narration regex deletes those whole,
        # so the Windows arm of the path regex was never exercised.
        raw = "fatal: could not create work tree dir 'C:\\Users\\alice\\.pyrite\\repos\\o\\r'\n"

        safe = GitService.sanitize_error(raw, token=None)

        assert "C:\\Users\\alice" not in safe
        assert "alice" not in safe
        assert "could not create work tree dir" in safe

    def test_sanitize_output_still_exists_and_redacts_tokens(self):
        assert GitService._sanitize_output("err ghp_x", "ghp_x") == "err ***"


class TestSanitiserPreservesURLs:
    """Finding 3: `_ABS_PATH_RE` must not eat scheme-qualified URLs or
    scp-style remotes — that mangled text is what fork (#52) and pr (#53)
    callers actually receive, since only clone is classified."""

    @pytest.mark.parametrize(
        ("raw", "must_survive"),
        [
            (
                "fatal: repository 'https://github.com/owner/repo/' not found\n",
                "https://github.com/owner/repo/",
            ),
            (
                "fatal: Could not read from remote repository ssh://git@github.com/owner/repo.git\n",
                "ssh://git@github.com/owner/repo.git",
            ),
            (
                "fatal: 'git@github.com:owner/repo.git' does not appear to be a git repository\n",
                "git@github.com:owner/repo.git",
            ),
            (
                "hint: See https://docs.github.com/articles/about-remote-repositories\n",
                "https://docs.github.com/articles/about-remote-repositories",
            ),
        ],
    )
    def test_urls_survive_redaction(self, raw, must_survive):
        safe = GitService.sanitize_error(raw, token=None)

        assert must_survive in safe, f"URL mangled by path redaction: {safe!r}"


class TestSanitiserPathsWithSpaces:
    """Finding 4: a path containing a space must be redacted whole — the first
    pass left `'<path> Docs<path>'`, leaking a directory name."""

    def test_quoted_path_with_space_is_fully_redacted(self):
        raw = (
            "fatal: could not create work tree dir "
            "'/Users/alice/My Docs/repos/o/r': Permission denied\n"
        )

        safe = GitService.sanitize_error(raw, token=None)

        assert "My Docs" not in safe
        assert "Docs" not in safe
        assert "alice" not in safe
        assert "Permission denied" in safe

    def test_unquoted_path_with_space_is_fully_redacted(self):
        raw = "fatal: cannot access /Users/alice/My Docs/repos/o/r: Permission denied\n"

        safe = GitService.sanitize_error(raw, token=None)

        assert "Docs" not in safe
        assert "alice" not in safe
        assert "Permission denied" in safe

    def test_windows_path_with_space_is_fully_redacted(self):
        raw = "fatal: cannot open 'C:\\Users\\alice\\My Docs\\repos\\o\\r\\.git'\n"

        safe = GitService.sanitize_error(raw, token=None)

        assert "Docs" not in safe
        assert "repos" not in safe
        assert "alice" not in safe

    def test_tilde_path_with_space_is_fully_redacted(self):
        raw = "fatal: cannot open '~/My Docs/repos/o/r/.git': No such file\n"

        safe = GitService.sanitize_error(raw, token=None)

        assert "Docs" not in safe
        assert "No such file" in safe


class TestErrorClassification:
    """Criterion 3: three actionable failures map to three stable codes."""

    @pytest.mark.parametrize(
        ("template", "expected_code"),
        [
            (STDERR_REPO_NOT_FOUND, "REPO_NOT_FOUND"),
            (STDERR_AUTH_FAILED, "AUTH_REQUIRED"),
            (STDERR_AUTH_NO_USERNAME, "AUTH_REQUIRED"),
            (STDERR_BRANCH_NOT_FOUND, "BRANCH_NOT_FOUND"),
        ],
    )
    def test_classify(self, tmp_path, template, expected_code):
        raw = template.format(dest=tmp_path / "owner" / "repo")

        code, message = GitService.classify_git_error(raw, token=None)

        assert code == expected_code
        assert str(tmp_path) not in message
        assert "Cloning into" not in message

    def test_codes_are_distinct(self, tmp_path):
        codes = {
            GitService.classify_git_error(t.format(dest=tmp_path / "o" / "r"), None)[0]
            for t in (STDERR_REPO_NOT_FOUND, STDERR_AUTH_FAILED, STDERR_BRANCH_NOT_FOUND)
        }
        assert len(codes) == 3

    def test_unknown_stderr_falls_back_without_leaking(self, tmp_path):
        raw = f"Cloning into '{tmp_path}/o/r'...\nfatal: something entirely new\n"

        code, message = GitService.classify_git_error(raw, token=None)

        assert code == "CLONE_FAILED"
        assert str(tmp_path) not in message

    def test_branch_pattern_is_matched_before_repo_pattern(self, tmp_path):
        """Pattern order is load-bearing: git says "Remote branch X not found
        in upstream origin", which a looser "not found" reading would swallow
        into REPO_NOT_FOUND. Pins the order against a reorder."""
        raw = STDERR_BRANCH_NOT_FOUND.format(dest=tmp_path / "o" / "r")

        code, _message = GitService.classify_git_error(raw, token=None)

        assert code == "BRANCH_NOT_FOUND"
        assert code != "REPO_NOT_FOUND"

    def test_missing_local_remote_is_not_repo_not_found(self):
        """Finding 2: `does not appear to be a git repository` is what git says
        when the *local* remote is missing (a push from a KB with no remote).
        Telling that user "Repository not found, or the configured credentials
        cannot see it" is false on both halves."""
        code, message = GitService.classify_git_error(STDERR_NO_REMOTE, token=None)

        assert code != "REPO_NOT_FOUND"
        assert "credentials cannot see it" not in message


@pytest.mark.usefixtures("git_url_lookups_answered_directly")
class TestCloneLogsFullStderr:
    """Criterion 4: the operator still gets everything the body omits."""

    def test_clone_logs_raw_stderr_at_warning(self, tmp_path, caplog):
        dest = tmp_path / "owner" / "repo"
        raw = STDERR_REPO_NOT_FOUND.format(dest=dest)

        with (
            patch("subprocess.run", _fake_clone_run(STDERR_REPO_NOT_FOUND)),
            caplog.at_level(logging.WARNING, logger="pyrite.services.git_service"),
        ):
            success, message = GitService.clone(
                "https://github.com/owner/repo", dest, branch="main"
            )

        assert success is False
        assert str(dest) not in message
        assert "Cloning into" not in message

        carrying = [r for r in caplog.records if "Repository not found" in r.getMessage()]
        assert carrying, "the raw stderr must reach the log"
        assert all(r.levelno == logging.WARNING for r in carrying), (
            "the record carrying the stderr must be at WARNING, not DEBUG: "
            f"{[(r.levelname, r.getMessage()[:60]) for r in carrying]}"
        )

        logged = "\n".join(r.getMessage() for r in carrying)
        assert str(dest) in logged, "the operator must still see the real path"
        assert raw.strip().splitlines()[-1] in logged


@pytest.mark.usefixtures("git_url_lookups_answered_directly")
class TestPullAndPushReturnGitsOwnWords:
    """Finding 1: `pull`/`push` must return git's own text (token- and
    path-redacted), not a canned "Git operation failed — see the server log"
    that the CLI operator, who *is* the operator, cannot act on. The web UI
    renders `push_error` verbatim (web/src/routes/changes/+page.svelte:50)."""

    def _repo(self, tmp_path):
        path = tmp_path / "repo"
        path.mkdir()
        (path / ".git").mkdir()
        return path

    def test_pull_surfaces_merge_conflict_stderr(self, tmp_path):
        with patch("subprocess.run", _fake_failing_run(STDERR_PULL_MERGE_CONFLICT)):
            success, message = GitService.pull(self._repo(tmp_path))

        assert success is False
        assert "would be overwritten by merge" in message, (
            f"the real git error must surface, not a canned string: {message!r}"
        )
        assert "see the server log" not in message
        assert str(tmp_path) not in message

    def test_push_surfaces_rejected_stderr(self, tmp_path):
        repo = self._repo(tmp_path)
        with (
            patch.object(GitService, "is_git_repo", return_value=True),
            patch.object(GitService, "_checked_out_branch", return_value="main"),
            # push checks its remote against `git remote` and its branch
            # with `git check-ref-format` first; this test is about what
            # it reports when the push itself fails.
            patch.object(GitService, "list_remotes", return_value={"origin"}),
            patch.object(GitService, "is_valid_branch_name", return_value=True),
            patch("subprocess.run", _fake_failing_run(STDERR_PUSH_REJECTED)),
        ):
            success, message = GitService.push(repo)

        assert success is False
        assert "tip of your current branch is behind" in message, (
            f"the real git error must surface, not a canned string: {message!r}"
        )
        assert "see the server log" not in message
        assert "https://github.com/owner/repo.git" in message, (
            "the remote URL must survive path redaction"
        )
        assert str(tmp_path) not in message

    def test_push_with_no_remote_says_so(self, tmp_path):
        """Finding 2, end to end: a push from a KB with no remote must not be
        reported as "Repository not found, or the configured credentials
        cannot see it"."""
        repo = self._repo(tmp_path)
        with (
            patch.object(GitService, "is_git_repo", return_value=True),
            patch.object(GitService, "_checked_out_branch", return_value="main"),
            # push checks its remote against `git remote` and its branch
            # with `git check-ref-format` first; this test is about what
            # it reports when the push itself fails.
            patch.object(GitService, "list_remotes", return_value={"origin"}),
            patch.object(GitService, "is_valid_branch_name", return_value=True),
            patch("subprocess.run", _fake_failing_run(STDERR_NO_REMOTE)),
        ):
            success, message = GitService.push(repo)

        assert success is False
        assert "does not appear to be a git repository" in message
        assert "credentials cannot see it" not in message

    def test_pull_still_redacts_tokens_and_paths(self, tmp_path):
        stderr = (
            f"fatal: unable to access 'https://oauth2:ghp_secret123@github.com/o/r/': "
            f"could not lock config file {tmp_path}/repo/.git/config\n"
        )
        with patch("subprocess.run", _fake_failing_run(stderr)):
            success, message = GitService.pull(self._repo(tmp_path), token="ghp_secret123")

        assert success is False
        assert "ghp_secret123" not in message
        assert str(tmp_path) not in message
        assert "could not lock config file" in message


@pytest.mark.usefixtures("git_url_lookups_answered_directly")
class TestSubscribeEndpointDisclosure:
    """Criterion 2 + 3, end to end through the REST endpoint."""

    @pytest.fixture
    def client_factory(self, make_client, workspace, tmp_path):
        def _make(service):
            client, _, _ = make_client(
                auth=AuthConfig(enabled=True, allow_registration=True),
                dependency_overrides={get_repo_service: lambda: service},
                register_user=("testuser", "password123"),
            )
            return client

        return _make

    def test_subscribe_nonexistent_repo_leaks_no_paths(
        self, client_factory, repo_service, workspace, tmp_path
    ):
        client = client_factory(repo_service)

        with patch("subprocess.run", _fake_clone_run(STDERR_REPO_NOT_FOUND)):
            r = client.post(
                "/api/repos/subscribe",
                json={"remote_url": "https://github.com/owner/repo"},
            )

        assert r.status_code == 400
        detail = r.json()["detail"]
        body = r.text

        # The exact regression string from the CodeQL report.
        assert "Cloning into" not in body
        assert str(workspace) not in body
        assert str(tmp_path) not in body
        assert str(Path.home()) not in body
        assert detail["code"] == "REPO_NOT_FOUND"
        # ...but the caller can still act on it.
        assert "not found" in detail["message"].lower()

    @pytest.mark.parametrize(
        ("template", "expected_code"),
        [
            (STDERR_REPO_NOT_FOUND, "REPO_NOT_FOUND"),
            (STDERR_AUTH_FAILED, "AUTH_REQUIRED"),
            (STDERR_BRANCH_NOT_FOUND, "BRANCH_NOT_FOUND"),
        ],
    )
    def test_subscribe_reports_distinct_codes(
        self, client_factory, repo_service, tmp_path, template, expected_code
    ):
        client = client_factory(repo_service)

        with patch("subprocess.run", _fake_clone_run(template)):
            r = client.post(
                "/api/repos/subscribe",
                json={"remote_url": "https://github.com/owner/repo"},
            )

        assert r.status_code == 400
        assert r.json()["detail"]["code"] == expected_code
        assert str(tmp_path) not in r.text

    def test_subscribe_unclassified_failure_stays_path_free(
        self, client_factory, repo_service, workspace, tmp_path
    ):
        """A clone failure matching *no* pattern — the shape most likely to
        carry a path git wrote about the destination.

        Note for the reader: unlike the fork/pr cases below, this one cannot
        be made to depend on `redact_paths`. A clone that matches no pattern
        falls to `CLONE_FAILED`, whose message is a fixed literal by design:
        an unrecognised stderr may embed a hostname or a URL, so none of it is
        forwarded. `TestUnclassifiedErrorsDependOnRedaction` covers the paths
        where redaction is the only thing standing between git's text and the
        caller."""
        client = client_factory(repo_service)

        with patch("subprocess.run", _fake_clone_run(STDERR_UNCLASSIFIED_SPACED_PATH)):
            r = client.post(
                "/api/repos/subscribe",
                json={"remote_url": "https://github.com/owner/repo"},
            )

        assert r.status_code == 400
        body = r.text
        assert "Cloning into" not in body
        assert str(workspace) not in body
        assert str(tmp_path) not in body
        assert str(Path.home()) not in body


class TestUnclassifiedErrorsDependOnRedaction:
    """The headline subscribe test passes with `redact_paths` stubbed to the
    identity, because clone classification short-circuits to a fixed literal.
    These cases return the service's own text, so a clean body proves
    redaction ran — stub `redact_paths` and they go red."""

    @pytest.fixture
    def client_factory(self, make_client):
        def _make(service):
            client, _, _ = make_client(
                auth=AuthConfig(enabled=True, allow_registration=True),
                dependency_overrides={get_repo_service: lambda: service},
                register_user=("testuser", "password123"),
            )
            return client

        return _make

    def test_fork_error_with_spaced_path_is_fully_redacted(self, client_factory, tmp_path):
        leaky = (
            "fatal: could not create work tree dir "
            f"'{tmp_path}/My Docs/owner/repo': Permission denied\n"
        )
        svc = MagicMock(spec=RepoService)
        svc._github_token = "ghp_test"
        svc.fork_and_subscribe.return_value = {"success": False, "error": leaky}
        client = client_factory(svc)

        r = client.post("/api/repos/fork", json={"remote_url": "https://github.com/owner/repo"})

        assert r.status_code == 400
        assert str(tmp_path) not in r.text
        assert "My Docs" not in r.text
        assert "Docs" not in r.text
        # ...and the actionable part survives.
        assert "Permission denied" in r.json()["detail"]["message"]

    def test_pr_error_keeps_the_remote_url_but_drops_the_path(self, client_factory, tmp_path):
        leaky = (
            "fatal: unable to access 'https://github.com/owner/repo/': "
            f"could not lock config file {tmp_path}/owner/repo/.git/config\n"
        )
        svc = MagicMock(spec=RepoService)
        svc._github_token = "ghp_test"
        svc.create_pr.return_value = {"success": False, "error": leaky}
        client = client_factory(svc)

        r = client.post("/api/repos/owner/repo/pr", json={"title": "T", "body": "B"})

        assert r.status_code == 400
        message = r.json()["detail"]["message"]
        assert str(tmp_path) not in message
        assert "https://github.com/owner/repo/" in message, (
            "the caller's own remote URL is the actionable part and must survive"
        )


class TestSyncEndpointDisclosure:
    """Finding 5: `RepoService.sync` returns `{"success": True, "repos":
    {name: {"success": False, "error": ...}}}`, so a nested per-repo error is
    serialised verbatim at 200, never reaching `_error_detail`."""

    @pytest.fixture
    def client_factory(self, make_client):
        def _make(service):
            client, _, _ = make_client(
                auth=AuthConfig(enabled=True, allow_registration=True),
                dependency_overrides={get_repo_service: lambda: service},
                register_user=("testuser", "password123"),
            )
            return client

        return _make

    def test_nested_per_repo_error_is_sanitised(self, client_factory, tmp_path):
        leaky = f"Pull failed: fatal: cannot open '{tmp_path}/owner/repo/.git/config'\n"
        svc = MagicMock(spec=RepoService)
        svc._github_token = "ghp_test"
        svc.sync.return_value = {
            "success": True,
            "repos": {"owner/repo": {"success": False, "error": leaky}},
        }
        client = client_factory(svc)

        r = client.post("/api/repos/owner/repo/sync")

        assert r.status_code == 200
        assert str(tmp_path) not in r.text, f"nested error leaked the server path: {r.text!r}"
        nested = r.json()["repos"]["owner/repo"]
        assert nested["success"] is False
        assert "cannot open" in nested["error"]


class TestForkAndPRDisclosure:
    """Criterion 5: fork (#52) and pr (#53) route errors through the same
    sanitiser."""

    @pytest.fixture
    def client_factory(self, make_client):
        def _make(service):
            client, _, _ = make_client(
                auth=AuthConfig(enabled=True, allow_registration=True),
                dependency_overrides={get_repo_service: lambda: service},
                register_user=("testuser", "password123"),
            )
            return client

        return _make

    def test_fork_error_is_sanitised(self, client_factory, tmp_path):
        leaky = f"Cloning into '{tmp_path}/owner/repo'...\nfatal: repository not found\n"
        svc = MagicMock(spec=RepoService)
        svc._github_token = "ghp_test"
        svc.fork_and_subscribe.return_value = {"success": False, "error": leaky}
        client = client_factory(svc)

        r = client.post("/api/repos/fork", json={"remote_url": "https://github.com/owner/repo"})

        assert r.status_code == 400
        assert str(tmp_path) not in r.text
        assert "Cloning into" not in r.text

    def test_pr_error_is_sanitised(self, client_factory, tmp_path):
        leaky = f"fatal: cannot open '{tmp_path}/owner/repo/.git/config'\n"
        svc = MagicMock(spec=RepoService)
        svc._github_token = "ghp_test"
        svc.create_pr.return_value = {"success": False, "error": leaky}
        client = client_factory(svc)

        r = client.post("/api/repos/owner/repo/pr", json={"title": "T", "body": "B"})

        assert r.status_code == 400
        assert str(tmp_path) not in r.text

    def test_fork_error_redacts_the_services_token(self, client_factory, tmp_path):
        """`_error_detail` called `sanitize_error(str(raw))` with no token,
        while every other call site passes one."""
        leaky = "fatal: unable to access 'https://oauth2:ghp_secret123@github.com/o/r/'\n"
        svc = MagicMock(spec=RepoService)
        svc._github_token = "ghp_secret123"
        svc.fork_and_subscribe.return_value = {"success": False, "error": leaky}
        client = client_factory(svc)

        r = client.post("/api/repos/fork", json={"remote_url": "https://github.com/owner/repo"})

        assert r.status_code == 400
        assert "ghp_secret123" not in r.text


_ABS_PATH_MARKERS = ("/", "~")


def _looks_absolute(value: str) -> bool:
    """True if `value` has the shape of an absolute filesystem path: a
    leading `/`, a leading `~`, or a Windows drive letter (`C:\\...`)."""
    if not isinstance(value, str) or not value:
        return False
    if value[0] in _ABS_PATH_MARKERS:
        return True
    return len(value) >= 3 and value[1] == ":" and value[2] in ("\\", "/")


def _assert_no_absolute_paths(obj, path=""):
    """Recursively walk a JSON-able structure and assert no string value has
    the shape of an absolute path. Asserts the property, not one hardcoded
    temp path, so it holds on any machine."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            _assert_no_absolute_paths(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _assert_no_absolute_paths(value, f"{path}[{i}]")
    elif isinstance(obj, str):
        assert not _looks_absolute(obj), f"absolute-path-shaped value at {path!r}: {obj!r}"


class TestSuccessBodyDisclosure:
    """Issue #195 -- the success-path twin of #161: `RepoInfo.local_path` and
    the `path` key in `subscribe`/`fork` success bodies carried the server's
    absolute workspace path. The maintainer's decision (2026-09-20): relativize
    to the workspace root (`owner/repo_name`), not drop or opaque-handle --
    `local_path` is public REST response shape an external consumer may read.

    Internal callers (repo_service.py:189, :221, :302, :347; config.py:682)
    consume these fields as absolute paths from the DB row or the service's
    own dict, which are untouched here -- only what crosses the HTTP boundary
    is relativized. See `_repo_dict_to_info` and the endpoint handlers in
    `pyrite/server/endpoints/repos.py`.
    """

    @pytest.fixture
    def client_factory(self, make_client):
        def _make(service):
            client, _, _ = make_client(
                auth=AuthConfig(enabled=True, allow_registration=True),
                dependency_overrides={get_repo_service: lambda: service},
                register_user=("testuser", "password123"),
            )
            return client

        return _make

    def test_list_repos_local_path_is_relative(self, client_factory, workspace, tmp_path):
        abs_path = workspace / "owner" / "repo"
        svc = MagicMock(spec=RepoService)
        svc._github_token = None
        svc.config = MagicMock()
        svc.config.settings.workspace_path = workspace
        svc.list_repos.return_value = [
            {
                "id": 1,
                "name": "owner/repo",
                "local_path": str(abs_path),
                "owner": "owner",
            }
        ]
        client = client_factory(svc)

        r = client.get("/api/repos")

        assert r.status_code == 200
        body = r.json()
        _assert_no_absolute_paths(body)
        assert body["repos"][0]["local_path"] == "owner/repo"

    def test_get_repo_local_path_is_relative(self, client_factory, workspace):
        abs_path = workspace / "owner" / "repo"
        svc = MagicMock(spec=RepoService)
        svc._github_token = None
        svc.config = MagicMock()
        svc.config.settings.workspace_path = workspace
        svc.get_repo_status.return_value = {
            "id": 1,
            "name": "owner/repo",
            "local_path": str(abs_path),
            "current_branch": "main",
        }
        client = client_factory(svc)

        r = client.get("/api/repos/owner/repo")

        assert r.status_code == 200
        body = r.json()
        _assert_no_absolute_paths(body)
        assert body["local_path"] == "owner/repo"

    def test_subscribe_success_path_is_relative(self, client_factory, workspace):
        abs_path = workspace / "owner" / "repo"
        svc = MagicMock(spec=RepoService)
        svc._github_token = None
        svc.config = MagicMock()
        svc.config.settings.workspace_path = workspace
        svc.subscribe.return_value = {
            "success": True,
            "repo": "owner/repo",
            "path": str(abs_path),
            "kbs": ["some-kb"],
            "entries_indexed": 3,
        }
        client = client_factory(svc)

        r = client.post(
            "/api/repos/subscribe",
            json={"remote_url": "https://github.com/owner/repo"},
        )

        assert r.status_code == 200
        body = r.json()
        _assert_no_absolute_paths(body)
        assert body["path"] == "owner/repo"

    def test_fork_success_path_is_relative(self, client_factory, workspace):
        abs_path = workspace / "myfork" / "repo"
        svc = MagicMock(spec=RepoService)
        svc._github_token = "ghp_test"
        svc.config = MagicMock()
        svc.config.settings.workspace_path = workspace
        svc.fork_and_subscribe.return_value = {
            "success": True,
            "repo": "myfork/repo",
            "path": str(abs_path),
            "kbs": [],
            "is_fork": True,
            "upstream": "owner/repo",
        }
        client = client_factory(svc)

        r = client.post("/api/repos/fork", json={"remote_url": "https://github.com/owner/repo"})

        assert r.status_code == 200
        body = r.json()
        _assert_no_absolute_paths(body)
        assert body["path"] == "myfork/repo"

    def test_relativize_falls_back_gracefully_outside_workspace(self, client_factory, tmp_path):
        """A path the workspace root cannot be relative to (e.g. legacy data
        from a moved workspace) must not raise -- and must still not leak the
        absolute value in a way an external caller could use to learn server
        layout. It's replaced by an opaque marker rather than crashing."""
        outside = tmp_path / "elsewhere" / "owner" / "repo"
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        svc = MagicMock(spec=RepoService)
        svc._github_token = None
        svc.config = MagicMock()
        svc.config.settings.workspace_path = workspace
        svc.list_repos.return_value = [{"id": 1, "name": "owner/repo", "local_path": str(outside)}]
        client = client_factory(svc)

        r = client.get("/api/repos")

        assert r.status_code == 200
        body = r.json()
        _assert_no_absolute_paths(body)


def test_no_second_clone_implementation_returning_raw_stderr():
    """`github_auth.clone_private_repo` was a second, dead clone path that
    returned `f"Clone failed: {result.stderr}"` — raw, path-bearing, bypassing
    every control in GitService. Nothing called it; it is gone, and must not
    come back as a way around the sanitiser."""
    import pyrite.github_auth as github_auth

    assert not hasattr(github_auth, "clone_private_repo")


def test_no_second_pull_implementation_returning_raw_stderr():
    """`github_auth.pull_repo` was the same shape of hole as
    `clone_private_repo`: a second, dead `git pull` that returned
    `f"Pull failed: {result.stderr}"` with only the token replaced, so the
    message carried absolute paths and bypassed `GitService.sanitize_error`.

    `git grep pull_repo` found only the definition, so there was no caller to
    migrate and it is gone rather than routed through the sanitiser. It must
    not come back as a way around the redaction (#185)."""
    import pyrite.github_auth as github_auth

    assert not hasattr(github_auth, "pull_repo")


@pytest.mark.usefixtures("git_url_lookups_answered_directly")
class TestErrorDetailHygiene:
    """Should-fix items: the public code set is closed, and messages are not
    double-prefixed."""

    @pytest.fixture
    def client_factory(self, make_client):
        def _make(service):
            client, _, _ = make_client(
                auth=AuthConfig(enabled=True, allow_registration=True),
                dependency_overrides={get_repo_service: lambda: service},
                register_user=("testuser", "password123"),
            )
            return client

        return _make

    def test_unknown_error_code_is_not_echoed_to_the_caller(self, client_factory):
        svc = MagicMock(spec=RepoService)
        svc._github_token = "ghp_test"
        svc.fork_and_subscribe.return_value = {
            "success": False,
            "error": "nope",
            "error_code": "SOMETHING_INTERNAL_1234",
        }
        client = client_factory(svc)

        r = client.post("/api/repos/fork", json={"remote_url": "https://github.com/owner/repo"})

        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "FORK_FAILED"
        assert "SOMETHING_INTERNAL_1234" not in r.text

    def test_known_error_code_is_passed_through(self, client_factory):
        svc = MagicMock(spec=RepoService)
        svc._github_token = "ghp_test"
        svc.fork_and_subscribe.return_value = {
            "success": False,
            "error": "nope",
            "error_code": "AUTH_REQUIRED",
        }
        client = client_factory(svc)

        r = client.post("/api/repos/fork", json={"remote_url": "https://github.com/owner/repo"})

        assert r.json()["detail"]["code"] == "AUTH_REQUIRED"

    def test_clone_message_is_not_double_prefixed(self, tmp_path):
        with patch("subprocess.run", _fake_clone_run(STDERR_REPO_NOT_FOUND)):
            success, message = GitService.clone(
                "https://github.com/owner/repo", tmp_path / "o" / "r"
            )

        assert success is False
        assert not message.startswith("Clone failed: Repository not found"), (
            f"double prefix: {message!r}"
        )

    def test_push_message_is_not_double_prefixed(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with (
            patch.object(GitService, "is_git_repo", return_value=True),
            patch.object(GitService, "_checked_out_branch", return_value="main"),
            # push checks its remote against `git remote` and its branch
            # with `git check-ref-format` first; this test is about what
            # it reports when the push itself fails.
            patch.object(GitService, "list_remotes", return_value={"origin"}),
            patch.object(GitService, "is_valid_branch_name", return_value=True),
            patch("subprocess.run", _fake_failing_run(STDERR_PUSH_REJECTED)),
        ):
            success, message = GitService.push(repo)

        assert success is False
        assert message.count("Push failed:") <= 1
