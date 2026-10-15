"""The machine-wide heavy-run lock (scripts/heavy_lock.py, #168).

On 2026-09-18 the conductor loop ran a 16 GB laptop out of memory: up to
eight `-n auto` suites at once from several worktrees, plus Playwright.
Nothing serialised them. Now every heavy runner -- `scripts/test-affected
--run` (the pre-push hook included), `scripts/verify-red.sh` and
`scripts/e2e.sh` -- takes one lock per user per machine, and a second one
waits its turn instead of stacking.

Every case uses real processes and a fake command, never a real suite, and a
temporary lock path (``PYRITE_SUITE_LOCK``): this file runs inside a suite
that may hold the real lock, and a case that reached it would wait on its own
parent. ``CI``/``GITHUB_ACTIONS`` are stripped from the children, or every
case would take the CI bypass and prove nothing.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LOCK = REPO / "scripts" / "heavy_lock.py"
E2E = REPO / "scripts" / "e2e.sh"
VERIFY_RED = REPO / "scripts" / "verify-red.sh"
MACHINE_OK = REPO / "scripts" / "machine_ok.sh"

# The scripts are run as processes; the two cases that read a function
# import it themselves, so every other case is red on its behaviour, not on a
# missing import, when the scripts are absent.
EXIT_TIMEOUT = 75  # EXIT_TIMEOUT, pinned: callers read it


def _script_module(name: str):
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        return __import__(name)
    finally:
        sys.path.remove(str(REPO / "scripts"))


# One deadline for a whole group of processes, generous: under the pre-push
# suite at -n 4 a fresh interpreter can take seconds to start
# (tests/test_task_claim_concurrency.py has the history).
GROUP_DEADLINE = 60.0

# What the fake command does: append "<event> <name> <time>" to a log, and
# optionally block until a release file exists.
FAKE = """\
import os, signal, sys, time
log, name = sys.argv[1], sys.argv[2]
def note(event):
    with open(log, "a") as f:
        f.write(f"{event} {name} {time.time():.6f}\\n")
def on_term(signum, frame):
    # A command that takes a moment to stop, as pytest does on SIGTERM: the
    # lock must still be held until it has.
    note("term")
    time.sleep(float(os.environ.get("FAKE_TERM_GRACE", "0.5")))
    note("end")
    sys.exit(0)
signal.signal(signal.SIGTERM, on_term)
note("start")
release = os.environ.get("FAKE_RELEASE")
if release:
    while not os.path.exists(release):
        time.sleep(0.02)
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
note("end")
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""


def _clean_env(lock: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYRITE_SUITE_LOCK")}
    for var in ("CI", "GITHUB_ACTIONS"):
        env.pop(var, None)
    env["PYRITE_SUITE_LOCK"] = str(lock)
    env.update(extra)
    return env


@pytest.fixture
def lock(tmp_path: Path) -> Path:
    return tmp_path / "locks" / "heavy.lock"


@pytest.fixture
def fake(tmp_path: Path) -> Path:
    path = tmp_path / "fake_cmd.py"
    path.write_text(FAKE)
    return path


@pytest.fixture
def log(tmp_path: Path) -> Path:
    return tmp_path / "events.log"


def _events(log: Path) -> list[tuple[str, str, float]]:
    if not log.exists():
        return []
    out = []
    for line in log.read_text().splitlines():
        event, name, t = line.split()
        out.append((event, name, float(t)))
    return out


def _spawn(lock: Path, fake: Path, log: Path, name: str, **env: str) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            str(LOCK),
            "--label",
            name,
            "--",
            sys.executable,
            str(fake),
            str(log),
            name,
        ],
        env=_clean_env(lock, **env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for(predicate, what: str, deadline: float, *watched: subprocess.Popen) -> None:
    """Poll until ``predicate`` holds. A watched process that has already
    exited can never make it true: fail at once with its stderr, rather than
    spin to the deadline (without the lock script every spawn exits at once)."""
    while not predicate():
        for proc in watched:
            if proc.poll() is not None:
                err = proc.stderr.read() if proc.stderr else ""
                raise AssertionError(f"{what}: pid {proc.pid} exited {proc.returncode}: {err}")
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.02)


def _lock_record(lock: Path) -> dict:
    try:
        return json.loads(lock.read_text() or "{}")
    except (FileNotFoundError, ValueError):
        return {}


def _started(log: Path, name: str) -> bool:
    return any(e == "start" and n == name for e, n, _ in _events(log))


