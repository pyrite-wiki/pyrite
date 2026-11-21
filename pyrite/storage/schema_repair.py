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

**A non-constant default needs an empty table.** SQLite's ``ALTER TABLE ADD
COLUMN`` accepts ``DEFAULT CURRENT_TIMESTAMP``/``CURRENT_DATE``/``CURRENT_TIME``
and a parenthesised expression that is not a literal while the table is empty,
then refuses them once it has a row (``Cannot add a column with non-constant
default``); a ``STORED`` generated column is refused the same way (``cannot add
a STORED column``). The
model's timestamps declare ``CURRENT_TIMESTAMP``, so a missing one on a table
with rows is detected while planning and refused with the rebuild suggestion
(#1318). Counting a table's rows happens only when the plan has such a column
for it, and an empty table still takes the column.

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

**The lock file is never deleted, on purpose.** A process waiting on the lock
holds the old file open; if the holder unlinked it on release, the next opener
would create a new file at the same path and lock *that*, and two processes
would hold "the" lock at once. An empty ``<index>.schema-lock`` left beside the
index is the price; do not clean it up.

**Why it fails open, unlike `pyrite/utils/file_lock.py`.** That lock guards a
compare-and-replace of a user's file, where running unlocked could lose data,
so it refuses (fails closed). This one only stops two openers racing to the same
idempotent DDL; unlocked, the worst case is the pre-#1302 crash of one opener.
So where no lock can be had (Windows, a read-only directory, a filesystem
without ``flock``) the open runs unlocked rather than refusing every command.

**Every SQLite fault is classified by ``storage_error_from``** (`errors.py`):
a lock is ``StorageBusyError`` (retryable, "wait and run it again"), a read-only
or full disk gets the disk suggestion, and only a schema this build cannot
reach in place is told to rebuild, with `INDEX_ONLY_STATE` named, since the
index file is the only home of ``kb add`` registrations, accounts, stars and
reviews.

**Default mode writes before it can refuse.** ``create_all``, the virtual
tables and this build's migrations run before the repair plans, so a refused
index has been changed by the open that refused it (the message says which
versions were applied). Nothing-written-before-refusal is the #718 explicit
mode's job, which must check before ``create_all``.

**Added columns carry no ``REFERENCES`` clause.** ``CreateColumn`` compiles the
column alone (``kb.repo_id`` becomes ``repo_id INTEGER``), so a foreign key the
repair adds has no ``ON DELETE`` action, unlike a table ``create_all`` made.
A declared ``UNIQUE`` index that fails on duplicate rows rolls the whole plan
back and refuses the open with `REPAIR_FAILED_SUGGESTION`.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import re
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Column, MetaData
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateColumn, CreateIndex, DefaultClause

from ..exceptions import IndexSchemaRefusedError, StorageBusyError
from .errors import INDEX_ONLY_STATE, storage_error_from

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
    "This Pyrite version cannot upgrade the index in place. Run `pyrite db backup` "
    "first, to keep a copy. Then move the index file aside and run `pyrite index "
    f"build`. The rebuild restores the entries from the markdown files, not the "
    f"{INDEX_ONLY_STATE}: they stay in the backup."
)

MIGRATION_FAILED_SUGGESTION = (
    "The index is intact: each finished migration step is kept, and the next open "
    "resumes from there. The log names the step's error. If the disk is writable and "
    "has space, report that line as a Pyrite bug; do not rebuild, which would lose the "
    f"{INDEX_ONLY_STATE}."
)

REPAIR_FAILED_SUGGESTION = (
    "The index is unchanged: the repair is applied as one transaction and rolled "
    "back. The log names the error. If the disk is writable and has space, report "
    f"that line as a Pyrite bug; do not rebuild, which would lose the {INDEX_ONLY_STATE}."
)

_DIALECT = sqlite_dialect.dialect()

#: A DEFAULT SQLite calls constant: a literal, optionally in parentheses.
#: ``CURRENT_TIMESTAMP``/``DATE``/``TIME`` are not literals to ALTER TABLE ADD
#: COLUMN, and neither is a function call such as ``(datetime('now'))``.
_LITERAL_DEFAULT = re.compile(
    r"""(?ix)
    (?: NULL | TRUE | FALSE
      | [+-]? (?: \d+(?:\.\d*)? | \.\d+ ) (?: e [+-]? \d+ )?
      | 0x[0-9a-f]+
      | '(?:[^']|'')*'
      | x'[0-9a-f]*'
    )
    """
)


