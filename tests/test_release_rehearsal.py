"""The release dry run rehearses the publish step on this machine.

Four things failed only on the day 0.25.7 was cut: the changelog section was
dated the day it was prepared; three untracked files made the checkout dirty;
the pre-push hook re-ran the local suite on a commit CI had passed, in a venv
missing a dev dependency, and blocked the push to `main`; and the earlier
rehearsal had met none of them, because a dry run stopped at its first failed
check and was rehearsed on a branch that was not `dev`.

So the dry run now asks every check it can and names each one that would stop
`--execute`. These tests give it a real checkout (git, a local bare `origin`,
this repository's own `scripts/test-affected` as the hook's script) with each
failing precondition in turn, then all of them at once.

Two seams, and no others: `gh` is a fake executable first on PATH, so nothing
reaches GitHub; and `git remote get-url [--push] origin` is answered with a GitHub URL,
because the real origin here is a directory. Everything else -- every other
git command, the hook's preflight subprocess -- is real.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from tests.verified_commit_cases import (
    CASES,
    FAKE_GH,
    FAKE_VENV_PYTHON,
    NOT_GITHUB_REMOTES,
    green,
)

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import release  # noqa: E402

VERSION = "0.1.0"
GITHUB_URL = "git@github.com:acme/widget.git"
TODAY = date.today().isoformat()

# What pre-commit's pre-push shim does, for one pushed ref: hand the hook the
# range and the remote, and run it. For a ref the remote has every commit of
# (the tag push, after main moved) it runs nothing.
PRE_PUSH = """\
#!/bin/sh
read local_ref local_sha remote_ref remote_sha || exit 0
case "$remote_sha" in 0000000000000000000000000000000000000000) exit 0;; esac
export PRE_COMMIT_FROM_REF="$remote_sha" PRE_COMMIT_TO_REF="$local_sha"
export PRE_COMMIT_REMOTE_NAME="$1" PRE_COMMIT_REMOTE_URL="$HOOK_REMOTE_URL"
exec .venv/bin/python scripts/test-affected --run -n 2
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))


