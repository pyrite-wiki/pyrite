"""Crash-safe file replacement that keeps what the file was (#405).

``atomic_write_text`` writes a temp file beside the real file, fsyncs it,
renames it over, and fsyncs the directory, so a crash leaves either the old
content or the new, never a truncated file. A rename makes a new inode, so
it would silently change three things the old ``open(path, "w")`` kept; each
is handled explicitly:

- **Mode and owner.** The temp file gets the original mode, and the
  original uid/gid (always when running as root, otherwise when they
  differ). If the owner cannot be restored, the write falls back to in
  place: a root save must never leave a root-owned file the service user
  cannot read (826e0c14 did).
- **Hard links.** A file with more than one link is written in place: a
  rename would leave the other names holding the old content.
- **A directory the process cannot write** (a read-only mount with one
  writable file, a root-owned ``~/.pyrite``): written in place.

Every fallback logs a warning naming the file and why the write was not
atomic. A symlink is followed: the rename happens in the target's directory,
so the link stays a link. A file this process may not write (``0o444``) is
refused with ``PermissionError``, as ``open(path, "w")`` refused it, even
when the directory would allow the rename.

Not carried across the rename: ACLs and extended attributes (macOS
``com.apple.*``, SELinux labels); the new file gets the directory's defaults.
A crash between creating the temp file and the rename can leave a
``.<name>.<hex>.tmp`` file beside the real one. Nothing reads or removes it;
it is safe to delete.

**The compare (``expect=``).** With ``expect``, the write is optimistic
(ADR-0042 decision 10): under ``pyrite.utils.file_lock`` it compares the file
with what the caller based its change on, immediately before the rename, and
writes nothing and raises ``FileChanged`` (carrying what is on disk) when they
differ. On the in-place paths above the compare runs under the lock
immediately before the truncate, and the write itself is not atomic (A5).
The lock covers a writer that takes it; an editor that does not can still slip
in between the compare and the rename. Without ``expect`` nothing changes: no
lock, no compare.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import logging
import os
import re
import secrets
import stat
from collections.abc import Iterator
from pathlib import Path

from pyrite.utils.file_lock import file_lock

logger = logging.getLogger(__name__)


LOCK_TIMEOUT = 10.0
"""Seconds ``expect=`` waits for the lock. The lock is held for a compare, a
rename and a directory fsync (milliseconds), so ten seconds is far beyond
any healthy wait and short enough that a hung filesystem or a stuck holder
fails the write with ``LockTimeout`` instead of hanging the caller."""

_DIGEST = re.compile(r"[0-9a-fA-F]{64}")


class _Absent:
    def __repr__(self) -> str:
        return "ABSENT"


ABSENT = _Absent()
"""``expect=ABSENT``: the file must not exist (a create that must not overwrite)."""


class FileChanged(Exception):  # noqa: N818 - the name ADR-0042 decision 10 uses
    """``expect`` did not match the file; nothing was written.

    ``on_disk`` is the file's bytes now, or ``None`` when it does not exist.
    """

    def __init__(self, path: os.PathLike[str] | str, on_disk: bytes | None) -> None:
        super().__init__(f"{path} changed since it was read; nothing was written")
        self.path = str(path)
        self.on_disk = on_disk

    @property
    def on_disk_text(self) -> str | None:
        return None if self.on_disk is None else self.on_disk.decode("utf-8", "replace")


def _matches(expect: bytes | str | _Absent, on_disk: bytes | None) -> bool:
    if expect is ABSENT:
        return on_disk is None
    if on_disk is None:
        return False
    if isinstance(expect, bytes):
        return on_disk == expect
    return hashlib.sha256(on_disk).hexdigest() == expect.lower()  # str: sha256 hex digest


def _fsync_directory(directory: Path) -> None:
    """Make the rename durable. Best effort: it runs after the rename has
    published the new content, so a failure here must not fail the save."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError as e:  # a platform that cannot open a directory (Windows)
        logger.debug("Cannot open %s to fsync it: %s", directory, e)
        return
    try:
        os.fsync(fd)
    except OSError as e:  # EINVAL on filesystems that reject a directory fsync
        logger.debug("fsync of directory %s failed: %s", directory, e)
    finally:
        os.close(fd)


