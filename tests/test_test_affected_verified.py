"""The pre-push path skips a push whose tip commit is verified, and only then.

On the 0.25.7 release the hook re-ran the full local suite on a commit CI had
already passed, in a venv missing a dev dependency, and blocked the push to
`main`. `scripts/test-affected` now asks the one definition of a verified
commit (scripts/verified_commit.py) about the pushed tip, and skips with one
line when the required check is green on that exact SHA.

A skip lets a push through with no local run, so most of this file is the
other direction: every way the lookup fails or is ambiguous, the suite runs
exactly as before.

Every case builds a throwaway repository with a local bare `origin`, runs the
real script the way the hook does (PRE_COMMIT_* set), and replaces the GitHub
lookup at the one seam where it is made: a fake `gh` executable first on PATH.
`.venv/bin/python` is a shell script that logs each pytest invocation. Nothing
here reaches GitHub.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from tests.verified_commit_cases import (
    CASES,
    GITHUB_REMOTES,
    NOT_GITHUB_REMOTES,
    FAKE_GH,
    FAKE_VENV_PYTHON,
    VERIFIED,
    answer,
    green,
    run_of,
)

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SCRIPT = SCRIPTS / "test-affected"
GITHUB_URL = "git@github.com:acme/widget.git"

# A "never skip" guard passes on a base that never skips on a verdict, by
# construction; what it pins is that the skip does not over-reach. Each fails
# when its guard alone is removed (see the PR's Guards list).
NEVER = pytest.mark.control(
    reason="never-skip guard: passes where nothing skips on a verdict; red when its guard is removed"
)


def _write(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "PYRITE_CONFIG_DIR", "PYRITE_DATA_DIR"):
        monkeypatch.setenv(var, str(home))
    for var in (
        "PRE_COMMIT_FROM_REF", "PRE_COMMIT_TO_REF", "PRE_COMMIT_REMOTE_URL",
        "PRE_COMMIT_REMOTE_NAME", "PYRITE_PUSH_FULL", "PYRITE_PUSH_FORCE",
        "PYRITE_PUSH_EXPERIMENTAL", "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "FAKE_EXIT",
        "FAKE_COLLECT_EXIT", "FAKE_PY_VERSION", "GH_TOKEN", "GITHUB_TOKEN", "GH_HOST",
    ):  # fmt: skip
        monkeypatch.delenv(var, raising=False)
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "Test")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "test@example.invalid")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "pytest-calls.log"))
    monkeypatch.setenv("REAL_PYTHON", sys.executable)
    # The fake `gh` goes FIRST on PATH: the real one is never reached.
    gh_dir = tmp_path / "fake-gh"
    (gh_dir / "bin").mkdir(parents=True)
    fake = gh_dir / "bin" / "gh"
    fake.write_text(FAKE_GH.format(python=sys.executable))
    fake.chmod(0o755)
    monkeypatch.setenv("FAKE_GH_DIR", str(gh_dir))
    monkeypatch.setenv("PATH", f"{gh_dir / 'bin'}{os.pathsep}{os.environ['PATH']}")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """`origin/main` at A; `origin/dev` and local `dev` at B, which changes
    pyrite/b.py. Pushing B to main is the release's push."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    root = tmp_path / "repo"
    files = {
        ".gitignore": ".venv/\n",
        "pyrite/__init__.py": "",
        "pyrite/a.py": "A = 1\n",
        "pyrite/b.py": "def func():\n    return 1\n",
        "tests/__init__.py": "",
        "tests/test_a.py": "import pyrite.a\n\ndef test_a():\n    pass\n",
        "tests/test_b.py": "import pyrite.b\n\ndef test_b():\n    pass\n",
    }
    for rel, text in files.items():
        _write(root, rel, text)
    _git(root, "init", "-q", "-b", "dev")
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "A: base")
    (root / "pyrite" / "b.py").write_text("def func():\n    return 2\n")
    _git(root, "commit", "-qam", "B: change b")
    _git(root, "push", "-q", "origin", "dev:refs/heads/dev", "dev~1:refs/heads/main")
    fake = root / ".venv" / "bin" / "python"
    fake.parent.mkdir(parents=True)
    fake.write_text(FAKE_VENV_PYTHON)
    fake.chmod(0o755)
    return root


def _sha(repo: Path, rev: str = "HEAD") -> str:
    return _git(repo, "rev-parse", rev).strip()