def changelog(day: str = TODAY) -> str:
    return f"# Changelog\n\n## [{VERSION}] - {day}\n\n- The first release.\n"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "PYRITE_CONFIG_DIR", "PYRITE_DATA_DIR"):
        monkeypatch.setenv(var, str(home))
    for var in (
        "PRE_COMMIT_FROM_REF", "PRE_COMMIT_TO_REF", "PRE_COMMIT_REMOTE_URL",
        "PRE_COMMIT_REMOTE_NAME", "PYRITE_PUSH_FULL", "PYRITE_PUSH_FORCE",
        "PYRITE_PUSH_EXPERIMENTAL", "PYRITE_PUSH_WORKERS", "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS", "FAKE_EXIT", "FAKE_COLLECT_EXIT", "FAKE_PY_VERSION",
        "GH_TOKEN", "GITHUB_TOKEN", "GH_HOST",
    ):  # fmt: skip
        monkeypatch.delenv(var, raising=False)
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "Test")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "test@example.invalid")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "pytest-calls.log"))
    monkeypatch.setenv("REAL_PYTHON", sys.executable)
    monkeypatch.setenv("HOOK_REMOTE_URL", GITHUB_URL)
    gh_dir = tmp_path / "fake-gh"
    (gh_dir / "bin").mkdir(parents=True)
    fake = gh_dir / "bin" / "gh"
    fake.write_text(FAKE_GH.format(python=sys.executable))
    fake.chmod(0o755)
    monkeypatch.setenv("FAKE_GH_DIR", str(gh_dir))
    monkeypatch.setenv("PATH", f"{gh_dir / 'bin'}{os.pathsep}{os.environ['PATH']}")
    assert shutil.which("gh") == str(fake), "the real gh would be reached"

    # `--wait-ci 0` everywhere; a test that sleeps has a bug.
    # `release.time` is the global `time` module, so the patch also reaches the
    # stdlib: `subprocess` polls with `time.sleep` while it waits on a child under
    # a `timeout=` (tests-t1 gave every git call one). That poll is the deadline,
    # not a test sleeping; every other caller still fails.
    real_sleep = time.sleep
    subprocess_file = subprocess.__file__

    def no_sleeping(seconds):
        if sys._getframe(1).f_code.co_filename == subprocess_file:
            return real_sleep(seconds)
        raise AssertionError(f"a test slept {seconds}s")

    monkeypatch.setattr(release.time, "sleep", no_sleeping)

    real = release._check_output

    def with_a_github_origin(cmd, **kwargs):
        if "get-url" in cmd and cmd[-1] == "origin":
            # The fetch URL and the push URL are different questions, and a
            # remote with a `pushurl` answers them differently.
            if "--push" in cmd:
                return os.environ.get("FAKE_PUSH_URL", GITHUB_URL) + "\n"
            return os.environ.get("FAKE_FETCH_URL", GITHUB_URL) + "\n"
        return real(cmd, **kwargs)

    monkeypatch.setattr(release, "_check_output", with_a_github_origin)


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A checkout a release can be cut from: clean `dev` at `origin/dev`,
    one commit ahead of `origin/main`, the section dated today, the hook
    installed, a venv, and `gate` green on the commit."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    root = tmp_path / "checkout"
    _write(root, ".gitignore", ".venv/\n__pycache__/\n")
    _write(root, "pyproject.toml", f'[project]\nname = "widget"\nversion = "{VERSION}"\n')
    _write(root, "CHANGELOG.md", "# Changelog\n")
    _write(root, "pyrite/__init__.py", "")
    _write(root, "pyrite/b.py", "def func():\n    return 1\n")
    _write(root, "tests/__init__.py", "")
    _write(root, "tests/test_b.py", "import pyrite.b\n\ndef test_b():\n    pass\n")
    # The hook's script is this repository's own, so the rehearsal asks the
    # real decision code -- with the modules it imports beside it.
    for name in ("test-affected", "verified_commit.py", "heavy_lock.py"):
        (root / "scripts").mkdir(exist_ok=True)
        shutil.copy(REPO / "scripts" / name, root / "scripts" / name)
    _git(root, "init", "-q", "-b", "dev")
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "the last release")
    (root / "pyrite" / "b.py").write_text("def func():\n    return 2\n")
    (root / "CHANGELOG.md").write_text(changelog())
    _git(root, "commit", "-qam", f"release: {VERSION}")
    _git(root, "push", "-q", "origin", "dev:refs/heads/dev", "dev~1:refs/heads/main")
    venv_python = root / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text(FAKE_VENV_PYTHON)
    venv_python.chmod(0o755)
    hook = root / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(PRE_PUSH)
    hook.chmod(0o755)
    gh_answers(green(_git(root, "rev-parse", "HEAD").strip()))
    return root


def gh_answers(check_runs: dict) -> None:
    """What the fake `gh` says. First matching needle wins."""
    answers = {
        "check-runs": check_runs,
        "label list": {"stdout": '[{"name":"release-blocker"}]'},
        "pr list": {"stdout": "[]"},
        "release view": {"exit": 1, "stderr": "release not found\n"},
        "release create": {"stdout": "https://github.com/acme/widget/releases/tag/v0.1.0\n"},
        "releases/tags": {"stdout": "2026-01-01T00:00:00Z\n"},
    }
    (Path(os.environ["FAKE_GH_DIR"]) / "answer.json").write_text(json.dumps(answers))


def gh_calls() -> list[str]:
    log = Path(os.environ["FAKE_GH_DIR"]) / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def pytest_calls() -> list[str]:
    log = Path(os.environ["FAKE_LOG"])
    return log.read_text().splitlines() if log.exists() else []


def head(checkout: Path) -> str:
    return _git(checkout, "rev-parse", "HEAD").strip()


