"""Shared classification of SQLite storage failures, without service imports."""

import errno
import sqlite3

from ..exceptions import StorageBusyError, StorageError

#: SQLite's primary result codes for a transient lock: another connection
#: holds the database (SQLITE_BUSY) or a table (SQLITE_LOCKED).
_TRANSIENT_SQLITE_CODES = frozenset({sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})

#: The same faults by message, for an error that lost ``sqlite_errorcode``
#: (constructed by a wrapper rather than raised by SQLite).
_TRANSIENT_SQLITE_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


#: Faults of the disk or the file's permissions: a rebuild fixes none of them.
_DISK_SQLITE_CODES = frozenset(
    {
        sqlite3.SQLITE_READONLY,
        sqlite3.SQLITE_FULL,
        sqlite3.SQLITE_IOERR,
        sqlite3.SQLITE_CANTOPEN,
        sqlite3.SQLITE_PERM,
    }
)
_DISK_SQLITE_MARKERS = (
    "readonly database",
    "database or disk is full",
    "disk i/o error",
    "unable to open database file",
)

#: What the index holds that `pyrite index build` does not bring back. Every
#: suggestion that moves the index aside names it (#1260 round 1): the index is
#: derived from the markdown files only in part.
INDEX_ONLY_STATE = (
    "KBs added with `pyrite kb add` (add them again), and the users, sessions, API "
    "keys, permissions, stars, reviews, settings and version history kept only in "
    "the index file"
)

BUSY_SUGGESTION = (
    "Another Pyrite process is writing to the index. Wait for it to finish, then run "
    "the command again."
)

DISK_SUGGESTION = (
    "Check that the index file and its directory are writable and that the disk has "
    "space, then run the command again. Nothing in the index was lost."
)


def driver_error(error: BaseException) -> BaseException:
    """The SQLite error under ``error``: SQLAlchemy's ``.orig``, a wrapper's
    ``__cause__`` (``MigrationError``), or ``error`` itself."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, sqlite3.Error):
            return current
        seen.add(id(current))
        orig = getattr(current, "orig", None)
        current = orig if isinstance(orig, BaseException) else current.__cause__
    return error


def _is_transient(error: BaseException) -> bool:
    """Could the same call succeed if made again? Only for a lock (#431)."""
    error = driver_error(error)
    if not isinstance(error, sqlite3.Error):
        return False
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None:
        # Extended codes (SQLITE_BUSY_SNAPSHOT, ...) carry the primary code in
        # the low byte.
        return (code & 0xFF) in _TRANSIENT_SQLITE_CODES
    lowered = str(error).lower()
    return any(marker in lowered for marker in _TRANSIENT_SQLITE_MARKERS)


def is_disk_fault(error: BaseException) -> bool:
    """A read-only or full disk, an I/O error, a file that cannot be opened."""
    error = driver_error(error)
    if isinstance(error, PermissionError):
        return True
    if isinstance(error, OSError) and error.errno in (
        errno.ENOSPC,
        errno.EROFS,
        getattr(errno, "EDQUOT", errno.ENOSPC),
    ):
        return True
    if not isinstance(error, sqlite3.Error):
        return False
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None:
        return (code & 0xFF) in _DISK_SQLITE_CODES
    lowered = str(error).lower()
    return any(marker in lowered for marker in _DISK_SQLITE_MARKERS)


def storage_error_from(
    error: BaseException,
    what: str = "Search failed",
    *,
    cls: type[StorageError] = StorageError,
    with_detail: bool = True,
) -> StorageError:
    """The ``StorageError`` for a sqlite failure that is not the caller's query.

    The one classifier: every site that turns a SQLite error (raw, wrapped by
    SQLAlchemy, or the cause of a ``MigrationError``) into a ``StorageError``
    calls it, and ``tests/test_index_schema_drift.py`` checks that the open,
    migrate, repair and write paths do.

    A lock becomes ``StorageBusyError`` (retryable) with `BUSY_SUGGESTION`,
    whatever ``cls`` asks for. Anything else is ``cls`` (default
    ``StorageError``, not retryable): schema drift, a missing table, corruption.
    ``with_detail=False`` leaves the driver's text out of the message, for a
    class whose message is shown as is (``IndexSchemaRefusedError``).
    """
    root = driver_error(error)
    if _is_transient(root):
        err: StorageError = StorageBusyError(f"{what}: {root}")
        err.suggestion = BUSY_SUGGESTION
        return err
    return cls(f"{what}: {root}" if with_detail else what)
