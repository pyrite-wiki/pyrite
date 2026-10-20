"""Caller-supplied remote and branch values are names, never git options.

``POST /api/kbs/{kb}/push`` and the MCP ``kb_push`` tool take a remote and a
branch from the caller. The remote must be one of the KB repository's
configured remotes (never a URL or a path); the branch must be a valid branch
name that does not begin with ``-``. Both are passed after
``--end-of-options``. A rejected value answers 400 ``INVALID_REF`` on REST and
a ``VALIDATION_ERROR`` result on MCP, and git is not run.

The same rule holds for the other git calls that take a branch
(``worktree_add``, ``merge_branch``, ``diff_branches``), and a structural test
pins every git argument list in ``git_service.py``.

Medium tests: a real KB in a real git repository with a real bare remote.
"""

import ast
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import InvalidGitRefError
from pyrite.server.api import create_app
from pyrite.server.mcp_server import PyriteMCPServer
from pyrite.services.export_service import ExportService
from pyrite.services.git_service import GitService, _git_env
from pyrite.storage.database import PyriteDB

KB = "notes"


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        env=_git_env(),
        timeout=120,
    )
    return r.stdout


def _commit(repo: Path, name: str) -> None:
    (repo / name).write_text(f"---\nid: {name}\ntype: note\ntitle: {name}\n---\nbody\n")
    _git(repo, "add", name)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", name)


@pytest.fixture
def repo(tmp_path):
    """A KB in a git repo with one configured remote, `origin`, a bare repo."""
    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    _git(kb_dir, "init", "-q", "-b", "main")
    _commit(kb_dir, "first.md")
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    _git(kb_dir, "remote", "add", "origin", str(bare))
    # A second bare repo that is NOT a configured remote.
    other = tmp_path / "other.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(other))
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_dir, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    return {"tmp": tmp_path, "kb_dir": kb_dir, "bare": bare, "other": other, "config": config}


def _pwned(repo) -> Path:
    return repo["tmp"] / "pwned"


def _option_remote(repo) -> str:
    return f"--receive-pack=touch {_pwned(repo)}"


def _heads(bare: Path) -> str:
    return _git(bare, "for-each-ref", "--format=%(refname)")


# ---------------------------------------------------------------------------
# The service: ExportService.push_kb
# ---------------------------------------------------------------------------


class TestPushKb:
    def _svc(self, repo):
        db = PyriteDB(repo["config"].settings.index_path)
        return ExportService(repo["config"], db), db

    def test_option_shaped_remote_is_refused_and_runs_nothing(self, repo):
        svc, db = self._svc(repo)
        try:
            with pytest.raises(InvalidGitRefError):
                svc.push_kb(KB, remote=_option_remote(repo), branch=str(repo["bare"]))
        finally:
            db.close()
        assert not _pwned(repo).exists()

    def test_url_or_path_remote_is_refused(self, repo):
        svc, db = self._svc(repo)
        try:
            for remote in (str(repo["other"]), f"file://{repo['other']}"):
                with pytest.raises(InvalidGitRefError):
                    svc.push_kb(KB, remote=remote, branch="main")
        finally:
            db.close()
        assert _heads(repo["other"]) == ""

    def test_unconfigured_remote_name_is_refused(self, repo):
        svc, db = self._svc(repo)
        try:
            with pytest.raises(InvalidGitRefError):
                svc.push_kb(KB, remote="upstream", branch="main")
        finally:
            db.close()

    @pytest.mark.parametrize(
        "branch", ["-f", "--force", "--receive-pack=x", "a b", "a..b", "a\nb", "@{-1}", ""]
    )
    def test_invalid_branch_is_refused(self, repo, branch):
        svc, db = self._svc(repo)
        try:
            with pytest.raises(InvalidGitRefError):
                svc.push_kb(KB, remote="origin", branch=branch)
        finally:
            db.close()
        assert _heads(repo["bare"]) == ""

    def test_configured_remote_and_valid_branch_push(self, repo):
        svc, db = self._svc(repo)
        try:
            result = svc.push_kb(KB, remote="origin", branch="main")
        finally:
            db.close()
        assert result["success"] is True, result
        assert "refs/heads/main" in _heads(repo["bare"])

    def test_default_branch_is_the_current_branch(self, repo):
        svc, db = self._svc(repo)
        try:
            result = svc.push_kb(KB)
        finally:
            db.close()
        assert result["success"] is True, result
        assert "refs/heads/main" in _heads(repo["bare"])