def rehearse(checkout: Path, *argv: str):
    """The dry run. Returns (exit code, the writes it planned)."""
    args = release.parse_args([VERSION, "--repo", str(checkout), "--wait-ci", "0", *argv])
    code, runner = release.run_release(args)
    return code, runner.planned


def make_untracked(checkout: Path) -> None:
    (checkout / "notes.txt").write_text("scratch\n")
    _write(checkout, "scratch/plan.md", "a plan\n")
    _write(checkout, "scratch/deep/data.json", "{}\n")


def make_stale_date(checkout: Path) -> str:
    """The section dated the day the release was PREPARED, committed and
    pushed, with `gate` green on it: everything else is in order."""
    prepared = (date.today() - timedelta(days=1)).isoformat()
    (checkout / "CHANGELOG.md").write_text(changelog(prepared))
    _git(checkout, "commit", "-qam", "release: dated yesterday")
    _git(checkout, "push", "-q", "origin", "dev:refs/heads/dev")
    gh_answers(green(head(checkout)))
    return prepared


class TestReady:
    def test_a_checkout_that_is_ready_is_reported_ready(self, checkout, capsys):
        code, planned = rehearse(checkout)
        out = capsys.readouterr().out
        assert code == 0, out
        assert f"Dry run complete: v{VERSION} looks releasable." in out
        assert [c for c in planned if c[:3] == ["gh", "release", "create"]]

    def test_it_says_the_commit_is_verified_and_what_the_hook_will_do(self, checkout, capsys):
        rehearse(checkout)
        out = capsys.readouterr().out
        sha = head(checkout)[:12]
        assert f"{sha} is verified" in out and "gate=success" in out
        assert "push to main: the hook skips" in out
        assert "push of the tag" in out
        assert pytest_calls() == [], "a rehearsal of a verified commit ran or collected a suite"

    def test_it_says_which_checkout_it_described(self, checkout, capsys):
        """The untracked files of 0.25.7 were in the checkout the release ran
        in, not the one it was rehearsed in."""
        rehearse(checkout)
        assert str(checkout) in capsys.readouterr().out

    def test_no_hook_installed_is_said_and_is_not_a_failure(self, checkout, capsys):
        (checkout / ".git" / "hooks" / "pre-push").unlink()
        code, _planned = rehearse(checkout)
        assert code == 0
        assert "no pre-push hook is installed here" in capsys.readouterr().out

    def test_the_rehearsal_changes_nothing(self, checkout):
        before = _git(checkout, "status", "--porcelain", "--untracked-files=all", "--ignored")
        refs = _git(checkout, "for-each-ref")
        rehearse(checkout)
        assert _git(checkout, "for-each-ref") == refs
        after = _git(checkout, "status", "--porcelain", "--untracked-files=all", "--ignored")
        assert after == before


