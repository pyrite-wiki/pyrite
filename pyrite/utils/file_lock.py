"""The momentary lock for entry files (ADR-0042 decision 10, amendments A1, A2).

A write holds this lock only for the instant of compare-and-replace; nothing
is held while a caller reads, decides or merges. It is two locks taken
together: an in-process ``threading.Lock`` and an OS lock (``fcntl.flock``) on
a *stripe file*.

**Where the lock is (A1, as amended in #748): where git locks.** The lock
directory is ``<git-dir>/pyrite/locks``, inside the git directory of the
repository whose work tree holds the file, beside git's own ``index.lock``.
``lock_dir_for`` is the one function that answers "where is the lock for this
file"; ``file_lock`` asks it and nothing else, and nothing overrides it. Whoever
can write the repository can take the lock; no shared temporary directory,
environment variable or cross-user trust decision is involved. A file that is
not in a git work tree is refused (``NotInGitRepository``), so
``atomic_write_text(expect=...)`` fails closed for a KB outside git.

**How the git dir is found.** ``lock_dir_for`` follows git's own discovery
(``setup_git_directory_gently_1`` in git's ``setup.c``) from the file's real
directory, without running git: at each directory, ``.git`` (a directory that
is a repository, or a ``gitdir:`` file) names the git dir; else a directory
that is itself a repository means the file is inside a git dir or a bare
repository, which is refused; else go up. Discovery stops at a filesystem
boundary, as git does by default. It reads **no environment variable**:
``GIT_DIR`` in one process's environment made ``git rev-parse`` name another
repository for the same file, and two writers of one file must never resolve
two lock directories. It runs no subprocess (``git rev-parse`` cost 22-27 ms a
call on macOS, and a server image may not have git). ``tests/test_file_lock.py``
checks it against ``git rev-parse`` on each layout it supports. Not
replicated: ``core.worktree``/``core.bare`` in a repository's config.

**A linked worktree locks in its own git dir** (``.git/worktrees/<name>``),
not the common one: each worktree has its own files, a real path belongs to
exactly one work tree, and that worktree's ``index.lock`` is there too.

**Permissions: never wider than the git dir.** A git dir this user cannot
write is refused, as git could not write ``index.lock`` there. ``pyrite/`` and
``locks/`` are created relative to an fd of the git dir and given the git
dir's own mode bits (setgid included, so a group-shared repository stays
group-shared), whatever the umask. An existing one that grants write to a
class of user the git dir does not is refused. A new stripe file is readable
and writable by exactly the classes that can write the git dir (``0755`` ->
``0600``, ``2775`` -> ``0660``); an existing stripe is opened as it is.
Directories are opened ``O_NOFOLLOW|O_DIRECTORY`` one component at a time and
stripes ``O_NOFOLLOW`` (``O_EXCL`` to create), relative to the verified
directory's fd, and a stripe must be a regular file.

**One file, one lock, whatever path reaches it (A2, maintainer 2026-10-07).**
The key is the ``(st_dev, st_ino)`` of the entry's parent directory plus the
entry's name, folded (below). Every path that reaches the entry
gives the same key: a symlink (the path is resolved first), ``..`` segments,
a firmlink such as macOS ``/System/Volumes/Data`` (where ``realpath`` gives a
different string for the same directory), and case or Unicode spelling of the
name. The atomic rename that replaces the file keeps the key, since the key
names the directory entry, not the inode. The name is folded by Unicode's
canonical caseless match, ``NFD(casefold(NFD(name)))`` (D145): a casefold can
leave a name un-normalised (U+0390 folds to three code points), and NFC before
the fold misses U+1FB7's title case. Against APFS this matched every spelling
APFS treats as one file, for every code point to U+2FFFF. If the
parent directory is replaced while the lock is being taken, the key changes;
``file_lock`` recomputes it under the lock and, if it moved, releases and
retakes the lock on the new key, within the same deadline. A hard link is another name for the
same inode, so another key: ``atomic_write_text`` refuses ``expect=`` writes
to a file with more than one link. On a case-sensitive filesystem ``Entry.md``
and ``entry.md`` share a key, which costs only a wait.

**Stripes, not one file per entry (A2).** The stripe is ``sha256(key) mod
STRIPES`` and the file is ``<lockdir>/<stripe>.lock``. Created on first use and never deleted (a waiter may hold the old inode while
a newcomer creates a new one), so a repository holds at most ``STRIPES``. A
write that moves a file takes the stripes of both paths in ascending order,
deduplicated; both paths must be in one git dir.

**Why the kernel lock.** The kernel drops an OS lock when its holder dies, so
``kill -9`` leaves no stale lock (an ``O_EXCL`` lock file, like git's
``index.lock``, would).

**Why the in-process lock.** ``flock`` binds to the open file description and
every acquire here opens its own, so threads exclude each other on Linux and
macOS. Where ``flock`` falls back to POSIX record locks (Linux NFS since
2.6.12) the lock belongs to the process and threads do not exclude each other;
the ``threading.Lock`` covers that.

**Limits, stated.**

- **Network filesystems.** A git dir on NFS: Linux maps ``flock`` to NLM byte
  locks, which work across clients only when ``lockd`` runs; some mounts
  (``nolock``, macOS NFS, some SMB/FUSE) answer ``ENOLCK``/``ENOTSUP``, and
  ``file_lock`` then raises ``LockDirError`` at once rather than waiting. Two
  machines on a shared or synced folder share no lock (decision 10: per host
  only); they rely on the compare.
- **A stopped holder.** The kernel releases the lock of a process that dies,
  not of one that is stopped (``SIGSTOP``, a debugger, a suspended laptop
  job): writers wait for it until ``LockTimeout``. That is why every wait is
  bounded, and ``file_lock`` rejects an unbounded ``timeout``.
- **A KB reachable only through ``core.worktree`` or ``GIT_DIR``** (a work tree
  whose git dir is not found by walking up from it) is refused as not in a
  git repository.
- **The entry's directory moved away during the write** fails closed: the
  write raises ``FileNotFoundError`` and changes nothing, and its temp file may
  be left in the moved directory (safe to delete, as after a crash).
- **A second mount of one filesystem** that reports another ``st_dev`` (two
  NFS mounts of one export, by reasoning; not run) gives other keys. A bind
  mount keeps ``st_dev`` and ``st_ino``, so its key is the same.
- Only mode bits are read; ACLs are not inspected.
- A forked child shares the parent's open file description, so a child that
  leaves a ``with`` block can release the parent's lock. ``atomic_write_text``
  never forks while holding it.
- **Windows is unsupported:** ``file_lock`` raises ``LockDirError``, so
  ``atomic_write_text(expect=)`` fails closed there.

The lock is not reentrant: taking a stripe you already hold times out. ``fcntl.flock`` is called by module attribute
because ``tests/test_doc_write_as_patch.py`` patches it.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import math
import os
import re
import stat
import threading
import time
import unicodedata
from collections.abc import Iterator
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: unsupported, see the module docstring
    fcntl = None  # type: ignore[assignment]

STRIPES = 1024
LOCK_TIMEOUT = 10.0
"""Seconds a writer waits for the lock by default. The lock is held for a
compare, a rename and a directory fsync (milliseconds), so ten seconds is far
beyond any healthy wait and short enough that a hung filesystem or a stopped
holder fails the write with ``LockTimeout`` instead of hanging the caller."""
LOCK_SUBDIR = ("pyrite", "locks")

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
    """The lock directory or a stripe file cannot be used safely."""


class NotInGitRepository(LockDirError):  # noqa: N818 - reads as the condition
    """The file is not in a git work tree, so it has no lock directory (A1)."""


class LockTimeout(TimeoutError):  # noqa: N818 - reads as the event, like FileChanged
    """The lock was not acquired within ``timeout`` seconds."""


# --------------------------------------------------------------------------
# Where the lock is
# --------------------------------------------------------------------------

_HEX_HEAD = re.compile(rb"[0-9a-f]{40}([0-9a-f]{24})?\s*")


def _valid_head(git_dir: str) -> bool:
    """git's ``validate_headref``: a symlink into ``refs/``, ``ref: ...`` or an
    object id."""
    head = os.path.join(git_dir, "HEAD")
    try:
        if os.path.islink(head):
            return os.readlink(head).startswith("refs/")
        with open(head, "rb") as f:
            text = f.read(256)
    except OSError:
        return False
    return text.startswith(b"ref:") or bool(_HEX_HEAD.fullmatch(text))


def _is_git_directory(path: str) -> bool:
    """git's ``is_git_directory``: a valid HEAD, and ``objects`` and ``refs``
    under the directory, or under the one its ``commondir`` file names."""
    if not os.path.isdir(path):
        return False
    base = path
    try:
        with open(os.path.join(path, "commondir"), encoding="utf-8") as f:
            common = f.read().strip()
        base = os.path.join(path, common)
    except FileNotFoundError:
        pass
    except OSError:
        return False
    return (
        os.access(os.path.join(base, "objects"), os.X_OK)
        and os.access(os.path.join(base, "refs"), os.X_OK)
        and _valid_head(path)
    )


def _read_gitfile(dot_git: str) -> str:
    """The git dir a ``.git`` file names (``gitdir: <path>``, relative to the
    file's directory). Raises ``LockDirError`` where git would refuse."""
    try:
        with open(dot_git, encoding="utf-8") as f:
            text = f.read(4096)
    except OSError as e:
        raise LockDirError(f"cannot read the git file {dot_git} ({e.strerror or e})") from e
    if not text.startswith("gitdir: "):
        raise LockDirError(f"{dot_git} is not a valid git file (no 'gitdir: ' line), as git says")
    target = text[len("gitdir: ") :].rstrip()
    resolved = os.path.join(os.path.dirname(dot_git), target)
    if not _is_git_directory(resolved):
        raise LockDirError(f"{dot_git} points at {target}, which is not a git repository")
    return resolved


def _not_in_git(path: str, why: str) -> NotInGitRepository:
    return NotInGitRepository(
        f"{path} is not in a git repository ({why}). Pyrite takes the lock for "
        "expect= writes inside the repository's git directory (ADR-0042 §10a A1), "
        "so a KB outside git cannot make them; run `git init` in the KB."
    )


def _git_dir_of(directory: str) -> str:
    """The git dir of the work tree holding ``directory`` (a real path)."""
    start_dev = os.stat(directory).st_dev
    current = directory
    while True:
        dot_git = os.path.join(current, ".git")
        if os.path.isfile(dot_git):
            return _read_gitfile(dot_git)
        if os.path.isdir(dot_git) and _is_git_directory(dot_git):
            return dot_git
        if _is_git_directory(current):
            raise NotInGitRepository(
                f"{directory} is inside a git directory or a bare repository ({current}), "
                "not a work tree; Pyrite locks only files in a work tree (ADR-0042 §10a A1)"
            )
        parent = os.path.dirname(current)
        if parent == current:
            raise _not_in_git(directory, "no .git in it or any parent directory")
        if os.stat(parent).st_dev != start_dev:
            raise _not_in_git(
                directory, f"discovery stopped at the filesystem boundary at {current}"
            )
        current = parent


def lock_dir_for(path: str | os.PathLike[str]) -> Path:
    """THE answer to "where is the lock for this file": ``<git-dir>/pyrite/locks``
    for the repository whose work tree holds ``realpath(path)``. The file need
    not exist; its directory must. Pure: reads the filesystem, never the
    environment, never runs git."""
    real = os.path.realpath(path)
    git_dir = os.path.realpath(_git_dir_of(os.path.dirname(real)))
    return Path(git_dir, *LOCK_SUBDIR)


def lock_key(path: str | os.PathLike[str]) -> str:
    """THE identity of an entry for locking (A2): its parent directory's
    ``(st_dev, st_ino)`` and its name folded by canonical caseless match.
    The parent must exist."""
    parent, name = os.path.split(os.path.realpath(path))
    st = os.stat(parent)
    # Unicode's canonical caseless match (D145): NFD(casefold(NFD(name))).
    # A casefold can leave a name un-normalised (U+0390 folds to U+03B9
    # U+0308 U+0301), and NFC before the fold is not enough either: the
    # title case of U+1FB7 composes to U+1FBC, whose fold reorders the iota.
    # Checked against APFS for every code point to U+2FFFF (#752 round 2).
    folded = unicodedata.normalize("NFD", unicodedata.normalize("NFD", name).casefold())
    return f"{st.st_dev}:{st.st_ino}/{folded}"


def _stripe(key: str) -> int:
    digest = hashlib.sha256(key.encode("utf-8", "surrogateescape")).digest()
    return int.from_bytes(digest, "big") % STRIPES


def stripe_of(path: str | os.PathLike[str]) -> int:
    return _stripe(lock_key(path))


def check_timeout(timeout: object) -> float:
    """A lock wait must be bounded: a number of seconds above zero and at most
    ``threading.TIMEOUT_MAX``, the longest wait a lock acquire accepts."""
    why = (
        f"a lock timeout must be a number of seconds above zero and at most "
        f"{threading.TIMEOUT_MAX:g}, not {timeout!r}: a stopped holder keeps its lock, "
        "so an unbounded wait can hang the writer"
    )
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError(why)
    try:
        value = float(timeout)
    except (OverflowError, ValueError):
        raise ValueError(why) from None
    if not math.isfinite(value) or not 0 < value <= threading.TIMEOUT_MAX:
        raise ValueError(why)
    return value


# --------------------------------------------------------------------------
# Opening the lock directory and a stripe
# --------------------------------------------------------------------------

_WRITE_BITS = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _stripe_mode(git_dir_mode: int) -> int:
    """Read and write for exactly the classes that can write the git dir."""
    mode = 0o600
    if git_dir_mode & stat.S_IWGRP:
        mode |= 0o060
    if git_dir_mode & stat.S_IWOTH:
        mode |= 0o006
    return mode


def _wider_than(mode: int, git_dir_mode: int) -> bool:
    """``mode`` lets a class of user write that ``git_dir_mode`` does not."""
    return bool(mode & _WRITE_BITS & ~git_dir_mode & (stat.S_IWGRP | stat.S_IWOTH))


def _unsafe(where: str | os.PathLike[str], why: str) -> LockDirError:
    return LockDirError(f"lock directory {where} cannot be used: {why}")


def _open_child_dir(parent_fd: int, parent: Path, name: str, git_dir_mode: int) -> int:
    path = parent / name
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        created = True
    except FileExistsError:
        created = False
    except OSError as e:
        raise _unsafe(path, f"cannot create it ({e.strerror or e})") from e
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except OSError as e:
        raise _unsafe(path, f"cannot open it as a directory ({e.strerror or e})") from e
    try:
        if created:
            os.fchmod(fd, stat.S_IMODE(git_dir_mode) & 0o3777)  # umask-independent
        elif _wider_than(os.fstat(fd).st_mode, git_dir_mode):
            raise _unsafe(
                path,
                f"its mode {oct(stat.S_IMODE(os.fstat(fd).st_mode))} lets users write it that "
                f"cannot write the git dir ({oct(stat.S_IMODE(git_dir_mode))}); it is wider "
                "than the git dir. Restore it, or delete it and Pyrite recreates it",
            )
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_lock_dir(lock_dir: Path) -> tuple[int, int]:
    """Open ``lock_dir`` (``<git-dir>/pyrite/locks``) one component at a time,
    creating what is missing. Returns its fd and the git dir's mode."""
    git_dir = lock_dir.parents[len(LOCK_SUBDIR) - 1]
    effective = os.access in os.supports_effective_ids
    if not os.access(git_dir, os.W_OK | os.X_OK, effective_ids=effective):
        raise _unsafe(
            lock_dir,
            f"this user cannot write the git dir {git_dir}, so git could not lock there either",
        )
    try:
        current = os.open(git_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as e:
        raise _unsafe(lock_dir, f"cannot open the git dir {git_dir} ({e.strerror or e})") from e
    try:
        git_mode = os.fstat(current).st_mode
        parent = git_dir
        for name in LOCK_SUBDIR:
            child = _open_child_dir(current, parent, name, git_mode)
            os.close(current)
            current, parent = child, parent / name
    except BaseException:
        os.close(current)
        raise
    return current, git_mode


def _open_stripe(lock_dir: Path, stripe: int) -> int:
    dfd, git_mode = _open_lock_dir(lock_dir)
    try:
        name = f"{stripe}.lock"
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        mode = _stripe_mode(git_mode)
        try:
            fd = os.open(name, flags | os.O_CREAT | os.O_EXCL, mode, dir_fd=dfd)
            os.fchmod(fd, mode)  # only a file this call created; umask-independent
        except FileExistsError:
            try:
                fd = os.open(name, flags, dir_fd=dfd)
            except OSError as e:
                raise _unsafe(
                    lock_dir, f"stripe file {name} cannot be opened ({e.strerror or e})"
                ) from e
        except OSError as e:
            raise _unsafe(
                lock_dir, f"stripe file {name} cannot be created ({e.strerror or e})"
            ) from e
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise _unsafe(lock_dir, f"stripe file {name} is not a regular file")
        return fd
    finally:
        os.close(dfd)


# --------------------------------------------------------------------------
# Taking the lock
# --------------------------------------------------------------------------

_BUSY = {errno.EWOULDBLOCK, errno.EAGAIN}


def _no_flock(lock_dir: Path, e: OSError) -> LockDirError:
    return _unsafe(
        lock_dir,
        f"its filesystem does not support flock ({e.strerror or e}); a git dir on a "
        "network filesystem may need lockd, see pyrite.utils.file_lock",
    )


def _os_lock(fd: int, deadline: float, lock_dir: Path) -> None:
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as e:
            if e.errno not in _BUSY:
                raise _no_flock(lock_dir, e) from e
            if time.monotonic() >= deadline:
                raise LockTimeout("lock not acquired in time") from None
            time.sleep(_POLL_SECONDS)


def _os_unlock(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def file_lock(*paths: str | os.PathLike[str], timeout: float = LOCK_TIMEOUT) -> Iterator[None]:
    """Hold the momentary lock for one path, or for two when a write moves a
    file. The lock is where ``lock_dir_for`` says, and only there; the stripe
    is ``stripe_of`` the entry. ``timeout`` must be finite (``check_timeout``)."""
    timeout = check_timeout(timeout)
    if fcntl is None:
        raise LockDirError("file locking is not supported on Windows")
    if not 1 <= len(paths) <= 2:
        raise ValueError("file_lock takes one path, or two for a move")
    lock_dirs = [lock_dir_for(p) for p in paths]
    if len(lock_dirs) == 2 and not os.path.samefile(*(d.parent.parent for d in lock_dirs)):
        raise LockDirError(
            f"a move between two git repositories ({lock_dirs[0].parent.parent} and "
            f"{lock_dirs[1].parent.parent}) cannot be locked as one write"
        )
    lock_dir = lock_dirs[0]
    deadline = time.monotonic() + timeout
    while True:
        keys = [lock_key(p) for p in paths]
        with contextlib.ExitStack() as stack:
            for stripe in sorted({_stripe(k) for k in keys}):
                proc_lock = _process_locks[stripe]
                if not proc_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
                    raise LockTimeout("lock not acquired in time")
                stack.callback(proc_lock.release)
                fd = _open_stripe(lock_dir, stripe)
                stack.callback(os.close, fd)
                _os_lock(fd, deadline, lock_dir)
                stack.callback(_os_unlock, fd)
            # The key names the parent directory by inode. If the directory
            # was replaced while the lock was being taken, the lock held is
            # for the old key: release it and take the new one.
            if [lock_key(p) for p in paths] == keys:
                yield
                return
        if time.monotonic() >= deadline:
            raise LockTimeout("lock not acquired in time: the entry's directory kept changing")
