"""Back up the SQLite index, refusing one that is corrupt (#922)."""

import sqlite3
from pathlib import Path

from ..exceptions import StorageError
from ..storage.connection import CORRUPT_INDEX_SUGGESTION, index_file_problem


def backup_index(db_path: Path, output_path: Path) -> None:
    """Copy ``db_path`` to ``output_path`` with SQLite's backup API.

    Raises ``StorageError`` (with the rebuild suggestion) for an index SQLite
    cannot read or that fails its checks.
    """
    source_conn = None
    dest_conn = None
    try:
        source_conn = sqlite3.connect(str(db_path))
        dest_conn = sqlite3.connect(str(output_path))
        # The same header rule the opener applies (a WAL or a stale count
        # exempts the file), then SQLite's own check.
        problem = index_file_problem(db_path)
        if problem:
            raise sqlite3.DatabaseError(problem)
        verdict = source_conn.execute("PRAGMA integrity_check").fetchone()
        if verdict is None or verdict[0] != "ok":
            raise sqlite3.DatabaseError(
                f"integrity_check: {verdict[0] if verdict else 'no result'}"
            )
        source_conn.backup(dest_conn)
    except sqlite3.Error as exc:
        # This command opens the index itself rather than through PyriteDB, so
        # a corrupt index.db reaches here as a raw sqlite3 error; refuse it as
        # every other command does (#922), and do not leave a half-written
        # backup that looks like one.
        if dest_conn is not None:
            dest_conn.close()
            dest_conn = None
        output_path.unlink(missing_ok=True)
        err = StorageError(f"Cannot read index database at {db_path}: {exc}")
        err.suggestion = CORRUPT_INDEX_SUGGESTION
        raise err from exc
    finally:
        if dest_conn is not None:
            dest_conn.close()
        if source_conn is not None:
            source_conn.close()