# ---------------------------------------------------------------------------
# REST: POST /api/kbs/{kb}/push
# ---------------------------------------------------------------------------


class TestRestPush:
    def _client(self, repo):
        return TestClient(create_app(config=repo["config"]))

    def test_option_shaped_remote_answers_invalid_ref(self, repo):
        r = self._client(repo).post(
            f"/api/kbs/{KB}/push",
            json={"remote": _option_remote(repo), "branch": str(repo["bare"])},
        )
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["code"] == "INVALID_REF"
        assert not _pwned(repo).exists()

    def test_option_shaped_branch_answers_invalid_ref(self, repo):
        r = self._client(repo).post(
            f"/api/kbs/{KB}/push", json={"remote": "origin", "branch": "--delete"}
        )
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["code"] == "INVALID_REF"

    def test_url_remote_answers_invalid_ref(self, repo):
        r = self._client(repo).post(
            f"/api/kbs/{KB}/push", json={"remote": str(repo["other"]), "branch": "main"}
        )
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["code"] == "INVALID_REF"
        assert _heads(repo["other"]) == ""

    def test_valid_push_succeeds(self, repo):
        r = self._client(repo).post(
            f"/api/kbs/{KB}/push", json={"remote": "origin", "branch": "main"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["success"] is True
        assert "refs/heads/main" in _heads(repo["bare"])


# ---------------------------------------------------------------------------
# MCP: kb_push
# ---------------------------------------------------------------------------


class TestMcpPush:
    @pytest.fixture
    def server(self, repo):
        s = PyriteMCPServer(repo["config"], tier="admin")
        try:
            yield s
        finally:
            s.close()

    def test_option_shaped_remote_is_a_validation_error(self, repo, server):
        result = server._dispatch_tool(
            "kb_push", {"kb": KB, "remote": _option_remote(repo), "branch": str(repo["bare"])}
        )
        assert result.get("error_code") == "VALIDATION_ERROR", result
        assert not _pwned(repo).exists()

    def test_option_shaped_branch_is_a_validation_error(self, repo, server):
        result = server._dispatch_tool("kb_push", {"kb": KB, "remote": "origin", "branch": "-f"})
        assert result.get("error_code") == "VALIDATION_ERROR", result

    @pytest.mark.parametrize(("field", "value"), [("branch", 123), ("remote", ["origin"])])
    def test_non_string_values_are_a_validation_error(self, repo, server, field, value):
        args = {"kb": KB, "remote": "origin", "branch": "main", field: value}
        result = server._dispatch_tool("kb_push", args)
        assert result.get("error_code") == "VALIDATION_ERROR", result
        assert _heads(repo["bare"]) == ""

    def test_valid_push_succeeds(self, repo, server):
        result = server._dispatch_tool("kb_push", {"kb": KB, "remote": "origin", "branch": "main"})
        assert result.get("success") is True, result
        assert "refs/heads/main" in _heads(repo["bare"])


# ---------------------------------------------------------------------------
# The other git calls that take a branch
# ---------------------------------------------------------------------------


class TestOtherBranchArguments:
    @pytest.fixture
    def kb_dir(self, repo):
        _git(repo["kb_dir"], "branch", "user/alice")
        return repo["kb_dir"]

    def test_worktree_add_refuses_option_shaped_branch(self, kb_dir, repo):
        ok, msg = GitService.worktree_add(kb_dir, repo["tmp"] / "wt", "-q")
        assert (ok, msg) == (False, "Invalid branch name")
        assert not (repo["tmp"] / "wt").exists()

    def test_worktree_add_accepts_a_server_branch(self, kb_dir, repo):
        ok, msg = GitService.worktree_add(kb_dir, repo["tmp"] / "wt", "user/bob")
        assert ok, msg
        ok, msg = GitService.worktree_add(kb_dir, repo["tmp"] / "wt2", "user/alice")
        assert ok, msg

    @pytest.mark.parametrize("which", ["branch", "into"])
    def test_merge_branch_refuses_option_shaped_names(self, kb_dir, which):
        args = {"branch": "user/alice", "into": "main", which: "--abort"}
        assert GitService.merge_branch(kb_dir, **args) == (False, "Invalid branch name")

    def test_merge_branch_merges(self, kb_dir):
        ok, msg = GitService.merge_branch(kb_dir, "user/alice", into="main")
        assert ok, msg

    @pytest.mark.parametrize("which", ["base", "head"])
    def test_diff_branches_refuses_option_shaped_names(self, kb_dir, which):
        args = {"base": "main", "head": "user/alice", which: "--output=x"}
        assert GitService.diff_branches(kb_dir, **args) == (False, "Invalid branch name")

    def test_diff_branches_diffs(self, kb_dir):
        _git(kb_dir, "checkout", "-q", "user/alice")
        _commit(kb_dir, "second.md")
        ok, out = GitService.diff_branches(kb_dir, "main", "user/alice", stat_only=True)
        assert ok and "second.md" in out


# ---------------------------------------------------------------------------
# Structural: every git argument list in git_service.py
# ---------------------------------------------------------------------------

# A non-literal value in a git argument list must sit after "--" or
# "--end-of-options", unless it is listed here with the reason it cannot be an
# option. Adding a git call that takes a variable fails this test until the
# call is made safe or listed.
_ALLOWED_BEFORE_SEPARATOR = {
    # clone: --branch's value is an option argument (validated: no leading "-").
    ("clone_with_code", "branch"),
    # commit -m <message>: an option argument, never parsed as an option.
    ("commit", "message"),
    # worktree add -b <branch>: an option argument, validated as a branch name.
    ("worktree_add", "branch"),
    # worktree remove <path>: an absolute path the service computes.
    ("worktree_remove", "str(worktree_path)"),
    # remote add <name> <url>: called with the literal "upstream" and a URL
    # parse_github_url accepted (https://github.com/... or git@github.com:...).
    ("add_remote", "name"),
    ("add_remote", "url"),
    # checkout does not accept --end-of-options on git 2.39; `into` is
    # validated as a branch name first.
    ("merge_branch", "into"),
    # log/diff with <since>..HEAD: server-recorded commit hashes (the
    # versions-commit-hash issue tracks validating them).
    ("get_file_log", "f'{since_commit}..HEAD'"),
    ("get_changed_files", "f'{since_commit}..HEAD'"),
    # check-ref-format refs/heads/<name>: begins with "refs/", never an option.
    ("is_valid_branch_name", "f'refs/heads/{name}'"),
}

_SEPARATORS = {"--", "--end-of-options"}


def _git_argument_lists():
    source = Path(__file__).parent.parent / "pyrite" / "services" / "git_service.py"
    tree = ast.parse(source.read_text())
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef):
            continue
        for node in ast.walk(func):
            if isinstance(node, ast.List) and node.elts:
                yield func.name, node


def test_every_variable_git_argument_follows_a_separator():
    offenders = []
    git_lists = 0
    for func, node in _git_argument_lists():
        first = node.elts[0]
        is_git = isinstance(first, ast.Constant) and first.value == "git"
        is_tail = isinstance(first, ast.Constant) and first.value in _SEPARATORS
        if not (is_git or is_tail):
            continue
        git_lists += is_git
        seen_separator = False
        for elt in node.elts:
            if isinstance(elt, ast.Constant):
                seen_separator = seen_separator or elt.value in _SEPARATORS
                continue
            key = (func, ast.unparse(elt))
            if not seen_separator and key not in _ALLOWED_BEFORE_SEPARATOR:
                offenders.append(key)
    assert git_lists >= 20, "the scan found too few git calls; is it still reading the file?"
    assert offenders == [], f"variable git arguments before '--'/'--end-of-options': {offenders}"


def test_push_and_diff_arguments_are_after_end_of_options():
    """The caller-reachable calls use --end-of-options specifically."""
    lists = {}
    for func, node in _git_argument_lists():
        values = [e.value if isinstance(e, ast.Constant) else ast.unparse(e) for e in node.elts]
        lists.setdefault(func, []).append(values)
    push = next(v for v in lists["push"] if v[:2] == ["git", "push"])
    assert push.index("--end-of-options") < push.index("remote") < push.index("refspec")
    diff_tail = next(v for v in lists["diff_branches"] if v[0] == "--end-of-options")
    assert diff_tail[1] == "f'{base}...{head}'"


# ---------------------------------------------------------------------------
# A branch value names a branch and nothing else: no refspec meaning
# ---------------------------------------------------------------------------


def _rewrite_history(repo) -> str:
    """Push main, then give the KB an unrelated main, so only a forced push lands.

    Returns the commit the remote's main holds.
    """
    kb_dir = repo["kb_dir"]
    _git(kb_dir, "push", "-q", "origin", "main")
    pushed = _git(repo["bare"], "rev-parse", "refs/heads/main").strip()
    _git(kb_dir, "checkout", "-q", "--orphan", "rewrite")
    _commit(kb_dir, "other.md")
    _git(kb_dir, "branch", "-f", "main", "rewrite")
    _git(kb_dir, "checkout", "-q", "main")
    return pushed


def _remote_main(repo) -> str:
    return _git(repo["bare"], "rev-parse", "refs/heads/main").strip()


class TestNoRefspecMeaning:
    def test_rest_push_refuses_a_forcing_branch(self, repo):
        pushed = _rewrite_history(repo)
        r = TestClient(create_app(config=repo["config"])).post(
            f"/api/kbs/{KB}/push", json={"remote": "origin", "branch": "+main"}
        )
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["code"] == "INVALID_REF"
        assert _remote_main(repo) == pushed

    def test_mcp_push_refuses_a_forcing_branch(self, repo):
        pushed = _rewrite_history(repo)
        server = PyriteMCPServer(repo["config"], tier="admin")
        try:
            result = server._dispatch_tool(
                "kb_push", {"kb": KB, "remote": "origin", "branch": "+main"}
            )
        finally:
            server.close()
        assert result.get("error_code") == "VALIDATION_ERROR", result
        assert _remote_main(repo) == pushed

    def test_a_branch_value_never_pushes_a_tag_of_that_name(self, repo):
        """The pushed refspec is built by the server: refs/heads/<b>:refs/heads/<b>."""
        _git(repo["kb_dir"], "tag", "release")
        r = TestClient(create_app(config=repo["config"])).post(
            f"/api/kbs/{KB}/push", json={"remote": "origin", "branch": "release"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["success"] is False
        assert _heads(repo["bare"]) == ""

    def test_export_to_repo_refuses_a_forcing_branch(self, repo):
        pushed = _rewrite_history(repo)
        db = PyriteDB(repo["config"].settings.index_path)
        try:
            svc = ExportService(repo["config"], db)
            with pytest.raises(InvalidGitRefError):
                svc.export_kb_to_repo(KB, str(repo["bare"]), branch="+main")
        finally:
            db.close()
        assert _remote_main(repo) == pushed


def test_rest_export_answers_invalid_ref(repo):
    """POST /api/kbs/{kb}/export maps a rejected branch to 400 INVALID_REF."""
    pushed = _rewrite_history(repo)
    r = TestClient(create_app(config=repo["config"])).post(
        f"/api/kbs/{KB}/export", json={"repo_url": str(repo["bare"]), "branch": "+main"}
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "INVALID_REF"
    assert _remote_main(repo) == pushed


def test_push_without_a_current_branch_says_so(repo):
    """Detached HEAD and no branch given: the answer names the missing branch."""
    kb_dir = repo["kb_dir"]
    _git(kb_dir, "checkout", "-q", "--detach")
    r = TestClient(create_app(config=repo["config"])).post(
        f"/api/kbs/{KB}/push", json={"remote": "origin"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is False
    assert "current branch" in body["message"]
    assert "Invalid branch name" not in body["message"]
    assert _heads(repo["bare"]) == ""
