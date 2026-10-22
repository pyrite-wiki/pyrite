"""The one way Pyrite writes, moves and removes a KB file: inside its root.

A file is part of a KB only if it resolves, links followed, inside that KB's
resolved root. :class:`KBFiles` decides that for one KB and makes the write
itself, so the path it approves is the path it writes, with no window between
them. This is the write side of the one KB-file opener; reads are not routed
through it yet.

How a write stays inside, on POSIX (``dir_fd`` and ``O_NOFOLLOW``):

* the path is resolved once, and refused when the result leaves the root
  (``KBFileOutsideRootError``, ``OUTSIDE_KB_ROOT``). A link inside the root to
  a file or folder inside it resolves to its target, which is what is
  written; the link stays a link (ADR-0042 decision 12);
* the root is opened, and each folder of the resolved path is opened beneath
  it with ``O_NOFOLLOW``. A folder swapped for a link after the resolve makes
  that open fail, and the write is refused (``KBFileOutsideRootError``);
* every later step goes through the folder's descriptor, never through a path
  resolved again: the file is read ``O_NOFOLLOW``, the temp file is created
  ``O_CREAT | O_EXCL | O_NOFOLLOW`` beside it, and ``rename`` (which never
  follows its target) or ``link`` (exclusive, for a create) publishes it. A
  link swapped in for the file itself is replaced, never followed.

The rules, one per kind of write:

* **write** (``write_bytes``): inside the root; a file with more than one hard
  link is refused (``HardLinked``, ADR-0042 A2): its other names may be outside
  the root, and cannot be told from the inode. With ``expect`` the file is
  compared with the bytes the change was computed on, under the momentary
  lock where the KB is in git (ADR-0042 decision 10, §10a A1), and without one
  outside git (D1 (b)).
* **move** (``move_bytes``, ``move``): the source is not a symbolic link
  (moving the name would leave the target behind; moving the bytes would
  remove the target) and has one link; the destination is never replaced.
* **remove** (``unlink``): the name and the folder it is in resolve inside the
  root. The name itself is removed (a link as a link); removing one name of a
  hard-linked file leaves its other names as they were.

A refusal raises before anything is written, and its message names neither
the root nor an absolute path (the server log does).

Limits, stated rather than claimed away:

* off POSIX (no ``dir_fd``) the resolved path is used directly, which leaves
  the swap window open there;
* the root itself is resolved once per write and opened by that path: a root
  replaced mid-write is not detected (the root is the operator's);
* a folder already opened that is then moved out of the root (a rename of
  the folder itself, not a link swapped in) keeps its descriptor, and the
  write lands where the folder went. Moving a KB folder out of the KB is a
  local process acting on the operator's files, not a link a checkout can
  bring.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import secrets
import stat
from collections.abc import Iterator
from pathlib import Path

from ..exceptions import ValidationError
from .atomic_write import ABSENT, FileChanged, HardLinked
from .containment import inside_or_warn
from .file_lock import LOCK_TIMEOUT, NotInGitRepository, check_timeout, file_lock

logger = logging.getLogger(__name__)

__all__ = [
    "KBFileIsLinkError",
    "KBFileOutsideRootError",
    "KBFiles",
    "KBFolderChanged",
    "OwnerNotKeptError",
]

_HAS_DIR_FD = (
    os.open in os.supports_dir_fd
    and os.mkdir in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
    and os.rename in os.supports_dir_fd
    and os.link in os.supports_dir_fd
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
)
_DIR_FLAGS = getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_UNSUPPORTED = {errno.ENOTSUP, errno.ENOSYS, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}


class KBFileOutsideRootError(ValidationError):
    """A path left its KB's root; nothing was written. The message names no
    path."""

    error_code = "OUTSIDE_KB_ROOT"

    def __init__(self) -> None:
        super().__init__(
            "Refusing to write a KB file whose path leaves its KB's folder: a symbolic "
            "link on the path (to a file or a folder), or a configured path such as a "
            "type's subdirectory, leads outside the KB; nothing was written"
        )
        self.suggestion = (
            "replace the link with the file or folder it points to, inside the KB, or "
            "correct the configured path, then retry"
        )


class KBFolderChanged(FileChanged):
    """The folder opened is not the one the caller checked (``expect_folder``):
    a folder was replaced between the check and the write; nothing written."""


class OwnerNotKeptError(Exception):
    """A replace that may not fall back to writing in place
    (``write_bytes(in_place=False)``) could not give its new file the
    original's owner and group; nothing was written."""

    def __init__(self, uid: int, gid: int):
        super().__init__(f"cannot preserve owner {uid} and group {gid}")
        self.uid = uid
        self.gid = gid