def _finish(procs: list[subprocess.Popen], deadline: float) -> list[tuple[int, str, str]]:
    results = []
    for p in procs:
        try:
            out, err = p.communicate(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            for q in procs:
                q.kill()
            raise AssertionError(f"pid {p.pid} still running at the group deadline") from None
        results.append((p.returncode, out, err))
    return results


@pytest.fixture
def holder(lock, fake, log, tmp_path):
    """A process holding the lock until the test creates the release file."""
    release = tmp_path / "release"
    proc = _spawn(lock, fake, log, "holder", FAKE_RELEASE=str(release))
    deadline = time.monotonic() + GROUP_DEADLINE
    _wait_for(lambda: _started(log, "holder"), "the holder to start", deadline, proc)
    yield proc, release
    release.touch()
    try:
        proc.communicate(timeout=GROUP_DEADLINE)
    except subprocess.TimeoutExpired:
        proc.kill()


class TestOneAtATime:
    def test_concurrent_callers_never_overlap(self, lock, fake, log, tmp_path):
        """Three callers released by one barrier: their commands run one
        after another, and every one runs."""
        go = tmp_path / "go"
        starter = (
            "import os, sys, time\n"
            f"while not os.path.exists({str(go)!r}): time.sleep(0.005)\n"
            "os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n"
        )
        names = ["a", "b", "c"]
        procs = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    starter,
                    str(LOCK),
                    "--",
                    sys.executable,
                    str(fake),
                    str(log),
                    n,
                ],
                env=_clean_env(lock, FAKE_SLEEP="0.3"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for n in names
        ]
        go.touch()
        results = _finish(procs, time.monotonic() + GROUP_DEADLINE)
        assert [r[0] for r in results] == [0, 0, 0], results
        spans = {}
        for event, name, t in _events(log):
            spans.setdefault(name, {})[event] = t
        assert sorted(spans) == names
        ordered = sorted(spans.values(), key=lambda s: s["start"])
        for before, after in zip(ordered, ordered[1:], strict=False):
            assert before["end"] <= after["start"], f"overlapping runs: {ordered}"

    def test_waiters_start_in_the_order_they_arrived(self, lock, fake, log, holder):
        proc, release = holder
        queue = Path(str(lock) + ".queue")
        deadline = time.monotonic() + GROUP_DEADLINE
        waiters = []
        for i, name in enumerate(["first", "second", "third"], start=1):
            waiters.append(_spawn(lock, fake, log, name))
            # Each one is in the queue before the next arrives.
            _wait_for(
                lambda i=i: queue.is_dir() and len(list(queue.glob("*.ticket"))) == i,
                f"waiter {name} to queue",
                deadline,
                waiters[-1],
            )
        release.touch()
        results = _finish(waiters, deadline)
        assert [r[0] for r in results] == [0, 0, 0], results
        starts = [n for e, n, _ in _events(log) if e == "start" and n != "holder"]
        assert starts == ["first", "second", "third"]
        assert list(queue.glob("*.ticket")) == []

    def test_a_newcomer_does_not_jump_an_older_live_waiter(self, lock, fake, log):
        """The lock is free, but someone arrived first and is still waiting
        (its ticket is held): a newcomer does not take the lock past it."""
        import fcntl

        queue = Path(str(lock) + ".queue")
        queue.mkdir(parents=True)
        older = queue / f"{1:020d}-1.ticket"
        fd = os.open(older, os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            out = subprocess.run(
                [sys.executable, str(LOCK), "--", sys.executable, str(fake), str(log), "new"],
                env=_clean_env(lock, PYRITE_SUITE_LOCK_TIMEOUT="0.5"),
                capture_output=True,
                text=True,
                timeout=GROUP_DEADLINE,
            )
        finally:
            os.close(fd)
        assert out.returncode == EXIT_TIMEOUT, out.stderr
        assert "1 more ahead" in out.stderr
        assert not _started(log, "new")
        assert older.exists()  # a live waiter's ticket is never removed

    def test_the_waiter_says_who_it_is_waiting_for(self, lock, fake, log, holder):
        proc, _release = holder
        out = subprocess.run(
            [sys.executable, str(LOCK), "--", sys.executable, str(fake), str(log), "late"],
            env=_clean_env(lock, PYRITE_SUITE_LOCK_TIMEOUT="0.5"),
            capture_output=True,
            text=True,
            timeout=GROUP_DEADLINE,
        )
        assert f"pid {proc.pid}" in out.stderr
        assert "holder" in out.stderr  # the holder's label / command
        assert "since" in out.stderr
        assert str(lock) in out.stderr


class TestNeverWedged:
    def _killed_holder(self, lock, fake, log, tmp_path):
        """A holder SIGKILLed while its command runs, and a waiter queued
        behind it. Returns (waiter, release file, the command's pid)."""
        release = tmp_path / "release-orphan"
        holder = _spawn(lock, fake, log, "holder", FAKE_RELEASE=str(release))
        deadline = time.monotonic() + GROUP_DEADLINE
        _wait_for(lambda: _started(log, "holder"), "the holder to start", deadline, holder)
        _wait_for(lambda: "child" in _lock_record(lock), "the child pid", deadline)
        child = json.loads(lock.read_text())["child"]
        waiter = _spawn(lock, fake, log, "waiter", PYRITE_SUITE_LOCK_TIMEOUT="30")
        _wait_for(
            lambda: len(list(Path(str(lock) + ".queue").glob("*.ticket"))) == 1,
            "the waiter to queue",
            deadline,
            waiter,
        )
        holder.send_signal(signal.SIGKILL)
        holder.wait(timeout=60)
        return waiter, release, child

    def test_after_sigkill_the_next_run_waits_for_the_orphaned_command(
        self, lock, fake, log, tmp_path
    ):
        """SIGKILL gives the holder no chance to stop its command. The kernel
        frees the flock; the next run takes it and waits for that command."""
        waiter, release, child = self._killed_holder(lock, fake, log, tmp_path)
        try:
            time.sleep(1.5)  # many poll intervals: a waiter that would start, has
            assert not _started(log, "waiter"), "started beside the orphaned command"
            release.touch()
            ((code, _out, err),) = _finish([waiter], time.monotonic() + GROUP_DEADLINE)
            assert code == 0, err
            assert f"pid {child}" in err and "holder was killed" in err
            ends = {n: t for e, n, t in _events(log) if e == "end"}
            starts = {n: t for e, n, t in _events(log) if e == "start"}
            assert starts["waiter"] >= ends["holder"]
        finally:
            release.touch()

    def test_a_killed_holder_and_its_command_never_wedge_the_lock(self, lock, fake, log, tmp_path):
        """Nothing left running: the next run starts at once. No stale lock,
        nothing to delete by hand."""
        waiter, release, child = self._killed_holder(lock, fake, log, tmp_path)
        try:
            killed_at = time.time()
            os.kill(child, signal.SIGKILL)
            ((code, _out, err),) = _finish([waiter], time.monotonic() + GROUP_DEADLINE)
            assert code == 0, err
            start = next(t for e, n, t in _events(log) if e == "start" and n == "waiter")
            assert start - killed_at < 10, start - killed_at
        finally:
            release.touch()

    def test_the_orphan_wait_is_bounded(self, lock, fake, log, tmp_path):
        release = tmp_path / "release-orphan"
        holder = _spawn(lock, fake, log, "holder", FAKE_RELEASE=str(release))
        deadline = time.monotonic() + GROUP_DEADLINE
        _wait_for(
            lambda: "child" in _lock_record(lock),
            "the child pid",
            deadline,
            holder,
        )
        holder.send_signal(signal.SIGKILL)
        holder.wait(timeout=60)
        try:
            out = subprocess.run(
                [sys.executable, str(LOCK), "--", sys.executable, str(fake), str(log), "late"],
                env=_clean_env(lock, PYRITE_SUITE_LOCK_TIMEOUT="0.5"),
                capture_output=True,
                text=True,
                timeout=GROUP_DEADLINE,
            )
            assert out.returncode == EXIT_TIMEOUT, out.stderr
            assert "holder was killed" in out.stderr
            assert not _started(log, "late")
        finally:
            release.touch()

    def test_a_dead_waiter_does_not_hold_up_the_line(self, lock, fake, log, holder):
        proc, release = holder
        deadline = time.monotonic() + GROUP_DEADLINE
        queue = Path(str(lock) + ".queue")
        dead = _spawn(lock, fake, log, "dead")
        _wait_for(lambda: len(list(queue.glob("*.ticket"))) == 1, "dead to queue", deadline, dead)
        alive = _spawn(lock, fake, log, "alive")
        _wait_for(lambda: len(list(queue.glob("*.ticket"))) == 2, "alive to queue", deadline, alive)
        dead.send_signal(signal.SIGKILL)
        dead.wait(timeout=60)
        release.touch()
        ((code, _o, err),) = _finish([alive], deadline)
        assert code == 0, err
        assert list(queue.glob("*.ticket")) == []

    def test_a_fresh_machine_gets_the_lock_directory(self, tmp_path, fake, log):
        lock = tmp_path / "no" / "such" / "dir" / "heavy.lock"
        out = subprocess.run(
            [sys.executable, str(LOCK), "--", sys.executable, str(fake), str(log), "x"],
            env=_clean_env(lock),
            capture_output=True,
            text=True,
            timeout=GROUP_DEADLINE,
        )
        assert out.returncode == 0, out.stderr
        assert lock.exists()


class TestStoppingTheHolder:
    """While a heavy run's command still runs, no other heavy run starts --
    however the holder is stopped."""

    def _entry(self, how: str, fake: Path, log: Path, tmp_path: Path) -> tuple[list[str], dict]:
        if how == "heavy_lock.py":
            return [
                sys.executable,
                str(LOCK),
                "--",
                sys.executable,
                str(fake),
                str(log),
                "holder",
            ], {}
        # scripts/e2e.sh execs into heavy_lock.py; its command is npx.
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        npx = bin_dir / "npx"
        npx.write_text(
            f"#!{sys.executable}\nimport runpy, sys\nsys.argv = [{str(fake)!r}, {str(log)!r}, 'holder']\nrunpy.run_path(sys.argv[0], run_name='__main__')\n"
        )
        npx.chmod(0o755)
        return ["bash", str(E2E)], {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

    @pytest.mark.parametrize("how", ["heavy_lock.py", "scripts/e2e.sh"])
    def test_sigterm_reaches_the_command_and_the_lock_waits_for_it(
        self, lock, fake, log, tmp_path, how
    ):
        release = tmp_path / "never"
        argv, extra = self._entry(how, fake, log, tmp_path)
        holder = subprocess.Popen(
            argv,
            env=_clean_env(lock, FAKE_RELEASE=str(release), **extra),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + GROUP_DEADLINE
        try:
            _wait_for(lambda: _started(log, "holder"), "the holder to start", deadline, holder)
            waiter = _spawn(lock, fake, log, "waiter")
            _wait_for(
                lambda: len(list(Path(str(lock) + ".queue").glob("*.ticket"))) == 1,
                "the waiter to queue",
                deadline,
                waiter,
            )
            holder.send_signal(signal.SIGTERM)
            results = _finish([holder, waiter], deadline)
            assert [r[0] for r in results] == [0, 0], results
            events = {(e, n): t for e, n, t in _events(log)}
            assert ("term", "holder") in events, "SIGTERM was not passed on to the command"
            assert events[("start", "waiter")] >= events[("end", "holder")]
            assert json.loads(lock.read_text())["released"] is True
        finally:
            release.touch()


class TestBoundedWait:
    def test_the_timeout_ends_the_wait_without_running_the_command(self, lock, fake, log, holder):
        proc, _release = holder
        out = subprocess.run(
            [sys.executable, str(LOCK), "--", sys.executable, str(fake), str(log), "late"],
            env=_clean_env(lock, PYRITE_SUITE_LOCK_TIMEOUT="0.5"),
            capture_output=True,
            text=True,
            timeout=GROUP_DEADLINE,
        )
        assert out.returncode == EXIT_TIMEOUT
        assert f"pid {proc.pid}" in out.stderr
        assert not _started(log, "late")
        assert list(Path(str(lock) + ".queue").glob("*.ticket")) == []

    @pytest.mark.parametrize("value", ["soon", "-1", ""])
    def test_a_bad_timeout_is_refused_not_guessed(self, lock, fake, log, value):
        out = subprocess.run(
            [sys.executable, str(LOCK), "--", sys.executable, str(fake), str(log), "x"],
            env=_clean_env(lock, PYRITE_SUITE_LOCK_TIMEOUT=value),
            capture_output=True,
            text=True,
            timeout=GROUP_DEADLINE,
        )
        assert out.returncode == 2
        assert "PYRITE_SUITE_LOCK_TIMEOUT" in out.stderr
        assert not _started(log, "x")


class TestALockThatCannotBeUsed:
    def _run(self, lock: Path, fake: Path, log: Path, *pre: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, *pre, str(LOCK), "--", sys.executable, str(fake), str(log), "x"],
            env=_clean_env(lock),
            capture_output=True,
            text=True,
            timeout=GROUP_DEADLINE,
        )

    def test_a_lock_directory_that_is_a_file_is_a_clear_error(self, tmp_path, fake, log):
        (tmp_path / "file").write_text("")
        out = self._run(tmp_path / "file" / "heavy.lock", fake, log)
        assert out.returncode == 2
        assert "PYRITE_SUITE_LOCK_SKIP" in out.stderr
        assert "Traceback" not in out.stderr
        assert not _started(log, "x")

    @pytest.mark.skipif(os.geteuid() == 0, reason="root writes anywhere")
    def test_an_unwritable_cache_is_a_clear_error(self, tmp_path, fake, log):
        cache = tmp_path / "cache"
        cache.mkdir()
        cache.chmod(0o500)
        try:
            out = self._run(cache / "pyrite" / "heavy.lock", fake, log)
        finally:
            cache.chmod(0o700)
        assert out.returncode == 2
        assert "PYRITE_SUITE_LOCK_SKIP" in out.stderr
        assert "Traceback" not in out.stderr
        assert not _started(log, "x")

    def test_without_fcntl_the_run_goes_ahead_and_says_so(self, lock, fake, log):
        """Windows has no fcntl: no lock, one line, the command runs."""
        out = self._run(lock, fake, log, "-c", HIDE_FCNTL)
        assert out.returncode == 0, out.stderr
        assert "no fcntl" in out.stderr
        assert "Traceback" not in out.stderr
        assert _started(log, "x")


# Runs a script (argv[1:]) as __main__ with fcntl and pwd unimportable, as on
# Windows.
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


class TestExitCode:
    @pytest.mark.parametrize("code", [0, 1, 3])
    def test_the_command_exit_code_is_the_wrappers(self, lock, fake, log, code):
        out = subprocess.run(
            [sys.executable, str(LOCK), "--", sys.executable, str(fake), str(log), "x"],
            env=_clean_env(lock, FAKE_EXIT=str(code)),
            capture_output=True,
            text=True,
            timeout=GROUP_DEADLINE,
        )
        assert out.returncode == code

    def test_no_command_is_a_usage_error(self, lock):
        out = subprocess.run(
            [sys.executable, str(LOCK)],
            env=_clean_env(lock),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert out.returncode == 2
        assert out.stderr.startswith("usage:")


class TestWhoSkipsTheLock:
    """While the lock is held, these run at once."""

    @pytest.mark.parametrize(
        "env",
        [
            {"CI": "true"},
            {"GITHUB_ACTIONS": "true"},
            {"PYRITE_SUITE_LOCK_SKIP": "1"},
        ],
        ids=["CI", "GITHUB_ACTIONS", "PYRITE_SUITE_LOCK_SKIP"],
    )
    def test_ci_and_the_escape_hatch_do_not_wait(self, lock, fake, log, holder, env):
        out = subprocess.run(
            [sys.executable, str(LOCK), "--", sys.executable, str(fake), str(log), "free"],
            env=_clean_env(lock, PYRITE_SUITE_LOCK_TIMEOUT="0.5", **env),
            capture_output=True,
            text=True,
            timeout=GROUP_DEADLINE,
        )
        assert out.returncode == 0, out.stderr
        assert _started(log, "free")

    @pytest.mark.parametrize("value", ["0", "false", "no"])
    def test_a_false_skip_value_still_takes_the_lock(self, lock, fake, log, holder, value):
        out = subprocess.run(
            [sys.executable, str(LOCK), "--", sys.executable, str(fake), str(log), "x"],
            env=_clean_env(lock, PYRITE_SUITE_LOCK_TIMEOUT="0.5", PYRITE_SUITE_LOCK_SKIP=value),
            capture_output=True,
            text=True,
            timeout=GROUP_DEADLINE,
        )
        assert out.returncode == EXIT_TIMEOUT

    def test_a_run_nested_under_the_holder_does_not_wait_on_its_parent(
        self, lock, fake, log, holder
    ):
        """The pre-push suite holds the lock, and some of its tests start
        `scripts/test-affected --run`: they inherit the holder's marker."""
        out = subprocess.run(
            [sys.executable, str(LOCK), "--", sys.executable, str(fake), str(log), "child"],
            env=_clean_env(
                lock, PYRITE_SUITE_LOCK_TIMEOUT="0.5", PYRITE_SUITE_LOCK_HELD=str(lock.resolve())
            ),
            capture_output=True,
            text=True,
            timeout=GROUP_DEADLINE,
        )
        assert out.returncode == 0, out.stderr
        assert _started(log, "child")

    def test_the_holder_marks_its_command(self, lock, tmp_path):
        out = subprocess.run(
            [
                sys.executable,
                str(LOCK),
                "--",
                sys.executable,
                "-c",
                "import os; print(os.environ['PYRITE_SUITE_LOCK_HELD'])",
            ],
            env=_clean_env(lock),
            capture_output=True,
            text=True,
            timeout=GROUP_DEADLINE,
        )
        assert out.stdout.strip() == str(lock.resolve())

    @pytest.mark.control(
        reason="a marker for another lock is not this one's holder: waits, as before"
    )
    def test_a_marker_for_another_lock_does_not_skip(self, lock, fake, log, holder, tmp_path):
        out = subprocess.run(
            [sys.executable, str(LOCK), "--", sys.executable, str(fake), str(log), "x"],
            env=_clean_env(
                lock,
                PYRITE_SUITE_LOCK_TIMEOUT="0.5",
                PYRITE_SUITE_LOCK_HELD=str(tmp_path / "other.lock"),
            ),
            capture_output=True,
            text=True,
            timeout=GROUP_DEADLINE,
        )
        assert out.returncode == EXIT_TIMEOUT


class TestTheLockPath:
    def test_the_default_is_outside_any_worktree(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PYRITE_SUITE_LOCK", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        heavy_lock = _script_module("heavy_lock")
        assert heavy_lock.lock_path() == tmp_path / ".cache" / "pyrite" / "heavy.lock"

    def test_a_test_never_reaches_the_real_machine_lock(self, tmp_path, fake, log):
        """A test that starts a heavy runner without its own HOME or
        PYRITE_SUITE_LOCK would wait on the real lock -- for an hour, or on
        its own parent. It fails at once instead, naming the fix."""
        env = _clean_env(tmp_path / "unused")
        del env["PYRITE_SUITE_LOCK"]
        import pwd

        env["HOME"] = pwd.getpwuid(os.getuid()).pw_dir
        env["PYTEST_CURRENT_TEST"] = "tests/test_x.py::test_y (call)"
        out = subprocess.run(
            [sys.executable, str(LOCK), "--", sys.executable, str(fake), str(log), "x"],
            env=env,
            capture_output=True,
            text=True,
            timeout=GROUP_DEADLINE,
        )
        assert out.returncode == 2
        assert "PYRITE_SUITE_LOCK" in out.stderr
        assert not _started(log, "x")

    def test_the_holder_records_who_it_is(self, lock, fake, log, holder):
        proc, _release = holder
        info = json.loads(lock.read_text())
        assert info["pid"] == proc.pid
        assert "holder" in info["command"]
        assert info["since"]
        assert info["cwd"]


# --- the heavy runners go through it -----------------------------------------


@pytest.fixture
def held(lock, tmp_path):
    """The lock held by a bare flock, as any other heavy run would hold it."""
    import fcntl

    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    os.write(
        fd, json.dumps({"pid": os.getpid(), "command": "other suite", "since": "now"}).encode()
    )
    yield
    os.close(fd)


class TestHeavyRunnersTakeTheLock:
    def test_verify_red_waits_for_the_lock(self, lock, held):
        out = subprocess.run(
            ["bash", str(VERIFY_RED)],
            cwd=REPO,
            env=_clean_env(lock, PYRITE_SUITE_LOCK_TIMEOUT="0.5", VERIFY_RED_PYTHON=sys.executable),
            capture_output=True,
            text=True,
            timeout=GROUP_DEADLINE,
        )
        assert out.returncode == EXIT_TIMEOUT, out.stderr
        assert "other suite" in out.stderr

    def _fake_npx(self, tmp_path: Path, log: Path) -> dict[str, str]:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        npx = bin_dir / "npx"
        npx.write_text(f'#!/bin/sh\necho "$PWD $*" >> {log}\n')
        npx.chmod(0o755)
        return {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

    def test_e2e_waits_for_the_lock(self, lock, held, tmp_path, log):
        out = subprocess.run(
            ["bash", str(E2E), "e2e/one.spec.ts"],
            env=_clean_env(lock, PYRITE_SUITE_LOCK_TIMEOUT="0.5", **self._fake_npx(tmp_path, log)),
            capture_output=True,
            text=True,
            timeout=GROUP_DEADLINE,
        )
        assert out.returncode == EXIT_TIMEOUT, out.stderr
        assert not log.exists()

    def test_e2e_runs_playwright_from_web_with_its_arguments(self, lock, tmp_path, log):
        out = subprocess.run(
            ["bash", str(E2E), "e2e/one.spec.ts", "--headed"],
            env=_clean_env(lock, **self._fake_npx(tmp_path, log)),
            capture_output=True,
            text=True,
            timeout=GROUP_DEADLINE,
        )
        assert out.returncode == 0, out.stderr
        assert log.read_text().strip() == f"{REPO / 'web'} playwright test e2e/one.spec.ts --headed"

    def test_e2e_in_ci_needs_no_lock(self, lock, held, tmp_path, log):
        out = subprocess.run(
            ["bash", str(E2E)],
            env=_clean_env(lock, CI="true", **self._fake_npx(tmp_path, log)),
            capture_output=True,
            text=True,
            timeout=GROUP_DEADLINE,
        )
        assert out.returncode == 0, out.stderr
        assert log.read_text().strip() == f"{REPO / 'web'} playwright test"

    def test_npm_scripts_cannot_bypass_the_lock(self):
        scripts = json.loads((REPO / "web" / "package.json").read_text())["scripts"]
        assert scripts["test:e2e"] == "../scripts/e2e.sh"
        for name, command in scripts.items():
            assert "playwright test" not in command, f"npm run {name} runs Playwright unlocked"


# --- instructions name the locked runners ------------------------------------

INSTRUCTION_FILES = [
    ".claude/skills/pyrite-dev/SKILL.md",
    ".claude/skills/pyrite-dev/testing.md",
    ".claude/skills/pyrite-dev/tdd.md",
    ".claude/skills/pyrite-dev/debugging.md",
    "CONTRIBUTING.md",
    "CLAUDE.md",
    "AGENTS.md",
    "README.md",
    "docs/testing.md",
]


# The files whose instructions went around the lock when it landed.
FIXED_BY_THE_LOCK = {
    ".claude/skills/pyrite-dev/testing.md",
    ".claude/skills/pyrite-dev/tdd.md",
    ".claude/skills/pyrite-dev/debugging.md",
    "CONTRIBUTING.md",
    # Found by the wider pattern (the whole suite as `pytest tests/ -v`, or
    # `-n auto`, with no lock) once the conductor files left the repo.
    "README.md",
    "docs/testing.md",
}


# The whole backend suite through bare pytest: `pytest tests/` followed by a
# space or the end of the line (so `pytest tests/test_x.py` is not it) --
# `tests/ -v` and `tests/ extensions/ -n 4` both are.
WHOLE_SUITE = re.compile(r"\bpytest\s+tests/(?=\s|$)")


def unlocked_instructions(text: str) -> list[str]:
    """Lines telling a reader to run a heavy suite around the lock: the whole
    backend suite through bare pytest (the `-m core` smoke set is small and
    exempt), or Playwright through bare npx. A line that goes through a
    locked runner is fine, and so is one whose comment starts with ``CI``:
    it quotes CI, which takes no lock."""
    bad = []
    for line in text.splitlines():
        comment = line.split("#", 1)[1].strip() if "#" in line else ""
        if comment.startswith("CI") or "heavy_lock.py" in line or "scripts/e2e.sh" in line:
            continue
        if "npx playwright test" in line:
            bad.append(line)
        elif WHOLE_SUITE.search(line) and "-m core" not in line:
            bad.append(line)
    return bad


class TestInstructions:
    @pytest.mark.parametrize(
        "rel",
        [
            rel
            if rel in FIXED_BY_THE_LOCK
            else pytest.param(
                rel,
                marks=pytest.mark.control(
                    reason="already named the locked runners; a ratchet, red when one goes back"
                ),
            )
            for rel in INSTRUCTION_FILES
        ],
    )
    def test_every_instruction_names_the_locked_runner(self, rel):
        path = REPO / rel
        if not path.exists():
            pytest.skip(f"{rel} not in this checkout")
        assert unlocked_instructions(path.read_text()) == []

    @pytest.mark.control(reason="tests the check itself, which the change adds whole")
    def test_the_check_finds_the_old_commands(self):
        text = (
            "cd web && npx playwright test --headed\n"
            ".venv/bin/pytest tests/ extensions/ -n auto\n"
            ".venv/bin/pytest tests/ extensions/ -m core -n 4\n"
            "npx playwright test   # CI: the e2e job\n"
            ".venv/bin/pytest tests/ extensions/*/tests/ -q\n"
            ".venv/bin/pytest tests/ extensions/ -n 4   # as CI's gating job runs it\n"
            "scripts/heavy_lock.py -- .venv/bin/pytest tests/ extensions/ -n 4\n"
            "scripts/e2e.sh --headed\n"
            ".venv/bin/pytest tests/ -v\n"
            ".venv/bin/pytest tests/test_one.py -v\n"
            "pytest tests/\n"
        )
        assert unlocked_instructions(text) == [
            "cd web && npx playwright test --headed",
            ".venv/bin/pytest tests/ extensions/ -n auto",
            ".venv/bin/pytest tests/ extensions/*/tests/ -q",
            ".venv/bin/pytest tests/ extensions/ -n 4   # as CI's gating job runs it",
            ".venv/bin/pytest tests/ -v",
            "pytest tests/",
        ]


# --- the conductor's health step: scripts/machine_ok.sh ----------------------

MEMINFO = "MemTotal:       16000000 kB\nMemFree:  100 kB\nMemAvailable:    {avail} kB\n"


def _machine_ok(tmp_path: Path, **env: str) -> subprocess.CompletedProcess:
    full = {k: v for k, v in os.environ.items() if not k.startswith("PYRITE_MACHINE_")}
    full.update(env)
    return subprocess.run(
        ["bash", str(MACHINE_OK)], env=full, capture_output=True, text=True, timeout=GROUP_DEADLINE
    )


class TestMachineOk:
    def test_enough_memory_and_low_load_is_ok(self, tmp_path):
        meminfo = tmp_path / "meminfo"
        meminfo.write_text(MEMINFO.format(avail=8000000))
        out = _machine_ok(
            tmp_path, PYRITE_MACHINE_MEMINFO=str(meminfo), PYRITE_MACHINE_MAX_LOAD="10000"
        )
        assert out.returncode == 0, out.stdout + out.stderr
        assert "available memory 7.6 GB of 15.3 GB" in out.stdout

    def test_too_little_memory_refuses(self, tmp_path):
        meminfo = tmp_path / "meminfo"
        meminfo.write_text(MEMINFO.format(avail=1000000))
        out = _machine_ok(
            tmp_path, PYRITE_MACHINE_MEMINFO=str(meminfo), PYRITE_MACHINE_MAX_LOAD="10000"
        )
        assert out.returncode == 1
        assert "BUSY (memory)" in out.stdout

    def test_load_above_the_limit_refuses(self, tmp_path):
        meminfo = tmp_path / "meminfo"
        meminfo.write_text(MEMINFO.format(avail=8000000))
        out = _machine_ok(
            tmp_path, PYRITE_MACHINE_MEMINFO=str(meminfo), PYRITE_MACHINE_MAX_LOAD="-1"
        )
        assert out.returncode == 1
        assert "BUSY (load)" in out.stdout

    def test_unreadable_memory_never_stops_a_tick(self, tmp_path):
        """No /proc/meminfo, no vm_stat: say so and exit 0."""
        out = _machine_ok(
            tmp_path,
            PYRITE_MACHINE_MEMINFO=str(tmp_path / "missing"),
            PYRITE_MACHINE_MAX_LOAD="10000",
        )
        assert out.returncode == 0, out.stdout + out.stderr
        assert "could not read available memory" in out.stdout

    def test_vm_stat_counts_free_inactive_and_speculative_pages(self):
        text = (
            "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
            "Pages free:                               1000.\n"
            "Pages active:                             9999.\n"
            "Pages inactive:                           2000.\n"
            "Pages speculative:                         500.\n"
        )
        machine_ok = _script_module("machine_ok")
        assert machine_ok.parse_vm_stat(text) == 3500 * 16384
        assert machine_ok.parse_vm_stat("no header") is None

    def test_a_bad_limit_falls_back_to_the_default(self, tmp_path):
        meminfo = tmp_path / "meminfo"
        meminfo.write_text(MEMINFO.format(avail=8000000))
        out = _machine_ok(
            tmp_path, PYRITE_MACHINE_MEMINFO=str(meminfo), PYRITE_MACHINE_MIN_FREE_PCT="lots"
        )
        assert "PYRITE_MACHINE_MIN_FREE_PCT='lots' is not a number" in out.stderr
        assert "limit 30%" in out.stdout