class TestEachPreconditionIsNamed:
    """Each failing precondition in turn: exit 1, no write planned, and the
    message says what is wrong in terms the maintainer can act on."""

    def test_untracked_files_are_named_one_by_one(self, checkout, capsys):
        make_untracked(checkout)
        code, planned = rehearse(checkout)
        err = capsys.readouterr().err
        assert code == 1 and planned == []
        assert "3 untracked file(s)" in err
        for path in ("notes.txt", "scratch/plan.md", "scratch/deep/data.json"):
            assert path in err, f"{path} not named:\n{err}"

    def test_a_modified_file_is_told_apart_from_an_untracked_one(self, checkout, capsys):
        (checkout / "pyrite" / "b.py").write_text("def func():\n    return 3\n")
        (checkout / "notes.txt").write_text("scratch\n")
        code, planned = rehearse(checkout)
        err = capsys.readouterr().err
        assert code == 1 and planned == []
        assert "uncommitted changes" in err and " M pyrite/b.py" in err
        assert "1 untracked file(s)" in err and "notes.txt" in err

    def test_a_section_dated_the_prepare_day_says_the_date_it_has(self, checkout, capsys):
        prepared = make_stale_date(checkout)
        code, planned = rehearse(checkout)
        err = capsys.readouterr().err
        assert code == 1 and planned == []
        assert f"is dated {prepared}, today is {TODAY}" in err
        assert err.count("FAIL at ") == 1, f"only the date is wrong here:\n{err}"

    def test_another_branch_is_named(self, checkout, capsys):
        _git(checkout, "checkout", "-q", "-b", "release/prepare-0.1.0")
        code, planned = rehearse(checkout)
        err = capsys.readouterr().err
        assert code == 1 and planned == []
        assert "on branch 'release/prepare-0.1.0'" in err
        assert err.count("FAIL at ") == 1, err

    def test_a_dev_that_is_not_origin_dev_is_named(self, checkout, capsys):
        (checkout / "pyrite" / "b.py").write_text("def func():\n    return 3\n")
        _git(checkout, "commit", "-qam", "not pushed")
        code, planned = rehearse(checkout)
        err = capsys.readouterr().err
        assert code == 1 and planned == []
        assert f"HEAD ({head(checkout)[:12]}) is not origin/dev" in err

    @pytest.mark.parametrize("case", sorted(CASES))
    def test_a_commit_that_is_not_verified_is_named(self, checkout, capsys, case):
        """Every way the lookup fails or is ambiguous, through the dry run:
        the commit is not verified, and the release says so."""
        gh_answers(CASES[case][0](head(checkout)))
        code, planned = rehearse(checkout)
        err = capsys.readouterr().err
        assert code == 1 and planned == [], f"{case}: reported ready"
        assert "FAIL at ci: " in err and head(checkout)[:12] in err, err

    @pytest.mark.parametrize("url", NOT_GITHUB_REMOTES)
    def test_an_origin_that_only_looks_like_github_is_named(
        self, checkout, capsys, monkeypatch, url
    ):
        """`https://notgithub.com/acme/widget.git` was read as acme/widget on
        GitHub, and the release then asked GitHub about a commit it was about
        to push somewhere else."""
        monkeypatch.setenv("FAKE_PUSH_URL", url)
        code, planned = rehearse(checkout)
        err = capsys.readouterr().err
        assert code == 1 and planned == []
        assert "is not a GitHub repository URL" in err
        assert not any("check-runs" in call for call in gh_calls())

    def test_the_repository_is_the_one_pushed_to_not_the_one_fetched_from(
        self, checkout, capsys, monkeypatch
    ):
        """With a `pushurl` set, `main` and the tag go to the push URL. That
        is the repository the release must ask GitHub about, cut the release
        on, and hand to the hook -- all three, or they disagree."""
        monkeypatch.setenv("FAKE_FETCH_URL", "https://mirror.example/acme/widget.git")
        code, planned = rehearse(checkout)
        out = capsys.readouterr().out
        assert code == 0, out
        assert "origin is acme/widget" in out
        assert "push to main: the hook skips" in out and "on acme/widget" in out
        create = next(c for c in planned if c[:3] == ["gh", "release", "create"])
        assert create[create.index("--repo") + 1] == "acme/widget"

    def test_a_push_url_that_is_not_github_is_named_even_if_the_fetch_url_is(
        self, checkout, capsys, monkeypatch
    ):
        monkeypatch.setenv("FAKE_PUSH_URL", "https://mirror.example/acme/widget.git")
        code, planned = rehearse(checkout)
        assert code == 1 and planned == []
        assert "is not a GitHub repository URL" in capsys.readouterr().err

    def test_a_cancelled_workflow_run_is_named_and_the_remedy_is_to_rerun_it(
        self, checkout, capsys
    ):
        """One of the two workflow runs that tested the commit was cancelled.
        Nothing is wrong on dev, so "fix it on dev" sends the maintainer
        nowhere: the run is named, and the remedy is to re-run it."""
        from tests.verified_commit_cases import answer, run_of

        sha = head(checkout)
        url = "https://github.com/acme/widget/actions/runs/777/job/42"
        gh_answers(
            {
                "stdout": answer(
                    run_of(sha),
                    run_of(sha, conclusion="cancelled", check_suite={"id": 2}, html_url=url),
                )
            }
        )
        code, planned = rehearse(checkout)
        err = capsys.readouterr().err
        assert code == 1 and planned == []
        assert f"gate=cancelled ({url})" in err
        assert "gh run rerun 777" in err and "re-run" in err
        assert "Fix it on dev" not in err

    def test_a_venv_that_cannot_import_the_suite_is_named(self, checkout, capsys, monkeypatch):
        """0.25.7: the hook's venv had no `hypothesis`. With the commit
        verified the hook skips and never imports anything, so this is the
        case where it would run: PYRITE_PUSH_FORCE=1."""
        monkeypatch.setenv("PYRITE_PUSH_FORCE", "1")
        monkeypatch.setenv("FAKE_COLLECT_EXIT", "2")
        code, planned = rehearse(checkout)
        err = capsys.readouterr().err
        assert code == 1 and planned == []
        assert "FAIL at push_rehearsal: " in err
        assert "would stop the push to main" in err
        assert "No module named 'hypothesis'" in err
        assert str(checkout / ".venv" / "bin" / "python") in err

    def test_a_checkout_with_no_venv_is_named(self, checkout, capsys):
        shutil.rmtree(checkout / ".venv")
        code, planned = rehearse(checkout)
        err = capsys.readouterr().err
        assert code == 1 and planned == []
        assert "FAIL at push_rehearsal: " in err and ".venv" in err

    def test_a_suite_that_would_run_is_said_loudly_but_is_not_a_failure(
        self, checkout, capsys, monkeypatch
    ):
        """PYRITE_PUSH_FORCE=1 asks for the suite at the push. It imports;
        whether it passes cannot be known without running it, and a red run
        stops --execute at its first write, before anything has moved."""
        monkeypatch.setenv("PYRITE_PUSH_FORCE", "1")
        code, planned = rehearse(checkout)
        out = capsys.readouterr().out
        assert code == 0 and planned
        assert "WILL RUN the suite at the push to main" in out
        assert "PYRITE_PUSH_FORCE" in out
        assert "looks releasable, with 1 caution(s)" in out
        (call,) = pytest_calls()
        assert "--collect-only" in call


