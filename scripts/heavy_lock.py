#!/usr/bin/env python3
"""One heavy test run at a time on this machine (#168).

On 2026-09-18 the maintainer's orchestration loop ran a 16 GB laptop out of memory: up to
eight `pytest -n auto` suites from different worktrees, plus Playwright,
each unaware of the others. Every heavy runner now takes this lock first:

  scripts/test-affected --run   (and so the pre-push hook)
  scripts/verify-red.sh
  scripts/e2e.sh                (and so `npm run test:e2e`)

A second heavy run waits its turn -- in the order it arrived -- printing who
holds the lock, and starts when the holder ends. It never fails because
another run is going, and never runs beside it.

Usage
  scripts/heavy_lock.py [--label TEXT] -- COMMAND [ARGS...]

  Exit status is COMMAND's; 75 when the wait timed out (COMMAND not run);
  2 for a usage or configuration error.

  As a module: ``heavy_lock.run(command, label, cwd=...)`` -- the same
  locking, signal forwarding and reaping as the command line.

The lock
  ``fcntl.flock`` on ``~/.cache/pyrite/heavy.lock`` -- one per user per
  machine, outside every worktree, so all of them contend for it. The holder
  writes its pid, command, directory, start time and its command's pid into
  the lock file; a waiter prints them on entry and every 60 seconds.

  While the command runs, the lock is held, however the holder is stopped:
  - exit, Ctrl-C, SIGTERM, SIGHUP: the holder forwards SIGTERM/SIGHUP to its
    command (Ctrl-C reaches it from the terminal), waits for it to end, and
    only then marks the record released and lets the lock go.
  - SIGKILL (or a crash): the kernel releases the flock at once -- there is
    never a stale lock to delete -- but the record was not marked released.
    The next run takes the lock and then waits, saying so, until the
    command pid in that record is gone (bounded by the timeout). The work
    is not killed; it finishes, and the next run starts after it.
  The descriptor is not inherited: a server or xdist worker the command
  leaves behind never keeps the lock held. The command's pid is the one
  thing a killed holder's successor waits for; what that command itself
  leaves running after it exits is outside the lock.

  Order: each waiter holds a flock on its own ticket in
  ``heavy.lock.queue/`` and tries the lock only when no older ticket is
  still held. A waiter that died leaves a ticket nobody holds; the next
  waiter to look removes it.

Environment
  PYRITE_SUITE_LOCK          lock file path (default ~/.cache/pyrite/heavy.lock)
  PYRITE_SUITE_LOCK_TIMEOUT  seconds to wait before giving up (default 3600)
  PYRITE_SUITE_LOCK_SKIP=1   do not take the lock (the escape hatch: a
                             one-off run you know is small, a machine that
                             can afford two)
  CI / GITHUB_ACTIONS        set: no lock; a CI runner is its own machine
  PYRITE_SUITE_LOCK_HELD     set by the holder for its command: a heavy run
                             started inside it (a test of test-affected
                             under the pre-push suite) is part of the run
                             already holding the lock, so it does not wait
                             on its own parent

Where ``fcntl`` does not exist (Windows), runs go ahead without a lock and
say so in one line.

A single test file run by hand (``pytest tests/test_x.py``) goes through no
runner and takes no lock.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

try:  # POSIX only; Windows runs without the lock (one line says so).
    import fcntl
except ImportError:  # pragma: no cover - exercised by a test that hides it
    fcntl = None  # type: ignore[assignment]

EXIT_TIMEOUT = 75  # EX_TEMPFAIL: try again later
EXIT_USAGE = 2
DEFAULT_TIMEOUT = 3600.0
NOTICE_EVERY = 60.0
POLL = 0.1
_FALSE = {"", "0", "false", "no", "off"}
_SKIP_HINT = "set PYRITE_SUITE_LOCK to another path, or PYRITE_SUITE_LOCK_SKIP=1 to run without it"


class LockError(Exception):
    """The lock cannot be used as configured; exit 2 with this message."""


class LockTimeoutError(Exception):
    """The wait ended before the lock was free."""


def _set(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() not in _FALSE


def lock_path() -> Path:
    override = os.environ.get("PYRITE_SUITE_LOCK")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "pyrite" / "heavy.lock"


def skip_reason(path: Path) -> str | None:
    """Why this run takes no lock, or None when it must take it."""
    if _set("CI") or _set("GITHUB_ACTIONS"):
        return "CI"
    if _set("PYRITE_SUITE_LOCK_SKIP"):
        return "PYRITE_SUITE_LOCK_SKIP"
    held = os.environ.get("PYRITE_SUITE_LOCK_HELD")
    if held and _same(Path(held), path):
        return "nested in the run holding it"
    if fcntl is None:
        return "no fcntl on this platform"
    return None


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _check_not_a_test(path: Path) -> None:
    """A test that reaches the real machine lock would wait on whatever
    suite holds it -- possibly its own parent. Fail fast, naming the fix."""
    if not os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("PYRITE_SUITE_LOCK"):
        return
    try:
        import pwd

        real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError):
        return
    if _same(Path.home(), real_home):
        raise LockError(
            f"a test reached the machine-wide lock {path}; set PYRITE_SUITE_LOCK "
            "(or HOME) to a temporary path in the test"
        )


def _timeout() -> float:
    raw = os.environ.get("PYRITE_SUITE_LOCK_TIMEOUT")
    if raw is None:
        return DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        value = -1.0
    if not value >= 0:
        raise LockError(f"PYRITE_SUITE_LOCK_TIMEOUT={raw!r}: a number of seconds, 0 or more")
    return value


def _record(path: Path) -> dict:
    try:
        info = json.loads(path.read_text() or "{}")
    except (OSError, ValueError):
        return {}
    return info if isinstance(info, dict) else {}


def _holder_line(path: Path) -> str:
    info = _record(path)
    if not info:
        return f"the heavy-run lock {path} (holder not recorded yet)"
    return (
        f"the heavy-run lock {path}, held by pid {info.get('pid', '?')} "
        f"({info.get('command', '?')}) in {info.get('cwd', '?')} since {info.get('since', '?')}"
    )


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _orphan(info: dict) -> int | None:
    """The command pid a killed holder left running, if it still runs."""
    child = info.get("child")
    if info.get("released") or not isinstance(child, int) or child <= 0:
        return None
    return child if _alive(child) else None


def _try_flock(fd: int, mode: int) -> bool:
    try:
        fcntl.flock(fd, mode | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
            return False
        raise


def _take_ticket(queue: Path) -> tuple[int, Path]:
    """Join the line: a ticket file named by arrival time, flocked before it
    becomes visible, so no one ever sees it unheld and removes it."""
    queue.mkdir(parents=True, exist_ok=True)
    name = f"{time.time_ns():020d}-{os.getpid()}"
    tmp = queue / f"{name}.tmp"
    fd = os.open(tmp, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    ticket = queue / f"{name}.ticket"
    os.rename(tmp, ticket)
    return fd, ticket


def _ahead(queue: Path, mine: Path) -> int:
    """How many live waiters arrived before me; removes dead ones' tickets."""
    live = 0
    for other in sorted(queue.glob("*.ticket")):
        if other.name >= mine.name:
            break
        try:
            fd = os.open(other, os.O_RDONLY)
        except FileNotFoundError:
            continue
        try:
            if _try_flock(fd, fcntl.LOCK_SH):
                # Nobody holds it: its waiter died without leaving the line.
                with contextlib.suppress(FileNotFoundError):
                    other.unlink()
            else:
                live += 1
        finally:
            os.close(fd)
    return live


