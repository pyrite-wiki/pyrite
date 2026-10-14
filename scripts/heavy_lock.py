#!/usr/bin/env python3
"""One heavy test run at a time on this machine (#168).

On 2026-09-18 the conductor loop ran a 16 GB laptop out of memory: up to
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

  As a module: ``with heavy_lock.hold("label"):`` around the heavy part.

The lock
  ``fcntl.flock`` on ``~/.cache/pyrite/heavy.lock`` -- one per user per
  machine, outside every worktree, so all of them contend for it. The kernel
  releases it when the holder exits, however it exits (SIGKILL included):
  there is no stale lock to clean up. The descriptor is not inherited, so a
  server or xdist worker the command leaves behind cannot keep it held. The
  holder writes its pid, command, directory and start time into the lock
  file; a waiter prints them on entry and every 60 seconds.

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

A single test file run by hand (``pytest tests/test_x.py``) goes through no
runner and takes no lock.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import json
import os
import pwd
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

EXIT_TIMEOUT = 75  # EX_TEMPFAIL: try again later
EXIT_USAGE = 2
DEFAULT_TIMEOUT = 3600.0
NOTICE_EVERY = 60.0
POLL = 0.1
_FALSE = {"", "0", "false", "no", "off"}


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
        real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
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


def _holder_line(path: Path) -> str:
    try:
        info = json.loads(path.read_text() or "{}")
    except (OSError, ValueError):
        info = {}
    if not info:
        return f"the heavy-run lock {path} (holder not recorded yet)"
    return (
        f"the heavy-run lock {path}, held by pid {info.get('pid', '?')} "
        f"({info.get('command', '?')}) in {info.get('cwd', '?')} since {info.get('since', '?')}"
    )


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


def acquire(path: Path, label: str, timeout: float | None = None) -> int:
    """Wait in line for the lock; return its descriptor, held.

    Raises LockTimeoutError when ``timeout`` seconds pass first."""
    timeout = _timeout() if timeout is None else timeout
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)  # not inheritable (PEP 446)
    queue = path.with_name(path.name + ".queue")
    ticket_fd, ticket = _take_ticket(queue)
    start = time.monotonic()
    next_notice = start
    try:
        while True:
            ahead = _ahead(queue, ticket)
            if ahead == 0 and _try_flock(fd, fcntl.LOCK_EX):
                break
            now = time.monotonic()
            if now - start >= timeout:
                raise LockTimeoutError(
                    f"heavy-lock: gave up after {timeout:g}s waiting for {_holder_line(path)}; "
                    f"{label} not run (PYRITE_SUITE_LOCK_TIMEOUT)"
                )
            if now >= next_notice:
                line = f"heavy-lock: waiting for {_holder_line(path)}"
                if ahead:
                    line += f"; {ahead} more ahead of this run"
                print(line, file=sys.stderr, flush=True)
                next_notice = now + NOTICE_EVERY
            time.sleep(POLL)
    except BaseException:
        os.close(fd)
        raise
    finally:
        with contextlib.suppress(FileNotFoundError):
            ticket.unlink()
        os.close(ticket_fd)
    record = {
        "pid": os.getpid(),
        "command": label,
        "cwd": os.getcwd(),
        "since": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    os.ftruncate(fd, 0)
    os.pwrite(fd, json.dumps(record).encode(), 0)
    waited = time.monotonic() - start
    if waited >= 1:
        print(f"heavy-lock: acquired after {waited:.0f}s", file=sys.stderr, flush=True)
    return fd


@contextlib.contextmanager
def hold(label: str) -> Iterator[None]:
    """Hold the machine-wide lock for the body, unless this run skips it.

    Sets PYRITE_SUITE_LOCK_HELD for the body's child processes."""
    path = lock_path()
    if skip_reason(path) is not None:
        yield
        return
    _check_not_a_test(path)
    fd = acquire(path, label)
    previous = os.environ.get("PYRITE_SUITE_LOCK_HELD")
    os.environ["PYRITE_SUITE_LOCK_HELD"] = str(path.resolve())
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("PYRITE_SUITE_LOCK_HELD", None)
        else:
            os.environ["PYRITE_SUITE_LOCK_HELD"] = previous
        os.close(fd)


def run(command: list[str], label: str | None = None) -> int:
    """Run ``command`` under the lock; its exit status, or EXIT_TIMEOUT/EXIT_USAGE."""
    label = label or shlex.join(command)
    try:
        with hold(label):
            proc = subprocess.Popen(command)
            # Ctrl-C reaches the whole foreground group anyway; pass SIGTERM on
            # so `kill <wrapper>` does not leave the command running unlocked.
            previous = signal.signal(signal.SIGTERM, lambda *_: proc.terminate())
            try:
                return proc.wait()
            except KeyboardInterrupt:
                return proc.wait()
            finally:
                signal.signal(signal.SIGTERM, previous)
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