class KBFileIsLinkError(ValidationError):
    """A move of a KB file that is itself a symbolic link; nothing was
    written. The message names no path."""

    def __init__(self) -> None:
        super().__init__(
            "Refusing to move a KB file that is a symbolic link: moving the link would "
            "leave its target behind, and moving the file would remove the target; "
            "nothing was written"
        )
        self.suggestion = "replace the link with the file it points to, then retry"


def _parts_below(real_root: str, real: str) -> tuple[str, ...] | None:
    """The components of ``real`` below ``real_root``, or None when it is not
    below it. Compared by name first, then (case-insensitive filesystems and
    other spellings of one directory) by identity of an ancestor."""
    try:
        if os.path.commonpath([real_root, real]) == real_root:
            rel = os.path.relpath(real, real_root)
            return () if rel == "." else tuple(rel.split(os.sep))
    except ValueError:
        pass
    try:
        if not os.path.isdir(real_root):
            return None
        parts: list[str] = []
        cur = real
        while True:
            if os.path.exists(cur) and os.path.samefile(cur, real_root):
                return tuple(reversed(parts))
            up = os.path.dirname(cur)
            if up == cur:
                return None
            parts.append(os.path.basename(cur))
            cur = up
    except OSError:
        return None


class Folder:
    """One KB folder, opened beneath the root without following a link.
    Every operation is relative to it (``fd``); off POSIX, to ``real``."""

    def __init__(self, fd: int | None, real: Path, rel: tuple[str, ...]):
        self.fd = fd
        self.real = real
        self.rel = rel

    def label(self, name: str) -> str:
        """``name``'s KB-relative path, for a message."""
        return "/".join((*self.rel, name))

    def _at(self, name: str) -> dict:
        return {"dir_fd": self.fd} if self.fd is not None else {}

    def _p(self, name: str) -> str:
        return name if self.fd is not None else str(self.real / name)

    def lstat(self, name: str) -> os.stat_result | None:
        try:
            return os.stat(self._p(name), follow_symlinks=False, **self._at(name))
        except FileNotFoundError:
            return None

    def read(self, name: str) -> bytes | None:
        """The file's bytes, read without following a link; None when absent.
        A link where the file was is a refusal."""
        try:
            fd = os.open(self._p(name), os.O_RDONLY | _NOFOLLOW | os.O_NONBLOCK, **self._at(name))
        except FileNotFoundError:
            return None
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise KBFileOutsideRootError() from e
            raise
        try:
            chunks = []
            while chunk := os.read(fd, 1 << 16):
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(fd)

    def writable(self, name: str) -> bool:
        if self.fd is not None:
            return os.access(name, os.W_OK, dir_fd=self.fd, follow_symlinks=False)
        return os.access(self.real / name, os.W_OK)

    def write_temp(
        self, name: str, data: bytes, like: os.stat_result | None, *, in_place: bool = True
    ) -> str | None:
        """``data`` in a new temp file beside ``name`` (fsynced; the mode and
        owner of ``like``, else a new file's mode under the umask). None when
        the folder takes no new file or the owner cannot be kept: the caller
        then writes in place. With ``in_place=False`` those raise instead (the
        ``OSError``, or ``OwnerNotKeptError``), and no temp file is left."""
        # The temp name keeps at most 64 bytes of the entry's, so a long entry
        # name (230+ bytes) still has a temp name under the 255-byte limit.
        stem = name.encode("utf-8")[:64].decode("utf-8", "ignore")
        tmp = f".{stem}.{secrets.token_hex(8)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW
        try:
            fd = os.open(self._p(tmp), flags, 0o600 if like else 0o666, **self._at(tmp))
        except OSError as e:
            # A folder that takes no new file, or (a filesystem with a shorter
            # limit) a temp name too long: an existing file is written in place.
            if (
                like is None
                or not in_place
                or not (isinstance(e, PermissionError) or e.errno == errno.ENAMETOOLONG)
            ):
                raise
            return None
        try:
            try:
                if like is not None:
                    os.fchmod(fd, stat.S_IMODE(like.st_mode))
                    mine = os.fstat(fd)
                    if hasattr(os, "fchown") and (
                        os.geteuid() == 0
                        or (mine.st_uid, mine.st_gid) != (like.st_uid, like.st_gid)
                    ):
                        try:
                            os.fchown(fd, like.st_uid, like.st_gid)
                        except OSError as e:
                            os.close(fd)
                            fd = -1
                            self.remove(tmp)
                            if not in_place:
                                raise OwnerNotKeptError(like.st_uid, like.st_gid) from e
                            return None
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view) :]
                os.fsync(fd)
            finally:
                if fd >= 0:
                    os.close(fd)
        except BaseException:
            self.remove(tmp)
            raise
        return tmp

    def write_in_place(self, name: str, data: bytes, why: str, checked: os.stat_result) -> None:
        """Write ``data`` into the file the caller checked (``checked``), and
        only that inode. The name is opened ``O_NOFOLLOW | O_NONBLOCK`` (a
        FIFO swapped in cannot hang the lock) and the descriptor must be a
        regular file with one link and ``checked``'s ``st_dev``/``st_ino``:
        a hard link to another file swapped in between the check and the
        open is refused (``HardLinked``), any other file is ``FileChanged``.
        Nothing is truncated before that."""
        logger.warning("Writing %s in place, not atomically: %s", self.label(name), why)
        try:
            fd = os.open(self._p(name), os.O_WRONLY | _NOFOLLOW | os.O_NONBLOCK, **self._at(name))
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise KBFileOutsideRootError() from e
            if e.errno in (errno.ENXIO, errno.ENOENT, errno.EISDIR):
                raise FileChanged(self.real / name, None) from e
            raise
        try:
            st = os.fstat(fd)
            if st.st_nlink > 1:
                raise HardLinked(self.label(name), st.st_nlink)
            if not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) != (
                checked.st_dev,
                checked.st_ino,
            ):
                raise FileChanged(self.real / name, None)
        except BaseException:
            os.close(fd)
            raise
        with os.fdopen(fd, "wb") as f:
            f.truncate(0)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

    def rename(self, tmp: str, name: str) -> None:
        """Put ``tmp`` at ``name``; never follows ``name``."""
        if self.fd is not None:
            os.rename(tmp, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        else:
            os.replace(self.real / tmp, self.real / name)

    def link_exclusive(self, tmp: str, src: Folder, name: str) -> None:
        """Give ``src``'s ``tmp`` the name ``name`` here, refusing
        (``FileExistsError``) when the name is taken: a hard link, which is
        atomic; where the filesystem has none, claim the name with ``O_EXCL``
        and rename over the claim.

        Publish exactly the new file at ``name`` and leave nothing behind the
        caller cannot name, or raise with ``name`` as it was and ``tmp`` still
        the caller's. A link can be made and still raise (EIO after the link,
        or EEXIST on an NFS retransmit of a link that succeeded): either error
        first checks whether ``name`` now names ``tmp``'s file and treats that
        as the publish it is (#914). The O_EXCL placeholder is removed on a
        failure only while it is still ours (the claim's inode, or empty when
        that could not be read; #477, #914)."""
        try:
            if self.fd is not None:
                os.link(tmp, name, src_dir_fd=src.fd, dst_dir_fd=self.fd, follow_symlinks=False)
            else:
                os.link(src.real / tmp, self.real / name)
        except FileExistsError:
            if self._names_the_same_file(src, tmp, name):
                src.unlink_published(tmp, self.label(name))
                return
            raise
        except OSError:
            # EIO can be reported after the link was created (#914). Only when
            # the name is not ours does the O_EXCL fallback run, which is still
            # exclusive and re-raises real errors.
            if self._names_the_same_file(src, tmp, name):
                src.unlink_published(tmp, self.label(name))
                return
        else:
            src.unlink_published(tmp, self.label(name))
            return
        try:
            claim = os.open(
                self._p(name), os.O_CREAT | os.O_EXCL | os.O_WRONLY | _NOFOLLOW, **self._at(name)
            )
        except FileExistsError:
            raise
        except OSError as e:
            if e.errno not in _UNSUPPORTED:
                raise
            claim = None
        # From here the placeholder at ``name`` is ours: everything up to a
        # successful rename runs inside one cleanup scope.
        claimed: os.stat_result | None = None
        try:
            if claim is not None:
                try:
                    claimed = os.fstat(claim)
                finally:
                    os.close(claim)
            if self.fd is not None:
                os.rename(tmp, name, src_dir_fd=src.fd, dst_dir_fd=self.fd)
            else:
                os.replace(src.real / tmp, self.real / name)
        except BaseException:
            if claim is not None:
                with contextlib.suppress(OSError):
                    st = self.lstat(name)
                    ours = st is not None and (
                        os.path.samestat(claimed, st) if claimed is not None else st.st_size == 0
                    )
                    if ours:
                        self.remove(name)
            raise

    def _names_the_same_file(self, src: Folder, tmp: str, name: str) -> bool:
        """Whether ``name`` here now names the inode ``src``'s ``tmp`` holds."""
        try:
            a, b = src.lstat(tmp), self.lstat(name)
        except OSError:
            return False
        return a is not None and b is not None and os.path.samestat(a, b)

    def unlink_published(self, tmp: str, published: str) -> None:
        """Remove the temp name after a successful publish; one retry for a
        transient sharing error, then a warning (a second hard link blocks
        ``ids pin`` and keeps today's bytes past the next update, #752)."""
        try:
            self.unlink(tmp)
        except FileNotFoundError:
            return
        except OSError:
            try:
                self.unlink(tmp)
            except FileNotFoundError:
                return
            except OSError:
                logger.warning(
                    "published %s, but could not remove its temp name %s, a second "
                    "hard link to the same file; delete it by hand",
                    published,
                    self.label(tmp),
                    exc_info=True,
                )

    def unlink(self, name: str) -> None:
        os.unlink(self._p(name), **self._at(name))

    def remove(self, name: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.unlink(name)

    def fsync(self) -> None:
        if self.fd is None:
            return
        with contextlib.suppress(OSError):  # a filesystem that refuses a directory fsync
            os.fsync(self.fd)


class KBFiles:
    """Writes, moves and removes the files of one KB, strictly inside its
    resolved root."""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
        #: The root resolved once, when this object is made (one per write):
        #: every decision and every open of that write use the same answer.
        self.real_root = os.path.realpath(self.root)

    def _absolute(self, path: str | os.PathLike[str]) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.root / p

    def contains(self, path: str | os.PathLike[str]) -> bool:
        """Whether ``path`` resolves inside the root (no I/O beyond resolving),
        against the root as resolved for this write."""
        try:
            real = os.path.realpath(self._absolute(path))
        except (OSError, ValueError, RuntimeError):
            return False
        return _parts_below(self.real_root, real) is not None

    def check(self, path: str | os.PathLike[str], what: str = "file") -> bool:
        """``contains``, logging once per file when not."""
        if self.contains(path):
            return True
        return inside_or_warn(self.root, self._absolute(path), what)

    def require(self, path: str | os.PathLike[str]) -> None:
        """Raise ``KBFileOutsideRootError`` unless ``path`` resolves inside the root."""
        self._rel_resolved(path)

    def _rel_resolved(self, path: str | os.PathLike[str]) -> tuple[str, ...]:
        """Components of the fully resolved ``path`` below the root."""
        real = os.path.realpath(self._absolute(path))
        parts = _parts_below(self.real_root, real)
        if parts is None:
            logger.warning("Refusing %s: it resolves outside the KB root %s", path, self.root)
            raise KBFileOutsideRootError()
        return parts

    def _rel_named(self, path: str | os.PathLike[str]) -> tuple[str, ...]:
        """Components of ``path`` as a name: its parent resolved, its last
        component kept (what unlink and move act on). The target it resolves
        to must also be inside the root."""
        p = self._absolute(path)
        if not self.contains(p):
            logger.warning("Refusing %s: it resolves outside the KB root %s", path, self.root)
            raise KBFileOutsideRootError()
        parts = _parts_below(self.real_root, os.path.realpath(p.parent))
        if parts is None or p.name in ("", ".", ".."):
            logger.warning("Refusing %s: its folder resolves outside the KB root", path)
            raise KBFileOutsideRootError()
        return (*parts, p.name)

    # -- opening beneath the root ---------------------------------------------

    def _open_dir(self, parts: tuple[str, ...], *, create: bool = False) -> int:
        fd = os.open(self.real_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            for name in parts:
                try:
                    nfd = os.open(name, os.O_RDONLY | _DIR_FLAGS, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    with contextlib.suppress(FileExistsError):
                        os.mkdir(name, 0o777, dir_fd=fd)
                    nfd = os.open(name, os.O_RDONLY | _DIR_FLAGS, dir_fd=fd)
                except OSError as e:
                    if e.errno in (errno.ELOOP, errno.ENOTDIR):
                        logger.warning("Refusing a path under %s: a folder changed", self.root)
                        raise KBFileOutsideRootError() from e
                    raise
                os.close(fd)
                fd = nfd
            return fd
        except BaseException:
            os.close(fd)
            raise

    @contextlib.contextmanager
    def folder(
        self, path: str | os.PathLike[str], *, named: bool = False, create: bool = False
    ) -> Iterator[tuple[Folder, str]]:
        """``path``'s folder, opened beneath the root without following a
        link, and the file's name in it: the resolved file (a link inside
        written through), or with ``named`` the name itself (what a move or
        a removal acts on). ``create`` makes missing folders, beneath the
        root."""
        parts = self._rel_named(path) if named else self._rel_resolved(path)
        if not parts:
            raise IsADirectoryError("the KB's folder itself is not a file")
        rel = parts[:-1]
        real = Path(self.real_root, *rel)
        if not _HAS_DIR_FD:
            if create:
                real.mkdir(parents=True, exist_ok=True)
            yield Folder(None, real, rel), parts[-1]
            return
        fd = self._open_dir(rel, create=create)
        try:
            yield Folder(fd, real, rel), parts[-1]
        finally:
            os.close(fd)

    # -- the write rules ----------------------------------------------------

    def require_writable(self, path: str | os.PathLike[str]) -> None:
        """Refuse a write of ``path`` unless it resolves inside the root and
        is not a file with more than one hard link (``HardLinked``). A check
        for a caller's early answer; the write decides again."""
        parts = self._rel_resolved(path)
        if not parts:
            raise IsADirectoryError("the KB's folder itself is not a file")
        try:
            links = os.stat(self._absolute(path)).st_nlink
        except FileNotFoundError:
            return
        if links > 1:
            raise HardLinked("/".join(parts), links)

    def require_movable(self, src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        """Refuse a move of ``src`` to ``dst`` unless ``src`` is no symbolic
        link and is writable, its folder is inside the root, and ``dst`` is
        writable. A check for a caller's early answer; the move decides again."""
        self._rel_named(src)
        try:
            if stat.S_ISLNK(os.lstat(self._absolute(src)).st_mode):
                raise KBFileIsLinkError()
        except FileNotFoundError:
            pass
        self.require_writable(src)
        self.require_writable(dst)

    def require_removable(self, path: str | os.PathLike[str]) -> None:
        """Refuse removing ``path`` unless the file it names and the folder it
        is named in resolve inside the root."""
        self._rel_named(path)

    # -- the writes -----------------------------------------------------------

    @staticmethod
    def _locked(real: list[Path], lock_timeout: float, step) -> bool:
        """Run ``step`` under the momentary lock of ``real``'s paths; outside
        git, without it. Returns whether it ran locked."""
        try:
            with file_lock(*real, timeout=lock_timeout):
                step()
            return True
        except NotInGitRepository:
            step()
            return False

    def write_bytes(
        self,
        path: str | os.PathLike[str],
        data: bytes,
        *,
        expect: bytes | object | None = None,
        expect_folder: tuple[int, int] | None = None,
        in_place: bool = True,
        lock_timeout: float = LOCK_TIMEOUT,
    ) -> bool:
        """Replace (or, with ``expect=ABSENT``, create) the file at ``path``
        with ``data``, inside the root; return whether it ran under the lock.

        ``expect`` bytes: the file must still hold them (``FileChanged``
        otherwise, nothing written). ``ABSENT``: the name must be free
        (``FileExistsError``); the publish is an exclusive link, so two creates
        racing give one file and one refusal. ``None``: no compare and no lock.
        ``expect_folder``: the ``(st_dev, st_ino)`` of the folder the caller
        checked; another folder at that path is ``KBFolderChanged``.
        A file with more than one hard link is refused (``HardLinked``), so is
        one this process may not write (``PermissionError``).
        ``in_place=False``: never fall back to writing the file in place; a
        temp file that cannot be made, or given the original's owner and
        group, is a refusal (the ``OSError``, ``OwnerNotKeptError``) instead.
        """
        if expect is not None:
            check_timeout(lock_timeout)
        creating = expect is ABSENT
        with self.folder(path, create=creating) as (d, name):
            if expect_folder is not None and d.fd is not None:
                here = os.fstat(d.fd)
                if (here.st_dev, here.st_ino) != expect_folder:
                    raise KBFolderChanged(d.label(name), None)
            like = d.lstat(name)
            if like is not None and not creating:
                self._refuse_unwritable(d, name, like)
            tmp = d.write_temp(name, data, None if creating else like, in_place=in_place)
            real = d.real / name

            def step() -> None:
                st = d.lstat(name)
                if creating:
                    if st is not None:
                        raise FileExistsError(d.label(name))
                    assert tmp is not None  # a new file: write_temp raises rather than None
                    d.link_exclusive(tmp, d, name)
                    return
                if st is not None:
                    self._refuse_unwritable(d, name, st)
                if expect is not None:
                    on_disk = d.read(name)
                    if on_disk != expect:
                        raise FileChanged(real, on_disk)
                if tmp is None:
                    if st is None:
                        raise FileChanged(real, None)
                    d.write_in_place(
                        name, data, "the folder takes no new file, or the owner", checked=st
                    )
                else:
                    d.rename(tmp, name)

            try:
                if expect is None:
                    step()
                    locked = False
                else:
                    locked = self._locked([real], lock_timeout, step)
            except BaseException:
                if tmp is not None:
                    d.remove(tmp)
                raise
            d.fsync()
            return locked

    @staticmethod
    def _refuse_unwritable(d: Folder, name: str, st: os.stat_result) -> None:
        if stat.S_ISLNK(st.st_mode):  # swapped in after the resolve
            raise KBFileOutsideRootError()
        if not stat.S_ISREG(st.st_mode):
            raise IsADirectoryError(f"{d.label(name)} is not a regular file")
        if st.st_nlink > 1:
            raise HardLinked(d.label(name), st.st_nlink)
        if not d.writable(name):
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), d.label(name))

    def move_bytes(
        self,
        src: str | os.PathLike[str],
        expect: bytes,
        dst: str | os.PathLike[str],
        data: bytes,
        *,
        lock_timeout: float = LOCK_TIMEOUT,
    ) -> bool:
        """Publish ``data`` at ``dst`` (never over a file there:
        ``FileExistsError``) and remove ``src``, which must still hold
        ``expect`` (``FileChanged``), as one write under one lock for both
        paths; return whether it ran locked. ``src`` is the name itself: a
        link there is refused (``KBFileIsLinkError``), never followed to its
        target. ``dst``'s folders are made beneath the root."""
        check_timeout(lock_timeout)
        self.require_movable(src, dst)
        with (
            self.folder(src, named=True) as (s, sname),
            self.folder(dst, create=True) as (t, tname),
        ):
            tmp = t.write_temp(tname, data, None)
            assert tmp is not None  # a new file: write_temp raises rather than None

            def step() -> None:
                st = s.lstat(sname)
                if st is None:
                    raise FileChanged(s.real / sname, None)
                if stat.S_ISLNK(st.st_mode):
                    raise KBFileIsLinkError()
                self._refuse_unwritable(s, sname, st)
                on_disk = s.read(sname)
                if on_disk != expect:
                    raise FileChanged(s.real / sname, on_disk)
                t.link_exclusive(tmp, t, tname)
                s.unlink(sname)

            try:
                locked = self._locked([s.real / sname, t.real / tname], lock_timeout, step)
            except BaseException:
                t.remove(tmp)
                raise
            t.fsync()
            s.fsync()
            return locked

    def move(self, src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        """Rename ``src`` to ``dst`` as it is (same inode), both inside the
        root, never replacing an existing ``dst`` (``FileExistsError``) and
        never moving a link as its target. ``dst``'s folders are made."""
        self.require_movable(src, dst)
        with (
            self.folder(src, named=True) as (s, sname),
            self.folder(dst, create=True) as (t, tname),
        ):
            st = s.lstat(sname)
            if st is not None and stat.S_ISLNK(st.st_mode):
                raise KBFileIsLinkError()
            if t.fd is not None:
                os.link(sname, tname, src_dir_fd=s.fd, dst_dir_fd=t.fd, follow_symlinks=False)
            else:
                os.link(s.real / sname, t.real / tname)
            try:
                s.unlink(sname)
            except BaseException:
                t.unlink(tname)  # one name, not two
                raise

    def unlink(self, path: str | os.PathLike[str], *, missing_ok: bool = True) -> None:
        """Remove the name ``path`` (a link as a link), inside the root."""
        with self.folder(path, named=True) as (d, name):
            try:
                d.unlink(name)
            except FileNotFoundError:
                if not missing_ok:
                    raise