class TestEveryOneIsNamedInOneRun:
    def test_the_0_25_7_release_day_in_one_rehearsal(self, checkout, capsys, monkeypatch):
        """Rehearsed on the prepare branch, with scratch files about, the
        section dated yesterday, CI still running and a venv that cannot
        import the suite: one dry run names all five. It used to name the
        first and stop."""
        prepared = make_stale_date(checkout)
        _git(checkout, "checkout", "-q", "-b", "release/prepare-0.1.0")
        make_untracked(checkout)
        gh_answers(CASES["the check is in progress"][0](head(checkout)))
        monkeypatch.setenv("FAKE_COLLECT_EXIT", "2")

        code, planned = rehearse(checkout)
        captured = capsys.readouterr()
        err = captured.err
        assert code == 1 and planned == []
        assert "3 untracked file(s)" in err and "scratch/plan.md" in err
        assert "on branch 'release/prepare-0.1.0'" in err
        assert f"is dated {prepared}, today is {TODAY}" in err
        assert "still running" in err and "gate=in_progress" in err
        assert "No module named 'hypothesis'" in err
        assert err.count("FAIL at ") == 5, err
        assert "is NOT ready" in err and "the 5 check(s)" in err
        assert "looks releasable" not in captured.out

    def test_each_is_flagged_where_it_is_found_too(self, checkout, capsys):
        make_untracked(checkout)
        make_stale = (date.today() - timedelta(days=3)).isoformat()
        (checkout / "CHANGELOG.md").write_text(changelog(make_stale))
        rehearse(checkout)
        out = capsys.readouterr().out
        flagged = [line for line in out.splitlines() if "!! WOULD STOP --execute:" in line]
        assert len(flagged) >= 2, out

    def test_nothing_after_the_checks_is_rehearsed_behind_a_failure(self, checkout, capsys):
        make_untracked(checkout)
        code, planned = rehearse(checkout)
        out = capsys.readouterr().out
        assert code == 1 and planned == []
        assert "IRREVERSIBLE" not in out and "WOULD RUN: git" not in out

    @pytest.mark.control(
        reason="the collect-all dry run must keep this one hard stop; the old dry run stopped everywhere"
    )
    def test_a_directory_that_is_not_a_checkout_still_stops_at_once(self, tmp_path, capsys):
        empty = tmp_path / "not-a-repo"
        empty.mkdir()
        code, planned = rehearse(empty)
        err = capsys.readouterr().err
        assert code == 1 and planned == []
        assert err.count("FAIL at preconditions") == 1


