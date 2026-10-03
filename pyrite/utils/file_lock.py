"""The momentary lock for entry files (ADR-0042 decision 10, amendments A1, A2).

A write holds this lock only for the instant of compare-and-replace; nothing
is held while a caller reads, decides or merges. It is two locks taken
together: an in-process ``threading.Lock`` and an OS lock (``fcntl.flock``,
``msvcrt.locking`` on Windows) on a *stripe file*.

**Stripes, not one file per entry (A2).** The stripe is
``sha256(realpath) mod STRIPES`` and the file is ``<lockdir>/<stripe>.lock``.
Lock files are created on first use and never deleted (a waiter may hold the
old inode while a newcomer creates a new one, so deleting is unsafe), so the
directory is bounded by ``STRIPES`` files however many entries are written.
Two entries sharing a stripe only wait for each other; they never conflict.
A write that moves a file takes the stripes of both paths in ascending order,
deduplicated, so two movers cannot deadlock.

**One lock directory per host, shared by every OS user (A1).** A server
running as a service user and a person editing the same tree must exclude each
other, so the directory does not depend on the user's config or data
directory (never ``default_data_dir()``, never inside the KB):
``$XDG_RUNTIME_DIR/pyrite/locks`` on Linux, ``$TMPDIR/pyrite-locks`` on macOS,
``%LOCALAPPDATA%\\pyrite\\locks`` on Windows. ``PYRITE_LOCK_DIR`` or the
``lock_dir`` argument overrides it. (Reading a ``lock_dir`` setting from the
operator's config is not wired yet.)

**Why the kernel lock.** The kernel drops an OS lock when its holder dies, so
``kill -9`` leaves no stale lock (an ``O_EXCL`` lock file would).

**Why the in-process lock.** ``flock`` binds to the open file description and
every acquire here opens its own, so threads exclude each other on Linux and
macOS. Where ``flock`` falls back to POSIX record locks (Linux NFS since
2.6.12) the lock belongs to the process and threads do not exclude each other;
the ``threading.Lock`` covers that.

The lock is not reentrant: taking a stripe you already hold deadlocks (or
times out, given ``timeout``). ``fcntl.flock`` is called by module attribute
because ``tests/test_doc_write_as_patch.py`` patches it.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import sys
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]
    import msvcrt

STRIPES = 1024
LOCK_DIR_ENV = "PYRITE_LOCK_DIR"

_POLL_SECONDS = 0.001
_process_locks = [threading.Lock() for _ in range(STRIPES)]


class LockTimeout(TimeoutError):  # noqa: N818 - reads as the event, like FileChanged
    """The lock was not acquired within ``timeout`` seconds."""


def default_lock_dir(platform: str | None = None, env: Mapping[str, str] | None = None) -> Path:
    """The lock directory when nothing overrides it (A1). Pure, so each
    platform's answer can be tested on any host."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    if platform.startswith("win"):
        base = env.get("LOCALAPPDATA")
        if base:
            return Path(base) / "pyrite" / "locks"
    elif platform == "darwin":
        tmp = env.get("TMPDIR")
        if tmp:
            return Path(tmp) / "pyrite-locks"
    else:
        runtime = env.get("XDG_RUNTIME_DIR")
        if runtime:
            return Path(runtime) / "pyrite" / "locks"
    return Path(tempfile.gettempdir()) / "pyrite-locks"


def lock_dir(override: str | os.PathLike[str] | None = None) -> Path:
    """The lock directory: argument, then ``PYRITE_LOCK_DIR``, then the default."""
    chosen = override or os.environ.get(LOCK_DIR_ENV)
    return Path(chosen) if chosen else default_lock_dir()


def stripe_of(path: str | os.PathLike[str]) -> int:
    digest = hashlib.sha256(os.path.realpath(path).encode("utf-8", "surrogateescape")).digest()
    return int.from_bytes(digest, "big") % STRIPES


def _open_stripe(directory: Path, stripe: int) -> int:
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o1777)
    except FileExistsError:
        pass
    path = directory / f"{stripe}.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    if hasattr(os, "fchmod"):
        with contextlib.suppress(OSError):  # shared by every OS user on the host (A1)
            os.fchmod(fd, 0o666)
    return fd


def _os_lock(fd: int, deadline: float | None) -> None:
    if fcntl is not None and deadline is None:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return
    while True:
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:  # pragma: no cover - Windows
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            if deadline is not None and time.monotonic() >= deadline:
                raise LockTimeout("lock not acquired in time") from None
            time.sleep(_POLL_SECONDS)


def _os_unlock(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
    else:  # pragma: no cover - Windows
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def file_lock(
    *paths: str | os.PathLike[str],
    lock_dir_path: str | os.PathLike[str] | None = None,
    timeout: float | None = None,
) -> Iterator[None]:
    """Hold the momentary lock for one path, or for two when a write moves a file."""
    if not 1 <= len(paths) <= 2:
        raise ValueError("file_lock takes one path, or two for a move")
    directory = lock_dir(lock_dir_path)
    stripes = sorted({stripe_of(p) for p in paths})
    deadline = None if timeout is None else time.monotonic() + timeout
    with contextlib.ExitStack() as stack:
        for stripe in stripes:
            proc_lock = _process_locks[stripe]
            if not proc_lock.acquire(
                timeout=-1 if deadline is None else max(0.0, deadline - time.monotonic())
            ):
                raise LockTimeout("lock not acquired in time")
            stack.callback(proc_lock.release)
            fd = _open_stripe(directory, stripe)
            stack.callback(os.close, fd)
            _os_lock(fd, deadline)
            stack.callback(_os_unlock, fd)
        yield