class Held:
    """The lock, held: its descriptor and the record in the lock file."""

    def __init__(self, fd: int, record: dict):
        self.fd = fd
        self.record = record
        self._write()

    def _write(self) -> None:
        os.ftruncate(self.fd, 0)
        os.pwrite(self.fd, json.dumps(self.record).encode(), 0)

    def started(self, pid: int) -> None:
        """Name the command's pid: a successor of a killed holder waits for it."""
        self.record["child"] = pid
        self._write()

    def release(self) -> None:
        """Mark the record released -- the command is over -- and let go."""
        with contextlib.suppress(OSError):
            self.record["released"] = True
            self._write()
        os.close(self.fd)


def acquire(path: Path, label: str, timeout: float | None = None) -> Held:
    """Wait in line for the lock, then for any command a killed holder left
    running; return it held. Raises LockTimeoutError when ``timeout`` seconds
    pass first, LockError when the lock file cannot be used at all."""
    timeout = _timeout() if timeout is None else timeout
    try:
        return _acquire(path, label, timeout)
    except OSError as exc:
        raise LockError(f"cannot use the heavy-run lock {path}: {exc}; {_SKIP_HINT}") from exc


def _acquire(path: Path, label: str, timeout: float) -> Held:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)  # not inheritable (PEP 446)
    queue = path.with_name(path.name + ".queue")
    try:
        ticket_fd, ticket = _take_ticket(queue)
    except BaseException:
        os.close(fd)
        raise
    start = time.monotonic()
    next_notice = start
    locked = False
    said = None  # what the last notice was about: a new reason gets a line at once
    try:
        while True:
            ahead = 0 if locked else _ahead(queue, ticket)
            if locked or (ahead == 0 and _try_flock(fd, fcntl.LOCK_EX)):
                locked = True
                orphan = _orphan(_record(path))
                if orphan is None:
                    break
                waiting_for = (
                    f"pid {orphan}, the command of a heavy run whose holder was killed "
                    f"({_record(path).get('command', '?')}); it holds no lock but still runs"
                )
            else:
                waiting_for = _holder_line(path)
            now = time.monotonic()
            if now - start >= timeout:
                raise LockTimeoutError(
                    f"heavy-lock: gave up after {timeout:g}s waiting for {waiting_for}; "
                    f"{label} not run (PYRITE_SUITE_LOCK_TIMEOUT)"
                )
            reason = "orphan" if locked else "holder"
            if now >= next_notice or reason != said:
                said = reason
                line = f"heavy-lock: waiting for {waiting_for}"
                if ahead:
                    line += f"; {ahead} more ahead of this run"
                print(line, file=sys.stderr, flush=True)
                next_notice = now + NOTICE_EVERY
            time.sleep(POLL)
        held = Held(
            fd,
            {
                "pid": os.getpid(),
                "command": label,
                "cwd": os.getcwd(),
                "since": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
        )
    except BaseException:
        os.close(fd)
        raise
    finally:
        with contextlib.suppress(FileNotFoundError):
            ticket.unlink()
        os.close(ticket_fd)
    waited = time.monotonic() - start
    if waited >= 1:
        print(f"heavy-lock: acquired after {waited:.0f}s", file=sys.stderr, flush=True)
    return held


@contextlib.contextmanager
def hold(label: str):
    """Hold the machine-wide lock for the body (yields a Held), or yield None
    when this run skips it. Sets PYRITE_SUITE_LOCK_HELD for child processes.

    Prefer run(): a body that starts a command must also forward signals to
    it and wait for it before the lock is let go."""
    path = lock_path()
    reason = skip_reason(path)
    if reason is not None:
        if reason.startswith("no fcntl"):
            print(f"heavy-lock: {reason}; running without the machine-wide lock", file=sys.stderr)
        yield None
        return
    _check_not_a_test(path)
    held = acquire(path, label)
    previous = os.environ.get("PYRITE_SUITE_LOCK_HELD")
    os.environ["PYRITE_SUITE_LOCK_HELD"] = str(path.resolve())
    try:
        yield held
    finally:
        if previous is None:
            os.environ.pop("PYRITE_SUITE_LOCK_HELD", None)
        else:
            os.environ["PYRITE_SUITE_LOCK_HELD"] = previous
        held.release()


_FORWARDED = tuple(getattr(signal, name) for name in ("SIGTERM", "SIGHUP") if hasattr(signal, name))


def run(command: list[str], label: str | None = None, *, cwd: str | Path | None = None) -> int:
    """Run ``command`` under the lock and return its exit status (or
    EXIT_TIMEOUT / EXIT_USAGE, with the reason on stderr).

    The lock is let go only after the command has ended: SIGTERM and SIGHUP
    to this process are passed on to it and the command is waited for;
    Ctrl-C reaches it from the terminal, and this process waits for it too.
    The command inherits os.environ, PYRITE_SUITE_LOCK_HELD included."""
    label = label or shlex.join(command)
    try:
        with hold(label) as held:
            proc = subprocess.Popen(command, cwd=cwd)
            if held is not None:
                held.started(proc.pid)

            def forward(signum, _frame):
                with contextlib.suppress(ProcessLookupError):
                    proc.send_signal(signum)

            previous = {sig: signal.signal(sig, forward) for sig in _FORWARDED}
            try:
                while True:
                    try:
                        return proc.wait()
                    except KeyboardInterrupt:
                        # The terminal sent SIGINT to the command as well; it
                        # decides how to stop, and the lock waits for it.
                        continue
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
    except LockTimeoutError as exc:
        print(exc, file=sys.stderr)
        return EXIT_TIMEOUT
    except LockError as exc:
        print(f"heavy-lock: {exc}", file=sys.stderr)
        return EXIT_USAGE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--label", help="what the waiting message calls this run")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    opts = parser.parse_args(argv)
    command = opts.command[1:] if opts.command[:1] == ["--"] else opts.command
    if not command:
        parser.print_usage(sys.stderr)
        return EXIT_USAGE
    return run(command, opts.label)


if __name__ == "__main__":
    sys.exit(main())
