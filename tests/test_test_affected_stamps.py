"""The pass stamp in scripts/test-affected: each tree is tested once.

A worker ran `scripts/test-affected --run`, it passed, and the pre-push hook
then ran the same selection on the same commit again -- about 45 minutes
under load (maintainer, 2026-09-25). A pass on a clean tree now records a
stamp keyed on the tree SHA and the interpreter's major.minor, under the git
common dir so every worktree shares it; a later run whose selection the stamp
covers skips.

Every case builds a throwaway git repository in tmp_path. Its `.venv/bin/
python` is a shell script standing in for the interpreter: it answers the
version query, logs every pytest invocation and exits with ``FAKE_EXIT``, so
a case can pass, fail, or change Python without running a real suite. One
case at the end runs real pytest end to end.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "test-affected"

# A "never stamp" / "never skip" guard passes on a base that never stamps
# anything, by construction; what it pins is that the stamp does not
# over-reach. Each one fails when its guard alone is removed (mutation-checked
# when the stamp landed).
NEVER = pytest.mark.control(
    reason="never-stamp/never-skip guard: passes where nothing stamps; red when its guard is removed"
)

FAKE_PYTHON = """\
#!/bin/sh
# The version query: `python -c ...`.
if [ "$1" = "-c" ]; then echo "${FAKE_PY_VERSION:-3.12}"; exit 0; fi
echo "$*" >> "$FAKE_LOG"
# Something that happens while the suite runs (another session commits).
if [ -n "$FAKE_DURING" ]; then sh -c "$FAKE_DURING"; fi
exit "${FAKE_EXIT:-0}"
"""


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
    # One temp dir for HOME and Pyrite's config/data (a subprocess must never
    # read or write the real ~/.pyrite), no inherited push variables, and a
    # git identity that does not depend on the real HOME's config.
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "PYRITE_CONFIG_DIR", "PYRITE_DATA_DIR"):
        monkeypatch.setenv(var, str(home))
    for var in ("PRE_COMMIT_FROM_REF", "PRE_COMMIT_TO_REF", "PYRITE_PUSH_FULL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("PYRITE_PUSH_FORCE", raising=False)
    monkeypatch.delenv("PYRITE_PUSH_EXPERIMENTAL", raising=False)
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    monkeypatch.delenv("PYTEST_PLUGINS", raising=False)
    monkeypatch.delenv("FAKE_EXIT", raising=False)
    monkeypatch.delenv("FAKE_PY_VERSION", raising=False)
    monkeypatch.delenv("FAKE_DURING", raising=False)
    # The machine-wide lock (scripts/heavy_lock.py) lives under HOME, which
    # is tmp here: each case has its own, never the real one.
    # CI and the escape hatch skip the lock, and a held marker means "nested":
    # the lock cases must see the lock path on a laptop and in CI alike.
    for var in (
        "PYRITE_SUITE_LOCK",
        "PYRITE_SUITE_LOCK_SKIP",
        "PYRITE_SUITE_LOCK_TIMEOUT",
        "PYRITE_SUITE_LOCK_HELD",
        "CI",
        "GITHUB_ACTIONS",
    ):
        monkeypatch.delenv(var, raising=False)
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "Test")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "test@example.invalid")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "pytest-calls.log"))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    files = {
        ".gitignore": ".venv/\n",
        "pyrite/__init__.py": "",
        "pyrite/a.py": "A = 1\n",
        "pyrite/b.py": "def func():\n    return 1\n",
        "pyrite/c.py": "C = 1\n",
        "tests/__init__.py": "",
        "tests/test_a.py": "import pyrite.a\n\ndef test_a():\n    pass\n",
        "tests/test_b.py": "import pyrite.b\n\ndef test_b():\n    pass\n",
        "tests/test_c.py": "import pyrite.c\n\ndef test_c():\n    pass\n",
    }
    for rel, text in files.items():
        _write(root, rel, text)
    _git(root, "init", "-q", "-b", "dev")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "feature/x")
    (root / "pyrite" / "b.py").write_text("def func():\n    return 2\n")
    _git(root, "commit", "-qam", "change b")
    fake = root / ".venv" / "bin" / "python"
    fake.parent.mkdir(parents=True)
    fake.write_text(FAKE_PYTHON)
    fake.chmod(0o755)
    return root


def _run(repo: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(repo), "--run", "-n", "2", *args],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        timeout=120,
    )


def _calls() -> list[str]:
    log = Path(os.environ["FAKE_LOG"])
    return log.read_text().splitlines() if log.exists() else []


def _stamp_dir(repo: Path) -> Path:
    return repo / ".git" / "pyrite-test-stamps"


def _stamps(repo: Path) -> list[dict]:
    d = _stamp_dir(repo)
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))] if d.is_dir() else []


def _tree(repo: Path, rev: str = "HEAD") -> str:
    return _git(repo, "rev-parse", f"{rev}^{{tree}}").strip()


class TestTheRunTakesTheMachineLock:
    """`--run` waits for any other heavy run on the machine (#168)."""

    @pytest.fixture
    def held(self, tmp_path):
        import fcntl

        lock = tmp_path / "home" / ".cache" / "pyrite" / "heavy.lock"
        lock.parent.mkdir(parents=True)
        fd = os.open(lock, os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, json.dumps({"pid": 4242, "command": "another suite"}).encode())
        yield lock
        os.close(fd)

    def test_the_run_waits_for_the_holder(self, repo, held):
        out = _run(repo, PYRITE_SUITE_LOCK_TIMEOUT="0.5")
        assert out.returncode == 75, out.stderr
        assert "pid 4242" in out.stderr and "another suite" in out.stderr
        assert _calls() == []

    @pytest.mark.control(
        reason="a dry run never ran pytest; pins that the lock did not move earlier"
    )
    def test_a_dry_run_takes_no_lock(self, repo, held):
        out = _run(repo, "--dry-run", PYRITE_SUITE_LOCK_TIMEOUT="0.5")
        assert out.returncode == 0, out.stderr

    @pytest.mark.control(
        reason="a stamp skip never ran pytest; pins that the lock did not move earlier"
    )
    def test_a_stamped_tree_skips_without_waiting(self, repo, held):
        held_fd_free = _run(repo, PYRITE_SUITE_LOCK_SKIP="1")
        assert held_fd_free.returncode == 0
        out = _run(repo, PYRITE_SUITE_LOCK_TIMEOUT="0.5")
        assert out.returncode == 0, out.stderr
        assert "skipping" in out.stderr
        assert len(_calls()) == 1

    def test_pytest_runs_marked_as_the_holder(self, repo, tmp_path):
        seen = tmp_path / "held.txt"
        out = _run(repo, FAKE_DURING=f'echo "$PYRITE_SUITE_LOCK_HELD" > {seen}')
        assert out.returncode == 0, out.stderr
        lock = (tmp_path / "home" / ".cache" / "pyrite" / "heavy.lock").resolve()
        assert seen.read_text().strip() == str(lock)


PY_FAKE_PYTHON = """\
#!{python}
# The worktree's interpreter, as a pytest that takes a moment to stop on
# SIGTERM and otherwise runs until FAKE_RELEASE exists.
import os, signal, sys, time
if sys.argv[1:2] == ["-c"]:
    print("3.12")
    sys.exit(0)
log = os.environ["FAKE_EVENTS"]
def note(event):
    with open(log, "a") as f:
        f.write(f"{{event}} {{os.getpid()}} {{time.time():.6f}}\\n")
def on_term(signum, frame):
    note("term")
    time.sleep(0.5)
    note("end")
    sys.exit(0)
signal.signal(signal.SIGTERM, on_term)
note("start")
while not os.path.exists(os.environ["FAKE_RELEASE"]):
    time.sleep(0.02)
note("end")
"""


class TestStoppingTestAffected:
    """While test-affected's pytest still runs, no other heavy run starts --
    however test-affected itself is stopped (#905 round 1)."""

    DEADLINE = 60.0

    @pytest.fixture
    def setup(self, repo, tmp_path):
        python = repo / ".venv" / "bin" / "python"
        python.write_text(PY_FAKE_PYTHON.format(python=sys.executable))
        python.chmod(0o755)
        events = tmp_path / "events.log"
        release = tmp_path / "release"
        lock = tmp_path / "home" / ".cache" / "pyrite" / "heavy.lock"
        env = {**os.environ, "FAKE_EVENTS": str(events), "FAKE_RELEASE": str(release)}
        for var in ("CI", "GITHUB_ACTIONS"):
            env.pop(var, None)
        holder = subprocess.Popen(
            [sys.executable, str(SCRIPT), "--root", str(repo), "--run", "-n", "2", "--force"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        end = time.monotonic() + self.DEADLINE
        while not (events.exists() and "start" in events.read_text()):
            assert holder.poll() is None, holder.communicate(timeout=60)
            assert time.monotonic() < end, "pytest never started"
            time.sleep(0.02)
        waiter_log = tmp_path / "waiter.log"
        waiter = subprocess.Popen(
            [
                sys.executable,
                str(SCRIPT.parent / "heavy_lock.py"),
                "--",
                sys.executable,
                "-c",
                f"import time; open({str(waiter_log)!r}, 'w').write(str(time.time()))",
            ],
            env={**env, "PYRITE_SUITE_LOCK_TIMEOUT": "30"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        queue = Path(str(lock) + ".queue")
        while not list(queue.glob("*.ticket")):
            assert waiter.poll() is None, waiter.communicate(timeout=60)
            assert time.monotonic() < end, "the waiter never queued"
            time.sleep(0.02)
        yield holder, waiter, events, release, waiter_log
        release.touch()
        for proc in (holder, waiter):
            if proc.poll() is None:
                proc.kill()

    @staticmethod
    def _events(path):
        out = {}
        for line in path.read_text().splitlines():
            event, _pid, t = line.split()
            out[event] = float(t)
        return out

    def test_sigterm_reaches_pytest_and_the_lock_waits_for_it(self, setup):
        holder, waiter, events, release, waiter_log = setup
        holder.send_signal(signal.SIGTERM)
        holder.communicate(timeout=self.DEADLINE)
        waiter.communicate(timeout=self.DEADLINE)
        seen = self._events(events)
        assert "term" in seen, "SIGTERM was not passed on to pytest"
        assert float(waiter_log.read_text()) >= seen["end"]

    def test_sigkill_the_next_run_waits_for_the_orphaned_pytest(self, setup):
        holder, waiter, events, release, waiter_log = setup
        holder.send_signal(signal.SIGKILL)
        holder.wait(timeout=60)
        time.sleep(1.5)
        assert not waiter_log.exists(), "a second run started beside the orphaned pytest"
        release.touch()
        _out, err = waiter.communicate(timeout=self.DEADLINE)
        assert "holder was killed" in err
        assert float(waiter_log.read_text()) >= self._events(events)["end"]


HIDE_FCNTL = """\
import runpy, sys
class Hide:
    def find_spec(self, name, path=None, target=None):
        if name in ("fcntl", "pwd"):
            raise ImportError(f"No module named {name!r} (hidden)")
sys.meta_path.insert(0, Hide())
for name in ("fcntl", "pwd"):
    sys.modules.pop(name, None)
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


class TestWithoutFcntl:
    """Windows has no fcntl or pwd: test-affected still lists, dry-runs and
    runs, without the lock, saying so once."""

    def _run(self, repo, *args):
        return subprocess.run(
            [sys.executable, "-c", HIDE_FCNTL, str(SCRIPT), "--root", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=120,
        )

    @pytest.mark.control(
        reason="dev never imported fcntl; red when heavy_lock imports it unguarded"
    )
    def test_dry_run(self, repo):
        out = self._run(repo, "--run", "--dry-run")
        assert out.returncode == 0, out.stderr
        assert "Traceback" not in out.stderr

    def test_run_goes_ahead_without_the_lock(self, repo):
        out = self._run(repo, "--run", "-n", "2", "--force")
        assert out.returncode == 0, out.stderr
        assert "no fcntl" in out.stderr
        assert len(_calls()) == 1


class TestStampWritten:
    def test_a_pass_on_a_clean_tree_is_stamped(self, repo):
        out = _run(repo)
        assert out.returncode == 0, out.stderr
        assert len(_calls()) == 1
        (stamp,) = _stamps(repo)
        assert stamp["tree"] == _tree(repo)
        assert stamp["python"] == "3.12"
        assert "tests/test_b.py" in stamp["selection"]

    def test_the_second_run_on_the_same_tree_skips(self, repo):
        assert _run(repo).returncode == 0
        out = _run(repo)
        assert out.returncode == 0, out.stderr
        assert len(_calls()) == 1, "the stamped selection ran again"
        said = out.stdout + out.stderr
        assert f"already passed on tree {_tree(repo)[:7]}" in said
        assert "1 tests selected" in said and "skipping" in said

    def test_full_is_stamped_as_full(self, repo):
        assert _run(repo, "--full").returncode == 0
        (stamp,) = _stamps(repo)
        assert stamp["selection"] == "full"

    def test_the_stamp_lives_in_the_common_dir_shared_by_worktrees(self, repo, tmp_path):
        assert _run(repo).returncode == 0
        other = tmp_path / "wt"
        _git(repo, "worktree", "add", "-q", str(other), "HEAD")
        fake = other / ".venv" / "bin" / "python"
        fake.parent.mkdir(parents=True)
        fake.write_text(FAKE_PYTHON)
        fake.chmod(0o755)
        out = _run(other, "--base", "dev")
        assert out.returncode == 0, out.stderr
        assert len(_calls()) == 1, "a worktree of the same tree ran it again"


class TestNeverStamped:
    @NEVER
    def test_a_failure_is_not_stamped(self, repo):
        out = _run(repo, FAKE_EXIT="1")
        assert out.returncode == 1
        assert _stamps(repo) == []
        assert _run(repo).returncode == 0
        assert len(_calls()) == 2, "a failed selection was treated as passed"

    @NEVER
    def test_a_tree_with_uncommitted_tracked_changes_is_not_stamped(self, repo):
        (repo / "pyrite" / "c.py").write_text("C = 2\n")
        assert _run(repo).returncode == 0
        assert _stamps(repo) == []

    @NEVER
    def test_an_untracked_python_file_makes_the_tree_dirty(self, repo):
        # An untracked conftest.py or module changes what ran without
        # changing HEAD's tree.
        _write(repo, "tests/conftest.py", "")
        assert _run(repo).returncode == 0
        assert _stamps(repo) == []

    @NEVER
    @pytest.mark.parametrize("var", ["PYTEST_ADDOPTS", "PYTEST_PLUGINS"])
    def test_pytest_options_from_the_environment_are_not_stamped(self, repo, var):
        # PYTEST_ADDOPTS="-k ..." narrows the run without a single argument
        # on the command line (cold read, #459).
        assert _run(repo, **{var: "-k b"}).returncode == 0
        assert _stamps(repo) == []

    @NEVER
    @pytest.mark.parametrize(
        "rel",
        [
            "tests/data.json",
            "pyrite/table.csv",
            "kb/entry.md",
            "scripts/tool",
            "extensions/x/y.yaml",
        ],
    )
    def test_an_untracked_file_under_a_code_root_makes_the_tree_dirty(self, repo, rel):
        # A test that reads an untracked data file passes; delete the file and
        # the same tree fails -- so that pass says nothing about the tree.
        _write(repo, rel, "{}")
        assert _run(repo).returncode == 0
        assert _stamps(repo) == []

    @NEVER
    def test_an_ignored_file_under_a_code_root_makes_the_tree_dirty(self, repo):
        (repo / ".gitignore").write_text(".venv/\n*.db\n")
        _git(repo, "commit", "-qam", "ignore dbs")
        _write(repo, "tests/fixture.db", "x")
        assert _run(repo).returncode == 0
        assert _stamps(repo) == []

    def test_a_file_the_run_leaves_behind_is_named_not_called_uncommitted(self, repo):
        # #852: a passing run left a gitignored fixture under a code root; git
        # status stayed clean, and the message said "uncommitted changes".
        (repo / ".gitignore").write_text(".venv/\n*.db\n")
        _git(repo, "commit", "-qam", "ignore dbs")
        assert _git(repo, "status", "--porcelain").strip() == ""
        out = _run(repo, FAKE_DURING="echo x > tests/leftover.db")
        assert out.returncode == 0, out.stderr
        assert _stamps(repo) == []
        assert _git(repo, "status", "--porcelain").strip() == "", "git still sees a clean tree"
        assert "uncommitted" not in out.stderr
        assert "the run left the tree unclean" in out.stderr
        assert "gitignored file under a code root: tests/leftover.db" in out.stderr

    def test_a_tree_unclean_before_the_run_says_so_and_names_the_file(self, repo):
        _write(repo, "tests/data.json", "{}")
        out = _run(repo)
        assert "not clean before the run: untracked file under a code root: tests/data.json" in (
            out.stderr
        )

    def test_build_noise_under_a_code_root_does_not_make_the_tree_dirty(self, repo):
        (repo / ".gitignore").write_text(".venv/\n__pycache__/\n*.egg-info/\n.pytest_cache/\n")
        _git(repo, "commit", "-qam", "ignore build noise")
        _write(repo, "tests/__pycache__/test_b.cpython-312.pyc", "x")
        _write(repo, "pyrite/__pycache__/b.cpython-312.pyc", "x")
        _write(repo, "pyrite.egg-info/PKG-INFO", "x")
        _write(repo, "tests/.pytest_cache/v/cache/lastfailed", "{}")
        assert _run(repo).returncode == 0
        assert len(_stamps(repo)) == 1

    def test_an_untracked_file_outside_the_code_roots_does_not_dirty_the_tree(self, repo):
        _write(repo, "notes.txt", "scratch")
        assert _run(repo).returncode == 0
        assert len(_stamps(repo)) == 1

    @NEVER
    def test_a_dirty_tree_does_not_skip_on_heads_stamp(self, repo):
        # The edit is what is being tested; HEAD's pass says nothing about it.
        assert _run(repo).returncode == 0
        (repo / "pyrite" / "b.py").write_text("def func():\n    return 3\n")
        assert _run(repo).returncode == 0
        assert len(_calls()) == 2

    @NEVER
    def test_a_commit_during_the_run_is_not_stamped(self, repo):
        # The tree HEAD names afterwards is not the tree that was tested.
        during = "echo 'C = 9' > pyrite/c.py && git commit -qam during"
        assert _run(repo, FAKE_DURING=during).returncode == 0
        assert _git(repo, "log", "-1", "--format=%s").strip() == "during"
        assert _stamps(repo) == []

    @NEVER
    def test_a_last_failed_run_is_not_stamped(self, repo):
        out = _run(repo, "--", "--lf")
        assert out.returncode == 0, out.stderr
        assert "--lf" in _calls()[0].split(), "--lf did not reach pytest"
        assert _stamps(repo) == []

    @NEVER
    @pytest.mark.parametrize("narrowing", [["-k", "b"], ["--last-failed"], ["--deselect", "x"]])
    def test_pytest_args_that_narrow_the_run_are_not_stamped(self, repo, narrowing):
        assert _run(repo, "--", *narrowing).returncode == 0
        assert _stamps(repo) == []

    @pytest.mark.control(reason="output flags run the whole selection, so they still stamp")
    def test_output_flags_still_stamp(self, repo):
        assert _run(repo, "--", "-v", "-x", "--tb=long").returncode == 0
        assert len(_stamps(repo)) == 1

    @NEVER
    def test_a_dry_run_is_not_stamped(self, repo):
        assert _run(repo, "--dry-run").returncode == 0
        assert _stamps(repo) == []


class TestCoverage:
    def test_a_superset_covers_a_subset(self, repo):
        assert _run(repo, "--files", "pyrite/a.py", "pyrite/b.py").returncode == 0
        out = _run(repo, "--files", "pyrite/b.py")
        assert out.returncode == 0, out.stderr
        assert len(_calls()) == 1

    @NEVER
    def test_a_subset_does_not_cover_a_superset(self, repo):
        assert _run(repo, "--files", "pyrite/b.py").returncode == 0
        assert _run(repo, "--files", "pyrite/a.py", "pyrite/b.py").returncode == 0
        assert len(_calls()) == 2

    @NEVER
    def test_a_core_only_pass_does_not_cover_an_experimental_run(self, repo):
        """#657: the default run deselects experimental tests, so its pass
        says nothing about them."""
        assert _run(repo, "--full").returncode == 0
        assert _run(repo, "--full", PYRITE_PUSH_EXPERIMENTAL="1").returncode == 0
        assert len(_calls()) == 2

    def test_an_experimental_pass_does_not_vouch_for_the_core_run_either(self, repo):
        # Kept separate by key: a re-run costs time, a wrong skip costs a gate.
        assert _run(repo, "--full", "--experimental").returncode == 0
        assert _run(repo, "--full").returncode == 0
        assert len(_calls()) == 2

    def test_full_covers_any_selection(self, repo):
        assert _run(repo, "--full").returncode == 0
        assert _run(repo, "--files", "pyrite/a.py", "pyrite/c.py").returncode == 0
        assert len(_calls()) == 1

    @NEVER
    def test_a_selection_does_not_cover_full(self, repo):
        assert _run(repo, "--files", "pyrite/a.py", "pyrite/b.py", "pyrite/c.py").returncode == 0
        assert _run(repo, "--full").returncode == 0
        assert len(_calls()) == 2

    def test_two_passes_on_one_tree_cover_their_union(self, repo):
        assert _run(repo, "--files", "pyrite/a.py").returncode == 0
        assert _run(repo, "--files", "pyrite/b.py").returncode == 0
        assert _run(repo, "--files", "pyrite/a.py", "pyrite/b.py").returncode == 0
        assert len(_calls()) == 2

    @NEVER
    def test_a_different_tree_does_not_match(self, repo):
        assert _run(repo).returncode == 0
        (repo / "pyrite" / "b.py").write_text("def func():\n    return 3\n")
        _git(repo, "commit", "-qam", "change b again")
        assert _run(repo).returncode == 0
        assert len(_calls()) == 2

    @NEVER
    def test_a_different_python_does_not_match(self, repo):
        assert _run(repo).returncode == 0
        assert _run(repo, FAKE_PY_VERSION="3.13").returncode == 0
        assert len(_calls()) == 2


class TestCoversRule:
    """`covers` directly: the one rule the CLI cases reach only through data."""

    @pytest.fixture(autouse=True)
    def _module(self):
        import importlib.machinery
        import importlib.util

        name = "test_affected_stamps_mod"
        loader = importlib.machinery.SourceFileLoader(name, str(SCRIPT))
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
        sys.modules[name] = module  # dataclasses look their module up here
        loader.exec_module(module)
        self.ta = module

    def test_no_list_covers_full(self):
        # Not even one whose items happen to spell it.
        assert not self.ta.covers(["f", "u", "l"], "full")

    def test_a_file_covers_its_node_ids(self):
        assert self.ta.covers(["tests/test_a.py"], ["tests/test_a.py::TestX::test_y"])
        assert not self.ta.covers(["tests/test_a.py::TestX"], ["tests/test_a.py"])


class TestBypass:
    def test_force_ignores_the_stamp(self, repo):
        assert _run(repo).returncode == 0
        assert _run(repo, "--force").returncode == 0
        assert len(_calls()) == 2

    @NEVER
    def test_the_force_variable_ignores_the_stamp(self, repo):
        assert _run(repo).returncode == 0
        assert _run(repo, PYRITE_PUSH_FORCE="1").returncode == 0
        assert len(_calls()) == 2


class TestUntrustedStamps:
    def _stamp_then(self, repo, text: str) -> None:
        assert _run(repo).returncode == 0
        (path,) = _stamp_dir(repo).glob("*.json")
        path.write_text(text)

    @pytest.mark.parametrize(
        "text",
        [
            "not json {",
            "[]",
            '{"version": 1}',
            # Each of these is a valid stamp but for one field.
            '{"version": 2, "tree": "TREE", "python": "3.12", "selection": "full", "when": NOW}',
            '{"version": 1, "tree": "TREE", "python": "3.12", "selection": 7, "when": NOW}',
            '{"version": 1, "tree": "TREE", "python": "3.12", "selection": [7], "when": NOW}',
            '{"version": 1, "tree": "TREE", "python": "3.12", "selection": "full", "when": "x"}',
        ],
    )
    def test_an_unparseable_stamp_is_ignored(self, repo, text):
        self._stamp_then(repo, text.replace("TREE", _tree(repo)).replace("NOW", str(time.time())))
        out = _run(repo)
        assert out.returncode == 0, out.stderr
        assert "Traceback" not in out.stderr
        assert len(_calls()) == 2

    def test_a_stamp_for_another_tree_under_this_name_is_ignored(self, repo):
        assert _run(repo).returncode == 0
        (path,) = _stamp_dir(repo).glob("*.json")
        stamp = json.loads(path.read_text())
        stamp["tree"] = "0" * 40
        path.write_text(json.dumps(stamp))
        assert _run(repo).returncode == 0
        assert len(_calls()) == 2

    def test_a_stamp_for_another_python_under_this_name_is_ignored(self, repo):
        assert _run(repo).returncode == 0
        (path,) = _stamp_dir(repo).glob("*.json")
        path.rename(path.with_name(path.name.replace("py3.12", "py3.13")))
        assert _run(repo, FAKE_PY_VERSION="3.13").returncode == 0
        assert len(_calls()) == 2

    def test_a_stamp_older_than_fourteen_days_is_ignored(self, repo):
        assert _run(repo).returncode == 0
        (path,) = _stamp_dir(repo).glob("*.json")
        stamp = json.loads(path.read_text())
        stamp["when"] = time.time() - 15 * 86400
        path.write_text(json.dumps(stamp))
        assert _run(repo).returncode == 0
        assert len(_calls()) == 2

    def test_old_stamps_are_pruned_when_a_new_one_is_written(self, repo):
        d = _stamp_dir(repo)
        d.mkdir()
        old = d / f"{'1' * 40}-py3.12.json"
        old.write_text("{}")
        stale = time.time() - 15 * 86400
        os.utime(old, (stale, stale))
        assert _run(repo).returncode == 0
        assert not old.exists()


class TestPrePush:
    """Under the hook, the stamp looked up is the PUSHED ref's tree."""

    def test_the_pushed_refs_tree_is_looked_up_not_the_working_trees(self, repo):
        assert _run(repo).returncode == 0
        pushed = _git(repo, "rev-parse", "HEAD").strip()
        base = _git(repo, "rev-parse", "dev").strip()
        # The working tree moves on: a new commit that no stamp covers.
        (repo / "pyrite" / "b.py").write_text("def func():\n    return 3\n")
        _git(repo, "commit", "-qam", "later")
        out = _run(repo, PRE_COMMIT_FROM_REF=base, PRE_COMMIT_TO_REF=pushed)
        assert out.returncode == 0, out.stderr
        assert len(_calls()) == 1, "the pushed tree passed already; the hook ran it again"

    @NEVER
    def test_a_pushed_tree_with_no_stamp_runs(self, repo):
        assert _run(repo).returncode == 0
        base = _git(repo, "rev-parse", "dev").strip()
        (repo / "pyrite" / "b.py").write_text("def func():\n    return 3\n")
        _git(repo, "commit", "-qam", "later")
        head = _git(repo, "rev-parse", "HEAD").strip()
        _git(repo, "checkout", "-q", "HEAD~1")  # the working tree is the stamped one
        out = _run(repo, PRE_COMMIT_FROM_REF=base, PRE_COMMIT_TO_REF=head)
        assert out.returncode == 0, out.stderr
        assert len(_calls()) == 2, "a stamp on the working tree skipped an untested push"


def _real_venv(repo: Path) -> None:
    """A `.venv/bin/python` that is the real interpreter running this suite."""
    python = repo / ".venv" / "bin" / "python"
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)


class TestRealPytest:
    @pytest.fixture
    def plain(self, repo):
        _write(repo, "tests/test_plain.py", "def test_plain():\n    assert True\n")
        _git(repo, "add", "tests/test_plain.py")
        _git(repo, "commit", "-qm", "plain")
        return repo

    def test_a_real_pass_is_stamped_and_the_rerun_skips(self, plain):
        _real_venv(plain)
        first = _run(plain, "--files", "tests/test_plain.py", "-n", "1")
        assert first.returncode == 0, first.stdout + first.stderr
        assert "1 passed" in first.stdout
        (stamp,) = _stamps(plain)
        assert stamp["python"] == f"{sys.version_info[0]}.{sys.version_info[1]}"
        second = _run(plain, "--files", "tests/test_plain.py", "-n", "1")
        assert second.returncode == 0
        assert "passed" not in second.stdout
        assert "skipping" in second.stdout + second.stderr

    @NEVER
    def test_without_the_worktrees_venv_nothing_is_stamped(self, plain):
        # The fallback interpreter is another checkout's venv, whose editable
        # installs point at THAT checkout's code (#210): its pass is about
        # other code, and must not land in the shared stamp store.
        (plain / ".venv" / "bin" / "python").unlink()
        out = _run(plain, "--files", "tests/test_plain.py", "-n", "1")
        assert out.returncode == 0, out.stdout + out.stderr
        assert "1 passed" in out.stdout
        assert _stamps(plain) == []
        assert "no .venv" in out.stderr
