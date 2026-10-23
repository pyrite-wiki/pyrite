"""Bring an index's tables to the model on every open (#1260), under one lock (#1302).

**Why a repair, beside the numbered migrations.** ``schema_version`` records
which migrations *this* file has run, but a number means what the build that
wrote it said it meant. Twice a migration number was taken by two branches at
once (v28/v29 on 2026-10-15; ``arch/2026-10`` used 28 for ``entry.aliases``
until the sprint merge renumbered it to 29 and gave 28 to
``link.target_prefix``). An index written by such a build records 28 and lacks
the column dev's 28 adds, so the migrations never run again and every read of
``link.target_prefix`` fails. So after the migrations, every open compares each
model table (``Base.metadata``) with what is on disk and adds what is missing:
the columns, then the indexes the model declares. A renumbered or half-applied
migration can no longer strand an index.

**What it will not do.** It only adds. A column SQLite cannot add in place (a
key, ``UNIQUE``, ``NOT NULL`` with no server default) refuses the open with
`IndexSchemaRefusedError` before any change, and so does a database that
refuses the write (read-only, full). Columns on disk that the model lacks are
left alone: an older build may still read them. Tables the model does not
declare (FTS, sqlite-vec, plugin tables, the raw-SQL tables of migrations
v19/v22/v23) are not its business.

**After the migrations, never before.** Run first, it would add
``starred_entry.user_id`` to a pre-v24 table, and v24 (which rebuilds the
table to change its unique constraint) would see the column and skip the
rebuild. And in the default mode a refusal comes after ``create_all`` and the
migrations have written: the repair plans before *it* writes, but only the
#718 explicit mode can refuse with the file untouched, by checking before
``create_all``.

**Kept apart from ``create_all``.** ``create_all`` adds missing *tables*; this
adds missing columns and indexes inside tables that exist. The #718 explicit
upgrade mode needs to refuse before either writes, so each is its own step in
``ConnectionMixin._prepare_schema``.

**The lock.** Two processes opening one old index (an MCP server and a CLI
command) both saw the version behind and both ran the migration; one of them
then failed with ``duplicate column name`` (#1302). ``schema_lock`` serialises
the whole schema step across processes with ``flock`` on a sibling file, so the
second process waits, then finds the index current and does nothing. The kernel
drops the lock when its holder dies, so a killed process leaves no stale lock.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import MetaData
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateColumn, CreateIndex

from ..exceptions import IndexSchemaRefusedError, StorageBusyError

try:
    import fcntl
except ImportError:  # Windows: no flock; the open runs unlocked (see schema_lock)
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

SCHEMA_LOCK_SUFFIX = ".schema-lock"

SCHEMA_LOCK_TIMEOUT = 300.0
"""Seconds an open waits for another process's schema step. A migration that
rebuilds a table (v24) on a large index takes seconds, not minutes; past this
the holder is stopped or hung, and the open fails with `StorageBusyError`
(retryable) instead of hanging the caller."""

REBUILD_SUGGESTION = (
    "Move the index file aside and run `pyrite index build` to rebuild the index "
    "from the markdown files."
)

WRITE_REFUSED_SUGGESTION = (
    "Check that the index file and its directory are writable and the disk has "
    "space, then run the command again. Or move the index file aside and run "
    "`pyrite index build` to rebuild the index from the markdown files."
)

_DIALECT = sqlite_dialect.dialect()


def _models() -> MetaData:
    from .models import Base

    return Base.metadata


def recorded_version(conn: sqlite3.Connection) -> int | None:
    """The highest version in ``schema_version``, or None without the table."""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def _version_text(conn: sqlite3.Connection) -> str:
    version = recorded_version(conn)
    return f"schema version {version}" if version is not None else "an unrecorded schema version"


def repair_schema(conn: sqlite3.Connection, metadata: MetaData | None = None) -> list[str]:
    """Add every model column and declared index missing from tables on disk.

    Returns what was added (``table.column`` and ``index <name>``), empty when
    the index already matched, in which case nothing was written. Plans first
    and refuses before any change if one column cannot be added; applies the
    plan in one ``BEGIN IMMEDIATE`` transaction, so a failure leaves the file as
    it was and the next open tries again.

    A model table absent from the file is skipped, never created (that is
    ``create_all``'s step): migrations are tested on bare connections
    (``tests/test_migrations.py``), where a step that assumes the model's
    tables broke 11 tests in the #1260 spike.
    """
    metadata = metadata if metadata is not None else _models()
    tables_on_disk = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    indexes_on_disk = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }

    add_columns: list[tuple[str, str, str]] = []  # (table, column, DDL)
    cannot_add: list[str] = []
    add_indexes: list[tuple[str, str]] = []  # (name, DDL)
    for table in metadata.sorted_tables:
        if table.name not in tables_on_disk:
            continue
        # table_xinfo, not table_info: table_info hides generated columns, and
        # a column that is there but hidden must not be added a second time.
        have = {row[1] for row in conn.execute(f'PRAGMA table_xinfo("{table.name}")')}
        for column in table.columns:
            if column.name in have:
                continue
            # SQLite's ALTER TABLE ADD COLUMN cannot add a key or a UNIQUE
            # column, and needs a default for NOT NULL (sqlite.org/lang_altertable.html).
            addable = not column.primary_key and not column.unique
            addable = addable and (column.nullable or column.server_default is not None)
            if not addable:
                cannot_add.append(f"{table.name}.{column.name}")
                continue
            ddl = str(CreateColumn(column).compile(dialect=_DIALECT))
            add_columns.append((table.name, column.name, ddl))
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            if index.name and index.name not in indexes_on_disk:
                ddl = str(CreateIndex(index, if_not_exists=True).compile(dialect=_DIALECT))
                add_indexes.append((index.name, ddl))

    if cannot_add:
        err = IndexSchemaRefusedError(
            f"The index ({_version_text(conn)}) was built by another Pyrite version: "
            f"{', '.join(cannot_add)} cannot be added to it in place."
        )
        err.suggestion = REBUILD_SUGGESTION
        raise err

    if not add_columns and not add_indexes:
        return []

    added = [f"{t}.{c}" for t, c, _ in add_columns] + [f"index {n}" for n, _ in add_indexes]
    if conn.in_transaction:
        conn.commit()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table_name, _column, ddl in add_columns:
            conn.execute(f'ALTER TABLE "{table_name}" ADD COLUMN {ddl}')
        for _name, ddl in add_indexes:
            conn.execute(ddl)
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        # The driver's text is logged, not shown: it is not safe by
        # construction (IndexSchemaRefusedError's contract).
        logger.warning("Index schema repair failed adding %s: %s", ", ".join(added), exc)
        err = IndexSchemaRefusedError(
            f"The index ({_version_text(conn)}) needs {', '.join(added)}, "
            "and the database refused the change."
        )
        err.suggestion = WRITE_REFUSED_SUGGESTION
        raise err from exc
    logger.info("Repaired the index schema: added %s", ", ".join(added))
    return added


@contextlib.contextmanager
def schema_lock(db_path: Path, timeout: float | None = None) -> Iterator[None]:
    """Hold an exclusive cross-process lock for the schema step of an open.

    The lock is ``flock`` on ``<index file><SCHEMA_LOCK_SUFFIX>`` beside the
    index. Every waiter opens its own file description, so threads of one
    process exclude each other too (Linux, macOS). Waits are bounded by
    ``timeout`` and end in `StorageBusyError`.

    Runs unlocked, as before #1302, where no lock can be had: Windows (no
    ``flock``), a lock file that cannot be created or opened (a read-only
    directory, where no process can migrate the index either), or a filesystem
    without ``flock`` (``ENOLCK``/``ENOTSUP``, some network mounts).
    """
    if fcntl is None:
        yield
        return
    lock_path = db_path.with_name(db_path.name + SCHEMA_LOCK_SUFFIX)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd = None
    for flags in (os.O_RDWR | os.O_CREAT, os.O_RDONLY):
        try:
            fd = os.open(lock_path, flags | nofollow, 0o644)
            break
        except OSError:
            continue
    if fd is None:
        logger.debug("No schema lock at %s; opening the index unlocked", lock_path)
        yield
        return
    try:
        # Read at call time, so a test can shorten it on the module.
        deadline = time.monotonic() + (SCHEMA_LOCK_TIMEOUT if timeout is None else timeout)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    err = StorageBusyError(
                        "Another Pyrite process is still upgrading the index schema."
                    )
                    err.suggestion = (
                        "Wait for the other Pyrite process, then run the command again."
                    )
                    raise err from None
                time.sleep(0.01)
            except OSError as exc:
                if exc.errno in (errno.ENOLCK, errno.ENOTSUP, errno.EOPNOTSUPP):
                    logger.debug("flock unsupported at %s; opening the index unlocked", lock_path)
                    os.close(fd)
                    fd = None
                    break
                raise
        yield
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
