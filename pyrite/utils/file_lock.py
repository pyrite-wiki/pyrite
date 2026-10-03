"""The momentary lock for entry files (ADR-0042 decision 10, amendments A1, A2).

A write holds this lock only for the instant of compare-and-replace; nothing
is held while a caller reads, decides or merges. It is two locks taken
together: an in-process ``threading.Lock`` and an OS lock (``fcntl.flock``) on a *stripe file*.

**Stripes, not one file per entry (A2).** The stripe is
``sha256(realpath) mod STRIPES`` and the file is ``<lockdir>/<stripe>.lock``.
Lock files are created on first use and never deleted (a waiter may hold the
old inode while a newcomer creates a new one, so deleting is unsafe), so the
directory is bounded by ``STRIPES`` files however many entries are written.
Two entries sharing a stripe only wait for each other; they never conflict.
A write that moves a file takes the stripes of both paths in ascending order,
deduplicated, so two movers cannot deadlock.

**The lock directory (A1, decided in #742).** Per user by default, in every
environment: ``$XDG_RUNTIME_DIR/pyrite/locks`` on Linux and
``$TMPDIR/pyrite-locks`` on macOS (both per-user directories), created
``0o700``. When that variable is unset, or names a directory group or other
users can write (``/tmp``, a ``2775`` container directory), the path is
``<tempdir>/pyrite-locks-<uid>``; if the temp directory itself lets others
rename entries (writable and not sticky), ``LockDirError`` is raised rather
than using it. Two OS users exclude each other only when
``PYRITE_LOCK_DIR`` (or the ``lock_dir_path`` argument) names one absolute
directory that both can use, and the check below allows exactly one shape: a
**root-owned sticky directory** (``root``, mode ``1777``). A directory owned by
one ordinary user is refused for every other user. The directory is never
``default_data_dir()`` and never inside the KB.

**The directory is used only if it passes this check**, else ``LockDirError``
names ``PYRITE_LOCK_DIR``: not a symlink, owned by the current user or root,
and not writable by group or others unless sticky. A stripe file is opened
relative to the verified directory's fd with ``O_NOFOLLOW``; only a file this
call just created (``O_CREAT|O_EXCL``) has its mode set (``0o600``, or
``0o666`` in a shared sticky directory); an existing one is opened as is, and
must be a regular file. Case and Unicode normalisation are folded before
hashing, so ``Entry.md`` and ``entry.md`` share a stripe on a case-insensitive
filesystem (on a case-sensitive one that is only extra waiting).

**Limits, stated.**

- Only the final path component of the lock directory is checked: a symlink
  in a parent is followed (``/var`` is one on macOS).
- Group-writable counts as shared, so a setgid ``2770`` group directory is
  refused.
- Only mode bits are read; ACLs are not inspected.
- A forked child shares the parent's open file description, so a child that
  leaves a ``with`` block can release the parent's lock. ``atomic_write_text``
  never forks while holding it.
- In a shared directory another user can restrict a stripe file they created
  (mode ``0o600``), so everyone else gets ``LockDirError`` (EACCES) for the
  entries on it; and any user can hold any stripe, so others wait until
  ``LockTimeout`` (10 s in ``atomic_write_text``).
- ``PYRITE_LOCK_DIR`` must be absolute.
- **Windows is unsupported:** ``file_lock`` raises ``LockDirError``, so
  ``atomic_write_text(expect=)`` fails closed there.

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
import getpass
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
except ImportError:  # Windows: unsupported, see the module docstring
    fcntl = None  # type: ignore[assignment]

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


_OTHERS_WRITE = stat.S_IWGRP | stat.S_IWOTH


def _is_shared(mode: int) -> bool:
    """Group or other users can write to a directory with this mode."""
    return bool(mode & _OTHERS_WRITE)


def _others_can_rename(mode: int) -> bool:
    """THE predicate: another user can write this directory (group-write or
    other-write) and it is not sticky, so they can rename or remove what is in
    it. Every "is this directory private?" question in this module asks it."""
    return _is_shared(mode) and not mode & stat.S_ISVTX


def _uid() -> str:
    if hasattr(os, "geteuid"):
        return str(os.geteuid())
    return getpass.getuser()


def _private_base(base: str) -> bool:
    """A base directory to create ``pyrite`` under: not group- or other-writable
    at all (a sticky shared one such as /tmp lets another user squat the name)."""
    try:
        return not _is_shared(os.stat(base).st_mode)
    except OSError:
        return True


def _fallback_parent() -> Path:
    """The temp directory the per-user fallback lives in. It must not be one
    where others can rename entries (a 0777 non-sticky TMPDIR): fail closed."""
    parent = Path(tempfile.gettempdir())
    try:
        mode = os.stat(parent).st_mode
    except OSError:
        return parent
    if _others_can_rename(mode):
        raise _unsafe(parent, "other users can rename entries in it (writable and not sticky)")
    return parent


def default_lock_dir(
    platform: str | None = None, env: Mapping[str, str] | None = None, uid: str | None = None
) -> Path:
    """The lock directory when nothing overrides it (A1): per user in every
    branch. Pure apart from one ``stat`` of the environment's base directory."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    uid = _uid() if uid is None else uid
    if platform == "darwin":
        base, name = env.get("TMPDIR"), Path("pyrite-locks")
    else:
        runtime = env.get("XDG_RUNTIME_DIR")
        base, name = runtime, Path("pyrite") / "locks"
    if base and _private_base(base):
        return Path(base) / name
    return _fallback_parent() / f"pyrite-locks-{uid}"


def lock_dir(override: str | os.PathLike[str] | None = None) -> Path:
    """The lock directory: argument, then ``PYRITE_LOCK_DIR``, then the default.
    It must be absolute."""
    chosen = override or os.environ.get(LOCK_DIR_ENV)
    result = Path(chosen) if chosen else default_lock_dir()
    if not result.is_absolute():
        raise LockDirError(
            f"lock directory {result} is not absolute; set {LOCK_DIR_ENV} to an absolute path"
        )
    return result


def stripe_of(path: str | os.PathLike[str]) -> int:
    real = unicodedata.normalize("NFC", os.path.realpath(path)).casefold()
    digest = hashlib.sha256(real.encode("utf-8", "surrogateescape")).digest()
    return int.from_bytes(digest, "big") % STRIPES


def _unsafe(directory: Path, why: str) -> LockDirError:
    return LockDirError(
        f"lock directory {directory} is not safe to use: {why}. "
        f"Point {LOCK_DIR_ENV} at a directory you own (mode 0700), or a root-owned sticky one."
    )


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
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        dfd = os.open(directory, flags)
    except OSError as e:
        raise _unsafe(directory, f"cannot open it as a directory ({e.strerror or e})") from e
    try:
        st = os.fstat(dfd)
        if created and hasattr(os, "fchmod"):
            os.fchmod(dfd, 0o700)
            st = os.fstat(dfd)
        if hasattr(os, "geteuid") and st.st_uid not in (os.geteuid(), 0):
            raise _unsafe(directory, f"it is owned by uid {st.st_uid}")
        if _others_can_rename(st.st_mode):
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
    if deadline is None:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise LockTimeout("lock not acquired in time") from None
            time.sleep(_POLL_SECONDS)


def _os_unlock(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def file_lock(
    *paths: str | os.PathLike[str],
    lock_dir_path: str | os.PathLike[str] | None = None,
    timeout: float | None = None,
) -> Iterator[None]:
    """Hold the momentary lock for one path, or for two when a write moves a file."""
    if fcntl is None:
        raise LockDirError("file locking is not supported on Windows")
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