def _gh_answers(reply: dict | None) -> None:
    path = Path(os.environ["FAKE_GH_DIR"]) / "answer.json"
    if reply is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(json.dumps({"check-runs": reply}))


def _gh_calls() -> list[str]:
    log = Path(os.environ["FAKE_GH_DIR"]) / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def _pytest_calls() -> list[str]:
    log = Path(os.environ["FAKE_LOG"])
    return log.read_text().splitlines() if log.exists() else []


def _hook(
    repo: Path,
    *args: str,
    to: str | None = None,
    frm: str | None = None,
    url: str | None = GITHUB_URL,
    mode: str = "--run",
    **env: str,
) -> subprocess.CompletedProcess:
    """The script as the pre-push hook runs it for one pushed ref."""
    hook_env = {
        "PRE_COMMIT_TO_REF": to or _sha(repo),
        "PRE_COMMIT_FROM_REF": frm if frm is not None else _sha(repo, "origin/main"),
        "PRE_COMMIT_REMOTE_NAME": "origin",
    }
    if url is not None:
        hook_env["PRE_COMMIT_REMOTE_URL"] = url
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(repo), mode, "-n", "2", *args],
        capture_output=True,
        text=True,
        env={**os.environ, **hook_env, **env},
        timeout=120,
    )


def _stamps(repo: Path) -> list[Path]:
    d = repo / ".git" / "pyrite-test-stamps"
    return sorted(d.glob("*.json")) if d.is_dir() else []


