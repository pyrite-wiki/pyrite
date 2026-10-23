"""Back up the SQLite index, refusing one that is corrupt (#922).

`REBUILD_SUGGESTION` sends a user here before any rebuild, so this command's
own refusals must be exact (#1260 round 2): a fault of the backup's
destination never says the index is unreadable, a lock on either file is
retryable, and the advice to move the index aside is given only for an index
that is corrupt.
"""

import sqlite3
import time
from pathlib import Path

from ..exceptions import StorageError
from ..storage import connection
from ..storage.connection import CORRUPT_INDEX_SUGGESTION, index_file_problem, is_index_corruption
from ..storage.errors import storage_error_from

#: A fault of the output path: its directory or the file is not writable, the
#: disk is full, or the file there is not a SQLite database.
BACKUP_DESTINATION_SUGGESTION = (
    "Check that the backup's output path can be written (its directory writable, "
    "space on the disk, no other kind of file already there), or choose another "
    "with --output, then run the command again. The index was not changed."
)

#: A fault reading the index that is neither a lock, nor the disk, nor
#: corruption: nothing says a rebuild would help.
BACKUP_RETRY_SUGGESTION = (
    "Run the command again. The index was not changed; `pyrite index health` shows its state."
)


def _corrupt(db_path: Path, detail: str) -> StorageError:
    err = StorageError(f"Cannot read index database at {db_path}: {detail}")
    err.suggestion = CORRUPT_INDEX_SUGGESTION
    return err


def _bounded_progress(timeout_s: float):
    """A ``Connection.backup`` progress callback that stops a copy waiting on a
    lock. Python's backup loop retries SQLITE_BUSY and SQLITE_LOCKED with no
    limit; raising from the callback is the only way out of it."""
    waiting_since: list[float] = []

    def progress(status: int, remaining: int, total: int) -> None:
        if status not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            waiting_since.clear()
            return
        if not waiting_since:
            waiting_since.append(time.monotonic())
        elif time.monotonic() - waiting_since[0] >= timeout_s:
            busy = sqlite3.OperationalError("database is locked")
            busy.sqlite_errorcode = status  # type: ignore[attr-defined]
            raise busy

    return progress


def backup_index(db_path: Path, output_path: Path) -> None:
    """Copy ``db_path`` to ``output_path`` with SQLite's backup API.

    Raises ``StorageError``, with a suggestion for the file at fault:
    `CORRUPT_INDEX_SUGGESTION` for an index that fails its header or SQLite's
    ``integrity_check``, `BACKUP_DESTINATION_SUGGESTION` for an output path
    that cannot take the copy, and ``StorageBusyError`` (retryable) for a lock
    on either. A file that was at ``output_path`` before the call is never
    deleted; one this call created is, so a failed backup leaves nothing that
    looks like one.
    """
    # Read at call time, as the opener's pragma does, so one constant governs
    # every wait on the index's lock.
    timeout_s = connection.SQLITE_BUSY_TIMEOUT_MS / 1000
    created = not output_path.exists()
    source_conn = None
    dest_conn = None
    try:
        # The source: the same header rule the opener applies (a WAL or a
        # stale count exempts the file), then SQLite's own check, which reads
        # every page and so meets a writer's lock here, not in the copy.
        # A directory is refused as the opener refuses it (`_init_connection`).
        problem = "the index path is a directory" if db_path.is_dir() else None
        problem = problem or index_file_problem(db_path)
        if problem:
            raise _corrupt(db_path, problem)
        try:
            source_conn = sqlite3.connect(str(db_path), timeout=timeout_s)
            verdict = source_conn.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.Error as exc:
            # This command opens the index itself rather than through PyriteDB,
            # so a corrupt index.db reaches here as a raw sqlite3 error; refuse
            # it as every other command does (#922).
            raise storage_error_from(
                exc,
                f"Cannot read index database at {db_path}",
                suggestion=(
                    CORRUPT_INDEX_SUGGESTION
                    if is_index_corruption(exc)
                    else BACKUP_RETRY_SUGGESTION
                ),
            ) from exc
        if verdict is None or verdict[0] != "ok":
            raise _corrupt(db_path, f"integrity_check: {verdict[0] if verdict else 'no result'}")

        # The destination. The index has just passed integrity_check, so a
        # fault from here on -- a read-only directory or file, a full disk, a
        # non-database file at the path (SQLITE_NOTADB, a corruption code) --
        # is the output path's.
        try:
            dest_conn = sqlite3.connect(str(output_path), timeout=timeout_s)
            source_conn.backup(dest_conn, progress=_bounded_progress(timeout_s))
        except sqlite3.Error as exc:
            raise storage_error_from(
                exc,
                f"Cannot write the backup to {output_path}",
                suggestion=BACKUP_DESTINATION_SUGGESTION,
                disk_suggestion=BACKUP_DESTINATION_SUGGESTION,
            ) from exc
    except StorageError:
        if dest_conn is not None:
            dest_conn.close()
            dest_conn = None
        if created:
            output_path.unlink(missing_ok=True)
        raise
    finally:
        if dest_conn is not None:
            dest_conn.close()
        if source_conn is not None:
            source_conn.close()