def _unparenthesized(value: str) -> str:
    """``value`` without balanced parentheses that wrap the whole expression.

    ``((1))`` becomes ``1``; ``(1) + (2)`` stays as it is, since its first
    parenthesis closes before the end. A quote is skipped, so a parenthesis in
    a string literal does not count.
    """
    value = value.strip()
    while len(value) > 1 and value[0] == "(" and value[-1] == ")":
        depth = 0
        in_string = False
        for position, char in enumerate(value):
            if in_string:
                if char == "'":
                    in_string = False
                continue
            if char == "'":
                in_string = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and position != len(value) - 1:
                    return value
        if depth != 0:
            return value
        value = value[1:-1].strip()
    return value


def _has_constant_default(column: Column) -> bool:
    """Whether SQLite accepts this column's DEFAULT on a table that has rows.

    A ``server_default`` string is quoted by SQLAlchemy (``"active"`` renders
    ``DEFAULT 'active'``), so it is always a literal. A raw SQL default
    (``text(...)``) counts only when it is a literal, optionally parenthesised.
    Any other SQL expression is treated as non-constant: the repair refuses
    rather than meet SQLite's refusal mid-transaction.
    """
    default = column.server_default
    if default is None:
        return True
    if not isinstance(default, DefaultClause):
        # `FetchedValue` marks a value the database supplies on insert or
        # update; it emits no DEFAULT in this column's DDL.
        return True
    arg = default.arg
    if isinstance(arg, str):
        return True
    sql = getattr(arg, "text", None)
    if not isinstance(sql, str):
        return False
    return _LITERAL_DEFAULT.fullmatch(_unparenthesized(sql)) is not None


def _needs_empty_table(column: Column) -> bool:
    """SQLite refuses this ALTER only while the table it alters has rows."""
    computed = column.computed
    if computed is not None:
        return bool(computed.persisted)
    return not _has_constant_default(column)


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


def describe_state(conn: sqlite3.Connection, version_at_open: int | None) -> str:
    """What this open found and did, in a refusal: the recorded version before
    the open and, when this open's migrations moved it, the version now."""
    now = recorded_version(conn)
    if version_at_open is None:
        version_at_open = now
    if version_at_open is None:
        return "The index (no recorded schema version)"
    if now is not None and now != version_at_open:
        return (
            f"The index was at schema version {version_at_open}; this open applied the "
            f"migrations to {now}, and then"
        )
    return f"The index (schema version {version_at_open})"


def repair_schema(
    conn: sqlite3.Connection,
    metadata: MetaData | None = None,
    *,
    version_at_open: int | None = None,
) -> list[str]:
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
    # Non-constant defaults and STORED generated columns are addable only to an
    # empty table; keep them apart so the refusal can say why.
    cannot_add_to_rows: list[str] = []
    add_indexes: list[tuple[str, str]] = []  # (name, DDL)
    has_rows: dict[str, bool] = {}

    def table_has_rows(name: str) -> bool:
        if name not in has_rows:
            has_rows[name] = (
                conn.execute(f'SELECT EXISTS(SELECT 1 FROM "{name}")').fetchone()[0] == 1
            )
        return has_rows[name]

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
            if _needs_empty_table(column) and table_has_rows(table.name):
                cannot_add_to_rows.append(f"{table.name}.{column.name}")
                continue
            ddl = str(CreateColumn(column).compile(dialect=_DIALECT))
            add_columns.append((table.name, column.name, ddl))
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            if index.name and index.name not in indexes_on_disk:
                ddl = str(CreateIndex(index, if_not_exists=True).compile(dialect=_DIALECT))
                add_indexes.append((index.name, ddl))

    if cannot_add or cannot_add_to_rows:
        state = describe_state(conn, version_at_open)
        verb = "lacks" if state.startswith("The index (") else "found it lacks"
        why = ""
        if cannot_add_to_rows:
            why = (
                " SQLite cannot add a column with a non-constant default, or a "
                "stored generated column, to a table that has rows."
            )
        err = IndexSchemaRefusedError(
            f"{state} {verb} {', '.join(cannot_add + cannot_add_to_rows)}, which this "
            f"Pyrite version cannot add in place, so the index is refused.{why}"
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
        raise storage_error_from(
            exc,
            f"{describe_state(conn, version_at_open)} needs {', '.join(added)}, and adding "
            "them failed; nothing was changed.",
            cls=IndexSchemaRefusedError,
            with_detail=False,
            suggestion=REPAIR_FAILED_SUGGESTION,
        ) from exc
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