class TestAVerifiedTipSkips:
    def test_the_release_push_does_not_rerun_the_suite(self, repo):
        sha = _sha(repo)
        _gh_answers(green(sha))
        out = _hook(repo)
        assert out.returncode == 0, out.stderr
        assert _pytest_calls() == [], "the suite ran on a commit CI had already passed"

    def test_it_says_so_in_one_line_naming_the_sha_the_check_and_the_repo(self, repo):
        sha = _sha(repo)
        _gh_answers(green(sha))
        out = _hook(repo)
        said = [line for line in out.stderr.splitlines() if "verified" in line]
        assert len(said) == 1, out.stderr
        assert sha[:12] in said[0] and "gate=success" in said[0] and "acme/widget" in said[0]

    @pytest.mark.parametrize("case", sorted(VERIFIED))
    def test_every_answer_that_verifies_skips(self, repo, case):
        _gh_answers(VERIFIED[case](_sha(repo)))
        out = _hook(repo)
        assert out.returncode == 0, out.stderr
        assert _pytest_calls() == [], case

    def test_it_asks_about_the_pushed_tip_in_the_repo_pushed_to(self, repo):
        sha = _sha(repo)
        _gh_answers(green(sha))
        _hook(repo)
        assert _gh_calls() == [f"api repos/acme/widget/commits/{sha}/check-runs?per_page=100"]

    def test_a_range_that_selects_the_full_suite_is_still_skipped(self, repo):
        """The release range always touches pyproject.toml, which selects the
        full suite. That is the push the skip exists for."""
        (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        _git(repo, "add", "pyproject.toml")
        _git(repo, "commit", "-qm", "C: touches test configuration")
        _git(repo, "push", "-q", "origin", "dev:refs/heads/dev")
        _gh_answers(green(_sha(repo)))
        out = _hook(repo)
        assert out.returncode == 0 and _pytest_calls() == [], out.stderr

    @pytest.mark.parametrize("url", GITHUB_REMOTES)
    def test_every_spelling_of_a_github_remote_is_understood(self, repo, url):
        _gh_answers(green(_sha(repo)))
        out = _hook(repo, url=url)
        assert out.returncode == 0 and _pytest_calls() == [], out.stderr
        assert "repos/acme/widget/" in _gh_calls()[0]

    @NEVER
    def test_a_verified_skip_writes_no_stamp(self, repo):
        """A stamp says THIS interpreter passed THIS tree. A verdict from CI
        says neither, and must not turn into one."""
        _gh_answers(green(_sha(repo)))
        _hook(repo)
        assert _stamps(repo) == []


class TestFailClosed:
    """The property that matters most: an untested commit is never skipped
    because a lookup failed. Each row of the shared list, through the hook."""

    @pytest.mark.parametrize("case", sorted(CASES))
    def test_the_suite_runs(self, repo, case):
        behaviour, _state = CASES[case]
        _gh_answers(behaviour(_sha(repo)))
        out = _hook(repo)
        ran = [c for c in _pytest_calls() if "tests/test_b.py" in c]
        assert len(ran) == 1, f"{case}: the suite did not run\n{out.stderr}"
        assert out.returncode == 0  # the fake suite passed
        assert len(_gh_calls()) == 1, f"{case}: {len(_gh_calls())} requests"

    @NEVER
    @pytest.mark.parametrize("case", ["no network", "the check failed", "the page is truncated"])
    def test_a_red_suite_still_blocks_the_push(self, repo, case):
        _gh_answers(CASES[case][0](_sha(repo)))
        out = _hook(repo, FAKE_EXIT="1")
        assert out.returncode == 1

    def test_it_says_why_it_is_running(self, repo):
        sha = _sha(repo)
        _gh_answers(CASES["the check is in progress"][0](sha))
        out = _hook(repo)
        said = [line for line in out.stderr.splitlines() if "not verified" in line]
        assert len(said) == 1 and sha[:12] in said[0] and "gate=in_progress" in said[0], out.stderr

    def test_gh_not_installed(self, repo, tmp_path, monkeypatch):
        # A PATH that holds git and nothing else: no fake `gh`, no real one.
        tools = tmp_path / "tools"
        tools.mkdir()
        (tools / "git").symlink_to(shutil.which("git"))
        assert shutil.which("gh", path=str(tools)) is None
        out = _hook(repo, PATH=str(tools))
        assert len(_pytest_calls()) == 1, out.stderr
        assert "not verified" in out.stderr

    @NEVER
    def test_gh_with_no_answer_at_all(self, repo):
        _gh_answers(None)  # the fake exits 97
        _hook(repo)
        assert len(_pytest_calls()) == 1

    def test_a_request_that_never_answers_is_abandoned_and_the_suite_runs(self, repo):
        reply = green(_sha(repo)) | {"sleep": 600}
        _gh_answers(reply)
        started = time.monotonic()
        out = _hook(repo)
        waited = time.monotonic() - started
        assert len(_pytest_calls()) == 1, out.stderr
        assert "timed out" in out.stderr
        assert len(_gh_calls()) == 1, "a retry after a timeout can hang a push"
        assert waited < 120, f"the hook waited {waited:.0f}s on a dead request"

    @NEVER
    @pytest.mark.parametrize(
        "url",
        [None, "", "/srv/git/widget.git", "origin", *NOT_GITHUB_REMOTES],
    )
    def test_a_remote_that_is_not_a_github_repository_is_never_asked_about(self, repo, url):
        _gh_answers(green(_sha(repo)))
        _hook(repo, url=url)
        assert len(_pytest_calls()) == 1
        assert _gh_calls() == []


class TestOnlyThePushedTip:
    @NEVER
    def test_a_verified_ancestor_does_not_vouch_for_a_commit_on_top(self, repo):
        """B is verified; C on top of it, already on a remote branch, is not."""
        verified = _sha(repo)
        (repo / "pyrite" / "b.py").write_text("def func():\n    return 3\n")
        _git(repo, "commit", "-qam", "C: on top of the verified commit")
        _git(repo, "push", "-q", "origin", "dev:refs/heads/topic")
        tip = _sha(repo)
        _gh_answers(green(verified))  # GitHub answers for B, whatever is asked
        out = _hook(repo)
        assert len(_pytest_calls()) == 1, out.stderr
        assert _gh_calls() == [f"api repos/acme/widget/commits/{tip}/check-runs?per_page=100"]

    @NEVER
    def test_a_tip_no_remote_has_makes_no_request(self, repo):
        """An ordinary push of new commits: GitHub cannot have tested what it
        has never seen, so the hook does not ask, and stays off the network."""
        (repo / "pyrite" / "b.py").write_text("def func():\n    return 3\n")
        _git(repo, "commit", "-qam", "C: new work")
        _gh_answers(green(_sha(repo)))
        _hook(repo)
        assert len(_pytest_calls()) == 1
        assert _gh_calls() == []

    def test_an_annotated_tag_is_asked_about_as_its_commit(self, repo):
        sha = _sha(repo)
        _git(repo, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
        tag_object = _sha(repo, "v1.0.0")
        assert tag_object != sha
        _gh_answers(green(sha))
        out = _hook(repo, to=tag_object, frm="0" * 40)
        assert out.returncode == 0 and _pytest_calls() == [], out.stderr
        assert sha in _gh_calls()[0]

    def test_a_new_branch_at_a_verified_commit_skips(self, repo):
        _gh_answers(green(_sha(repo)))
        out = _hook(repo, frm="0" * 40)
        assert out.returncode == 0 and _pytest_calls() == []

    def test_a_force_push_to_a_verified_commit_skips(self, repo):
        """What is verified is the tip's whole tree, not a range: the history
        the push replaces does not enter into it."""
        verified = _sha(repo)
        _git(repo, "checkout", "-q", "-b", "rewritten", "dev~1")
        (repo / "pyrite" / "a.py").write_text("A = 2\n")
        _git(repo, "commit", "-qam", "D: diverges")
        replaced = _sha(repo)
        _git(repo, "checkout", "-q", "dev")
        _gh_answers(green(verified))
        out = _hook(repo, to=verified, frm=replaced)
        assert out.returncode == 0 and _pytest_calls() == [], out.stderr

    @NEVER
    def test_a_ref_that_names_no_commit_is_refused_as_before(self, repo):
        _gh_answers(green(_sha(repo)))
        out = _hook(repo, to="1" * 40)
        assert out.returncode != 0
        assert "verified" not in out.stderr and _gh_calls() == []

    @NEVER
    def test_outside_the_hook_nothing_is_asked(self, repo):
        """`scripts/test-affected --run` by hand runs what it was asked to
        run: the skip belongs to a push, whose tip the hook names."""
        _gh_answers(green(_sha(repo)))
        out = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(repo), "--run", "-n", "2", "--full"],
            capture_output=True,
            text=True,
            # Even with a remote named, as a stray export would leave it.
            env={**os.environ, "PRE_COMMIT_REMOTE_URL": GITHUB_URL},
            timeout=120,
        )
        assert out.returncode == 0 and len(_pytest_calls()) == 1
        assert _gh_calls() == []

    @NEVER
    def test_a_lookup_that_crashes_before_it_asks_runs_the_suite(self, repo):
        """PYRITE_PUSH_FULL skips the diff that would have refused this ref,
        so the lookup is the first thing to meet it. git failing there is a
        failed lookup like any other: the suite runs, the hook does not die."""
        _gh_answers(green(_sha(repo)))
        out = _hook(repo, to="1" * 40, PYRITE_PUSH_FULL="1")
        assert out.returncode == 0, out.stderr
        assert len(_pytest_calls()) == 1 and _gh_calls() == []