class TestExecute:
    """`--execute` here pushes to the fixture's own bare repository in
    tmp_path and "creates the release" with the fake `gh`. The asserts at the
    top of each test are what keep it there."""

    def _contained(self, checkout: Path) -> None:
        origin = _git(checkout, "config", "remote.origin.url").strip()
        assert Path(origin).is_dir() and str(checkout.parent) in origin, origin
        assert shutil.which("gh").startswith(os.environ["FAKE_GH_DIR"])

    def test_it_still_stops_at_the_first_failed_check(self, checkout, capsys):
        self._contained(checkout)
        make_stale_date(checkout)
        make_untracked(checkout)
        args = release.parse_args(
            [
                VERSION,
                "--repo",
                str(checkout),
                "--wait-ci",
                "0",
                "--execute",
                "--skip-install-check",
            ]
        )
        code, runner = release.run_release(args)
        err = capsys.readouterr().err
        assert code == 1 and runner.planned == [] and runner.performed == []
        assert "untracked" in err and "is dated" not in err
        assert err.count("FAIL at ") == 1
        assert "Nothing further was attempted." in err

    def test_ready_means_execute_is_not_stopped_by_the_hook(self, checkout, capsys, monkeypatch):
        """The invariant: a dry run that says ready means --execute does not
        stop on these. The push to `main` goes through git's real pre-push
        hook, which runs the real scripts/test-affected -- and the suite it
        would run is rigged to FAIL, so the push succeeds only because the
        hook skipped a verified commit."""
        self._contained(checkout)
        monkeypatch.setenv("FAKE_EXIT", "1")
        sha = head(checkout)
        code, _planned = rehearse(checkout)
        assert code == 0

        args = release.parse_args(
            [
                VERSION,
                "--repo",
                str(checkout),
                "--wait-ci",
                "0",
                "--execute",
                "--skip-install-check",
            ]
        )
        code, runner = release.run_release(args)
        captured = capsys.readouterr()
        assert code == 0, captured.err
        origin = checkout.parent / "origin.git"
        assert _git(origin, "rev-parse", "refs/heads/main").strip() == sha
        assert _git(origin, "rev-parse", f"refs/tags/v{VERSION}^{{commit}}").strip() == sha
        assert pytest_calls() == [], "the hook ran the suite on a verified commit"
        assert any("release create" in call for call in gh_calls())

    def test_the_hook_does_stop_an_unverified_push(self, checkout, monkeypatch):
        """The control for the test above: same hook, same rigged suite, a
        commit GitHub has not passed -- the push is refused."""
        self._contained(checkout)
        monkeypatch.setenv("FAKE_EXIT", "1")
        gh_answers(CASES["the check failed"][0](head(checkout)))
        pushed = subprocess.run(
            ["git", "push", "origin", f"{head(checkout)}:refs/heads/main"],
            cwd=checkout,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert pushed.returncode != 0
        assert len(pytest_calls()) == 1
        origin = checkout.parent / "origin.git"
        assert _git(origin, "rev-parse", "refs/heads/main").strip() != head(checkout)