def _write_in_place(target: Path, data: bytes, why: str) -> None:
    logger.warning("Writing %s in place, not atomically: %s", target, why)
    with open(target, "r+b") as f:
        f.truncate(0)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _owner_restored(tmp: str, fd: int, original: os.stat_result) -> bool:
    """Give the temp file the original owner. False when that is not possible."""
    if not hasattr(os, "chown"):
        return True
    mine = os.fstat(fd)
    if os.geteuid() != 0 and (mine.st_uid, mine.st_gid) == (original.st_uid, original.st_gid):
        return True
    try:
        os.chown(tmp, original.st_uid, original.st_gid)
    except OSError:
        return False
    return True


def atomic_write_text(
    path: str | os.PathLike[str],
    text: str,
    *,
    encoding: str = "utf-8",
    expect: bytes | str | _Absent | None = None,
    lock_timeout: float = LOCK_TIMEOUT,
) -> None:
    """Replace ``path`` with ``text`` crash-safely; see the module docstring.

    ``expect``: the file's expected current bytes, its sha256 hex digest (a
    64-hex ``str``; any other ``str`` is a ``ValueError``, so text is never
    mistaken for a digest), or ``ABSENT``. Raises ``FileChanged`` and writes
    nothing on a mismatch, ``LockTimeout`` if the lock is not free in
    ``lock_timeout`` seconds.
    """
    if expect is not None and expect is not ABSENT:
        if isinstance(expect, str):
            if not _DIGEST.fullmatch(expect):
                raise ValueError(
                    "expect as a str must be a 64-hex sha256 digest; pass bytes for content"
                )
        elif not isinstance(expect, bytes):
            raise TypeError(
                f"expect must be bytes, a sha256 hex digest or ABSENT, not {type(expect).__name__}"
            )
    data = text.encode(encoding)
    target = Path(os.path.realpath(path))

    @contextlib.contextmanager
    def guard() -> Iterator[None]:
        """Lock and compare, around the one step that publishes the bytes."""
        if expect is None:
            yield
            return
        with file_lock(target, timeout=lock_timeout):
            try:
                on_disk: bytes | None = target.read_bytes()
            except FileNotFoundError:
                on_disk = None
            if not _matches(expect, on_disk):
                raise FileChanged(target, on_disk)
            yield

    try:
        original: os.stat_result | None = os.stat(target)
    except FileNotFoundError:
        original = None

    # open(path, "w") refused a file this process may not write; a rename
    # would replace it anyway (the directory decides that), so refuse too.
    if original is not None and not os.access(target, os.W_OK):
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(target))

    if original is not None and original.st_nlink > 1:
        with guard():
            _write_in_place(target, data, f"it has {original.st_nlink} hard links")
        return

    tmp = str(target.parent / f".{target.name}.{secrets.token_hex(8)}.tmp")
    # An existing file's mode is set explicitly below; a new file gets what
    # open(path, "w") would have given it (0o666 less the umask).
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600 if original else 0o666)
    except OSError as e:
        if original is None:
            raise
        with guard():
            _write_in_place(
                target, data, f"cannot create a file in {target.parent} ({e.strerror or e})"
            )
        return

    fallback: str | None = None
    try:
        try:
            if original is not None:
                if hasattr(os, "fchmod"):
                    os.fchmod(fd, stat.S_IMODE(original.st_mode))
                if not _owner_restored(tmp, fd, original):
                    fallback = (
                        f"cannot give a new file its owner {original.st_uid}:{original.st_gid}"
                    )
            if fallback is None:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view) :]
                os.fsync(fd)
        finally:
            os.close(fd)
        if fallback is None:
            with guard():
                os.replace(tmp, target)
    except BaseException:
        _remove(tmp)
        raise
    if fallback is not None:
        _remove(tmp)
        with guard():
            _write_in_place(target, data, fallback)
        return
    _fsync_directory(target.parent)


def _remove(tmp: str) -> None:
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