class TestNoNewBypass:
    """The existing switches keep their meaning, and none was added."""

    @NEVER
    def test_the_force_variable_runs_a_verified_tip_and_asks_nothing(self, repo):
        _gh_answers(green(_sha(repo)))
        _hook(repo, PYRITE_PUSH_FORCE="1")
        assert len(_pytest_calls()) == 1
        assert _gh_calls() == []

    @NEVER
    def test_the_force_flag_does_too(self, repo):
        _gh_answers(green(_sha(repo)))
        _hook(repo, "--force")
        assert len(_pytest_calls()) == 1 and _gh_calls() == []

    @NEVER
    @pytest.mark.parametrize("how", ["PYRITE_PUSH_FULL", "--full"])
    def test_an_explicit_request_for_the_full_suite_runs(self, repo, how):
        """A green `gate` says CI accepted the commit, not that the full suite
        ran: a pull-request `gate` is one interpreter and passes with `test`
        skipped. Asking for everything gets everything."""
        _gh_answers(green(_sha(repo)))
        if how.startswith("--"):
            _hook(repo, how)
        else:
            _hook(repo, **{how: "1"})
        (call,) = _pytest_calls()
        assert "tests/ extensions/" in call
        assert _gh_calls() == []

    @NEVER
    def test_the_experimental_scope_is_not_covered_by_gate(self, repo):
        """CI's `gate` does not need the experimental job, so a green gate
        says nothing about the tests PYRITE_PUSH_EXPERIMENTAL=1 adds."""
        _gh_answers(green(_sha(repo)))
        _hook(repo, PYRITE_PUSH_EXPERIMENTAL="1")
        assert len(_pytest_calls()) == 1 and _gh_calls() == []

    @NEVER
    def test_a_pass_stamp_is_used_first_and_nothing_is_asked(self, repo):
        subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(repo), "--run", "-n", "2", "--full"],
            check=True,
            capture_output=True,
            timeout=120,
        )
        assert len(_stamps(repo)) == 1
        out = _hook(repo)
        assert "already passed on tree" in out.stderr
        assert len(_pytest_calls()) == 1 and _gh_calls() == []

    def test_no_environment_variable_but_the_documented_ones_is_read(self):
        """A new variable that can skip the suite is a new bypass. The script
        reads these and no others; adding one means changing this list, in
        review."""
        import re

        names = set()
        for path in (SCRIPT, SCRIPTS / "verified_commit.py"):
            names |= set(
                re.findall(
                    r"""["']((?:PYRITE|PRE_COMMIT|GH|GITHUB)_[A-Z_]+)["']""", path.read_text()
                )
            )
        assert names == {
            "PYRITE_PUSH_FULL",
            "PYRITE_PUSH_FORCE",
            "PYRITE_PUSH_EXPERIMENTAL",
            "PRE_COMMIT_FROM_REF",
            "PRE_COMMIT_TO_REF",
            "PRE_COMMIT_REMOTE_URL",
            # set FOR the lookup, never read: no prompt, no update notice
            "GH_PROMPT_DISABLED",
            "GH_NO_UPDATE_NOTIFIER",
        }


