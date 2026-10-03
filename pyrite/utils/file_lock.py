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

**The lock directory (A1, pending the maintainer's decision).** By default it
is per user: ``$XDG_RUNTIME_DIR/pyrite/locks`` on Linux,
``$TMPDIR/pyrite-locks`` on macOS, ``%LOCALAPPDATA%\\pyrite\\locks`` on
Windows (else ``<tempdir>/pyrite-locks``), created ``0o700``. Two OS users
writing one tree exclude each other only when ``PYRITE_LOCK_DIR`` (or the
``lock_dir_path`` argument) points both at one directory that passes the check
below, for example a sticky ``1777`` directory the operator made. It never
depends on the config or data directory (not ``default_data_dir()``) and is
not inside the KB.

**The directory is trusted only if it is safe to be shared.** It must not be a
symlink, must be owned by the current user or root, and must not be writable
by group or others unless it is sticky; otherwise ``LockDirError`` names
``PYRITE_LOCK_DIR``. A stripe file is opened relative to the verified
directory with ``O_NOFOLLOW``; only a file this call just created
(``O_CREAT|O_EXCL``) has its mode set (``0o600``, or ``0o666`` in a shared
sticky directory); an existing one is opened as is, and must be a regular
file. Case and Unicode normalisation are folded before hashing, so
``Entry.md`` and ``entry.md`` share a stripe on a case-insensitive
filesystem (on a case-sensitive one that is only extra waiting).

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
import stat
import sys
import tempfile
import threading
import time
import unicodedata
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


def _reinit_process_locks() -> None:
    """A forked child inherits these in whatever state another thread of the
    parent left them; the thread that held one does not exist in the child."""
    for i in range(STRIPES):
        _process_locks[i] = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reinit_process_locks)


class LockDirError(OSError):
    """The lock directory or a stripe file is not safe to use; the message
    names ``PYRITE_LOCK_DIR``."""


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
    real = unicodedata.normalize("NFC", os.path.realpath(path)).casefold()
    digest = hashlib.sha256(real.encode("utf-8", "surrogateescape")).digest()
    return int.from_bytes(digest, "big") % STRIPES


def _unsafe(directory: Path, why: str) -> LockDirError:
    return LockDirError(
        f"lock directory {directory} is not safe to use: {why}. "
        f"Point {LOCK_DIR_ENV} at a directory you own (mode 0700), or a sticky shared one."
    )


def _is_shared(mode: int) -> bool:
    return bool(mode & (stat.S_IWGRP | stat.S_IWOTH))


def _open_directory(directory: Path) -> int:
    """Create the lock directory if missing (0o700, set explicitly: a mode
    passed to mkdir goes through the umask), then open it without following a
    symlink and check what was opened."""
    try:
        directory.parent.mkdir(parents=True, exist_ok=True)
        directory.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        created = False
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        dfd = os.open(directory, flags)
    except OSError as e:
        raise _unsafe(directory, f"cannot open it as a directory ({e.strerror or e})") from e
    try:
        st = os.fstat(dfd)
        if not stat.S_ISDIR(st.st_mode):
            raise _unsafe(directory, "it is not a directory")
        if created and hasattr(os, "fchmod"):
            os.fchmod(dfd, 0o700)
            st = os.fstat(dfd)
        if hasattr(os, "geteuid") and st.st_uid not in (os.geteuid(), 0):
            raise _unsafe(directory, f"it is owned by uid {st.st_uid}")
        if _is_shared(st.st_mode) and not st.st_mode & stat.S_ISVTX:
            raise _unsafe(directory, "it is writable by others and not sticky")
    except BaseException:
        os.close(dfd)
        raise
    return dfd


def _open_stripe(directory: Path, stripe: int) -> int:
    dfd = _open_directory(directory)
    try:
        shared = _is_shared(os.fstat(dfd).st_mode)
        name = f"{stripe}.lock"
        use_dir_fd = os.open in os.supports_dir_fd
        target = name if use_dir_fd else str(directory / name)
        extra = {"dir_fd": dfd} if use_dir_fd else {}
        base = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(target, base | os.O_CREAT | os.O_EXCL, 0o666 if shared else 0o600, **extra)
            if hasattr(os, "fchmod"):  # only a file this call created; umask-independent
                os.fchmod(fd, 0o666 if shared else 0o600)
        except FileExistsError:
            try:
                fd = os.open(target, base, **extra)
            except OSError as e:
                raise _unsafe(
                    directory, f"stripe file {name} cannot be opened ({e.strerror or e})"
                ) from e
        except OSError as e:
            raise _unsafe(
                directory, f"stripe file {name} cannot be created ({e.strerror or e})"
            ) from e
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise _unsafe(directory, f"stripe file {name} is not a regular file")
        return fd
    finally:
        os.close(dfd)


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