class TestPreflight:
    """`--preflight` answers "what would the hook do for this push" with the
    hook's own decision code, and runs nothing but a collection. The release
    dry run asks it (tests/test_release_rehearsal.py)."""

    def test_a_verified_tip_would_skip(self, repo):
        _gh_answers(green(_sha(repo)))
        out = _hook(repo, mode="--preflight")
        assert out.returncode == 0, out.stderr
        assert out.stdout.startswith("preflight: skip: "), out.stdout
        assert "gate=success" in out.stdout
        assert _pytest_calls() == []

    def test_a_stamped_tree_would_skip(self, repo):
        subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(repo), "--run", "-n", "2", "--full"],
            check=True,
            capture_output=True,
            timeout=120,
        )
        out = _hook(repo, mode="--preflight")
        assert out.stdout.startswith("preflight: skip: ") and "already passed" in out.stdout

    def test_an_unverified_tip_would_run_and_only_a_collection_happens(self, repo):
        _gh_answers(CASES["the check failed"][0](_sha(repo)))
        out = _hook(repo, mode="--preflight")
        assert out.returncode == 0, out.stderr
        assert out.stdout.startswith("preflight: would run: "), out.stdout
        (call,) = _pytest_calls()
        assert "--collect-only" in call and "tests/test_b.py" in call
        assert "-n" not in call.split()

    def test_a_suite_that_cannot_be_imported_fails_the_preflight(self, repo):
        """0.25.7: the hook's venv had no `hypothesis`."""
        _gh_answers(CASES["the check failed"][0](_sha(repo)))
        out = _hook(repo, mode="--preflight", FAKE_COLLECT_EXIT="2")
        assert out.returncode == 1
        assert out.stdout.startswith("preflight: cannot run: "), out.stdout
        assert "hypothesis" in out.stdout + out.stderr
        assert str(repo / ".venv" / "bin" / "python") in out.stdout

    def test_forced_it_would_run_whatever_github_says(self, repo):
        _gh_answers(green(_sha(repo)))
        out = _hook(repo, mode="--preflight", PYRITE_PUSH_FORCE="1")
        assert out.stdout.startswith("preflight: would run: ")
        assert "PYRITE_PUSH_FORCE" in out.stdout and _gh_calls() == []

    @NEVER
    def test_it_never_stamps(self, repo):
        _gh_answers(CASES["the check failed"][0](_sha(repo)))
        _hook(repo, mode="--preflight")
        assert _stamps(repo) == []

    def test_it_agrees_with_the_run_for_every_case(self, repo):
        """One decision, two readers: whatever `--run` would do for a push,
        `--preflight` says."""
        sha = _sha(repo)
        for name, behaviour in [*((n, b) for n, (b, _s) in CASES.items()), *VERIFIED.items()]:
            _gh_answers(behaviour(sha))
            said = _hook(repo, mode="--preflight").stdout
            Path(os.environ["FAKE_LOG"]).unlink(missing_ok=True)
            _hook(repo)
            ran = bool(_pytest_calls())
            # A run that passed stamped the tree; the next case starts without it.
            shutil.rmtree(repo / ".git" / "pyrite-test-stamps", ignore_errors=True)
            assert said.startswith("preflight: would run: " if ran else "preflight: skip: "), name
