"""
Database connection setup, extensions, migrations, and plugin tables.

Mixin class providing __init__, close, transaction, and schema management.
"""

from __future__ import annotations

import copy
import logging
import re
import sqlite3
import threading
import warnings
import weakref
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import TypeGuard, cast

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import ExceptionContext
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ..exceptions import IndexSchemaRefusedError, StorageError
from .backends.sqlite_backend import SQLiteBackend
from .errors import INDEX_ONLY_STATE, storage_error_from
from .migrations import MigrationError
from .models import Base
from .schema_repair import MIGRATION_FAILED_SUGGESTION, repair_schema, schema_lock
from .virtual_tables import create_fts_tables, create_vec_table

logger = logging.getLogger(__name__)

# The width of the concurrency that can be inside the database at once.
#
# FastAPI runs every plain `def` handler on anyio's worker threadpool, whose
# default limiter is 40 tokens — so up to 40 requests can be executing sync DB
# work simultaneously. The SQLAlchemy default pool is QueuePool(size=5,
# max_overflow=10) = 15 connections, which is *narrower* than that: the spike
# for #131 measured 26/40 units failing with
# "TimeoutError: QueuePool limit of size 5 overflow 10 reached" once each unit
# took its own connection.
#
# We size the pool to the limiter rather than capping the limiter: capping it
# would serialise non-DB async work (file reads, git calls, embedding) that has
# nothing to do with the database, and the connections are cheap — SQLite
# connections are file handles, and idle ones are recycled by the pool.
HANDLER_CONCURRENCY_LIMIT = 40

# Headroom above the limiter for the callers that are *not* threadpool
# handlers: the index worker's background thread, websocket broadcasts, and
# any nested scope inside one request.
_POOL_SIZE = HANDLER_CONCURRENCY_LIMIT
_MAX_OVERFLOW = 20

# How long a connection waits for SQLite's single write lock before raising
# "database is locked", in milliseconds. Set explicitly, in this one place,
# on every connection the pool hands out (#440) -- previously unset, so
# pysqlite's own undeclared default (also 5000 ms, the same number) applied
# instead, with nothing pinning it or saying so and nothing to notice if a
# driver upgrade moved it. `set_sqlite_pragma` below reads this module global
# at each connection's `connect` event, so a test pins it by monkeypatching
# this name and building a fresh ``PyriteDB`` -- see
# ``tests/test_storage.py::TestTransactionMode::test_busy_timeout_reads_this_module_constant``.
SQLITE_BUSY_TIMEOUT_MS = 5000

# Register explicit adapters to avoid Python 3.12+ deprecation warnings
sqlite3.register_adapter(datetime, lambda dt: dt.isoformat())
sqlite3.register_adapter(date, lambda d: d.isoformat())
sqlite3.register_converter("timestamp", lambda b: datetime.fromisoformat(b.decode()))

CORRUPT_INDEX_SUGGESTION = (
    "Move the unreadable index file aside, and keep it, then run `pyrite index build` "
    "to rebuild the index from the markdown files. The rebuild does not restore the "
    f"{INDEX_ONLY_STATE}."
)


def is_index_corruption(exc: BaseException) -> TypeGuard[sqlite3.DatabaseError]:
    """Distinguish a broken index from query syntax, locks and other SQL errors."""
    code = getattr(exc, "sqlite_errorcode", 0)
    return isinstance(exc, sqlite3.DatabaseError) and (code & 0xFF) in (
        sqlite3.SQLITE_CORRUPT,
        sqlite3.SQLITE_NOTADB,
    )


def index_file_problem(db_path: Path) -> str | None:
    """Why the index file at ``db_path`` is unreadable, judged from its header, or None.

    SQLite opens a file cut short without complaint: it trusts the page count in
    its header, and no read of a table notices the lost tail. So the file is
    checked against that count here, the one place the rule lives (the opener
    and ``db backup`` both call it). The count is only believed when it is
    current and the main file is the whole database:

    * a non-empty ``-wal`` means the main file may lawfully lag the count (a
      checkpoint in progress, or a crash in the middle of one): the pages are
      in the WAL;
    * offset 92 (version-valid-for) must equal offset 24 (change counter), or
      SQLite itself ignores the count at offset 28 and uses the file size.

    A zero-byte file is an empty database. A directory or a file that is not
    SQLite is refused whatever the WAL says.
    """
    if not db_path.exists():
        return None
    size = db_path.stat().st_size
    if size == 0:
        return None
    with db_path.open("rb") as fh:
        header = fh.read(100)
    if len(header) < 100 or not header.startswith(b"SQLite format 3\x00"):
        return "file is not a database"
    raw_page_size = int.from_bytes(header[16:18], "big")
    page_size = 65536 if raw_page_size == 1 else raw_page_size
    if page_size < 512:
        return "database disk image is malformed"
    wal = db_path.with_name(db_path.name + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        return None
    if header[92:96] != header[24:28]:
        return None
    in_header_pages = int.from_bytes(header[28:32], "big")
    if size % page_size != 0 or (in_header_pages > 0 and size < page_size * in_header_pages):
        return "database disk image is malformed"
    return None


class _FallbackSessionHolder:
    """Holds a per-thread fallback session and releases its connection on thread exit (#1100)."""

    def __init__(self, session: Session, db: ConnectionMixin) -> None:
        self.session = session
        self.db = weakref.ref(db)

    def __del__(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass
        db = self.db()
        if db is not None:
            try:
                with db._fallback_lock:
                    if self.session in db._fallback_sessions:
                        db._fallback_sessions.remove(self.session)
            except Exception:
                pass


class ConnectionMixin:
    """Database connection, extensions, migrations, and plugin table creation."""

    def _init_connection(self, db_path: Path):
        self.db_path = Path(db_path)
        self.engine = None
        self._sa_conn = None
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            if self.db_path.is_dir():
                raise IsADirectoryError(f"Index path is a directory: {self.db_path}")
            problem = index_file_problem(self.db_path)
            if problem:
                raise sqlite3.DatabaseError(problem)

            # Create SQLAlchemy engine.
            #
            # `check_same_thread: False` stays: the pool hands a connection to
            # whichever thread checks it out, and with a session per scope no two
            # threads share one. It is safe here precisely *because* the session
            # is no longer shared — it was the flag that made the shared-session
            # bug silent rather than a clean sqlite3 ProgrammingError (#131).
            self.engine = create_engine(
                f"sqlite:///{self.db_path}",
                echo=False,
                connect_args={"check_same_thread": False},
                pool_size=_POOL_SIZE,
                max_overflow=_MAX_OVERFLOW,
                pool_pre_ping=True,
            )

            # Set SQLite pragmas on every connection
            @event.listens_for(self.engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                # busy_timeout first: the journal_mode switch below waits for
                # another connection's lock under it, not pysqlite's default.
                cursor.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
                cursor.execute("PRAGMA foreign_keys = ON")
                cursor.execute("PRAGMA journal_mode = WAL")
                cursor.execute("PRAGMA synchronous = NORMAL")
                cursor.close()

            # Translate actual corruption, preserving query and lock errors.
            # Keep the listener free of an owner reference (engine -> self
            # would retain every request's database through an event cycle).
            index_path_for_errors = str(self.db_path)

            @event.listens_for(self.engine, "handle_error")
            def handle_db_error(exception_context: ExceptionContext) -> None:
                orig = exception_context.original_exception
                if is_index_corruption(orig):
                    raise storage_error_from(
                        orig,
                        f"Database error at {index_path_for_errors}",
                        suggestion=CORRUPT_INDEX_SUGGESTION,
                    ) from orig

            # Session factory. There is deliberately no long-lived *shared*
            # Session: one Session used by concurrent threads corrupts SQLAlchemy's
            # result state (#131). `self.session` below resolves per thread — the
            # innermost `session_scope()` on this thread, or a per-thread fallback
            # for callers that never open a scope.
            #
            # `_local` holds both: `.scopes` is the stack of active scopes and
            # `.fallback` the scope-free session. Single-threaded callers (the CLI,
            # `pyrite index build`, most tests) see exactly the previous behaviour
            # — one long-lived Session, so an uncommitted ORM change is still
            # visible to the next call on that thread. See `session` for why this
            # is thread-local rather than a ContextVar.
            self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
            self._local = threading.local()
            self._fallback_sessions: list[Session] = []
            self._fallback_lock = threading.Lock()

            # One process at a time brings the file to this build's schema
            # (#1302): two that both see it behind would both migrate it. The
            # lock is taken before the first connection, too: that connection
            # switches a new file to WAL, and SQLite answers a second process
            # doing the same at once with "database is locked", busy timeout
            # or not.
            with schema_lock(self.db_path):
                # Raw sqlite3 connection for the FTS5 / sqlite-vec virtual tables and
                # migrations. sqlite3 serialises internally, but see `_raw_cursor()`
                # below: a shared *cursor* is what is unsafe, so raw reads take their
                # own cursor under a lock rather than sharing one (#131 criterion 7).
                self._sa_conn = self.engine.connect()
                self._raw_conn = self._sa_conn.connection.dbapi_connection
                self._raw_conn.row_factory = sqlite3.Row
                self._raw_lock = threading.RLock()

                self._load_extensions()
                self._prepare_schema()
        except StorageError:
            # Already the error the user reads (the schema refusal, a busy
            # schema lock, corruption the handle_error listener translated);
            # only release the handles.
            self._dispose_after_failed_open()
            raise
        except (OSError, sqlite3.Error, SQLAlchemyError, MigrationError) as exc:
            self._dispose_after_failed_open()
            # A disk fault is not fixed by a rebuild; what is left is a file
            # SQLite cannot read (corruption, not a database, a directory).
            raise storage_error_from(
                exc,
                f"Cannot open index database at {self.db_path}",
                suggestion=CORRUPT_INDEX_SUGGESTION,
            ) from exc

        # Instantiate the search backend.
        #
        # It is handed the *owner* (this PyriteDB), not a Session object: the
        # backend's `self._session` is a property that reads the owner's
        # current scope, so a backend built once at startup still serves each
        # request from that request's own session (#131).
        self._backend = SQLiteBackend(
            session=None,
            raw_conn=self._raw_conn,
            vec_available=self.vec_available,
            owner=self,
        )

    # =====================================================================
    # Session lifetime (#131)
    # =====================================================================

    @property
    def session(self) -> Session:
        """The Session for the current unit of work.

        Resolution order:

        1. A session explicitly attached to *this handle* by
           :meth:`request_handle` — what the server's per-request dependency
           hands to the services, so every call made while serving one request
           resolves to that request's session on any thread.
        2. The innermost :meth:`session_scope` open on this thread.
        3. A per-thread fallback, created on demand. This preserves the
           previous single-threaded semantics exactly for the CLI,
           ``pyrite index build`` and most tests: the same object across calls
           on that thread, so an uncommitted ORM change is still visible to the
           next call.

        It is never the same object in two concurrent requests, which is the
        whole of this fix.

        **Why a per-request handle, and not a ContextVar or the thread.** Both
        of those were tried and both are wrong here, measurably. anyio runs
        every sync callable via ``context.run()`` on a *copy* of the context,
        so a ContextVar set in the dependency is invisible to the handler,
        which gets its own copy — a probe showed the dependency binding session
        A while the handler read session B. The thread is no better: one anyio
        worker thread interleaves *several* requests. A probe caught the
        sequence ``dep-in, dep-in, dep-in, handler, handler, dep-out,
        dep-out`` on a single thread, so request A's teardown would close
        request B's session while B's query was still streaming rows — exactly
        the ``identity map is no longer valid`` and ``IllegalStateChangeError:
        close() ... _connection_for_bind() is already in progress`` the live
        server produced. What the dependency and the handler reliably share is
        the object graph built for that request, so the session travels on it.
        """
        own = self.__dict__.get("_request_session")
        if own is not None:
            return own
        stack = getattr(self._local, "scopes", None)
        if stack:
            return stack[-1]
        holder = getattr(self._local, "fallback_holder", None)
        if holder is None:
            session = self._session_factory()
            holder = _FallbackSessionHolder(session, self)
            self._local.fallback_holder = holder
            # Tracked so `close()` can dispose of sessions belonging to threads
            # that have since exited or remain alive; `_FallbackSessionHolder`
            # disposes of the session if the thread terminates first (#1100).
            with self._fallback_lock:
                self._fallback_sessions.append(session)
        return holder.session

    @contextmanager
    def request_handle(self):
        """Yield a per-request handle onto this database, with its own Session.

        The handle is a thin proxy that shares this object's engine, pool, raw
        connection and backend, and differs in exactly one respect: its
        :attr:`session` is a Session created for this request and closed when
        the block exits, on every path including exceptions. Services built
        from the handle therefore never touch another request's session, and no
        service or endpoint signature had to change to get that.

        Closing is not optional: the measured failure mode for a half-done fix
        is per-request sessions that are never returned to the pool, trading
        the corruption for ``QueuePool limit ... reached``.
        """
        session = self._session_factory()
        handle = copy.copy(self)
        handle.__dict__["_request_session"] = session
        # The backend must resolve through the *handle*, not the original, or
        # every ORM read would fall back to the shared thread-local session.
        handle._backend = self._backend.for_owner(handle)
        try:
            yield handle
        finally:
            try:
                session.close()
            except Exception:  # pragma: no cover - defensive
                logger.warning("Failed to close request session", exc_info=True)

    @contextmanager
    def session_scope(self):
        """Bind a fresh Session to the calling thread, closed on every exit.

        For callers that own their thread for the duration of the work: the
        CLI, background jobs, tests. The server uses :meth:`request_handle`
        instead, because its worker threads interleave requests.

        Scopes nest: each gets its own Session and the enclosing one is
        restored on exit.
        """
        session = self._session_factory()
        stack = getattr(self._local, "scopes", None)
        if stack is None:
            stack = []
            self._local.scopes = stack
        stack.append(session)
        try:
            yield session
        finally:
            stack.pop()
            try:
                session.close()
            except Exception:  # pragma: no cover - defensive
                logger.warning("Failed to close scoped session", exc_info=True)

    @contextmanager
    def _raw_cursor(self, conn=None):
        """Serialise raw sqlite3 access and hand out a private cursor.

        ``_raw_conn`` is one sqlite3 connection shared by every caller, the
        same lifetime bug as the session had (#131 criterion 7). sqlite3
        serialises *statements* internally, but ``Connection.execute()``
        returns a fresh cursor whose rows are fetched later — and the
        module-level default ``check_same_thread=False`` means two threads can
        interleave execute/fetch on the same connection. Giving each caller its
        own cursor under a lock makes the FTS/vec paths safe without opening a
        second connection per request (a sqlite3 connection cannot be shared
        across the pool the way the ORM's can).

        ``conn`` lets a caller name the connection to take the cursor from
        while still taking this object's lock. A backend passes its own
        ``_raw_conn``, which is normally this same object but may have been
        substituted — ``_spy_on_sql`` in the conformance suite wraps it to
        record SQL. Ignoring that argument would hand back a cursor on the
        unwrapped connection and make the substitution silently ineffective.
        """
        with self._raw_lock:
            cursor = (conn if conn is not None else self._raw_conn).cursor()
            try:
                yield cursor
            except sqlite3.DatabaseError as exc:
                if not is_index_corruption(exc):
                    raise
                raise storage_error_from(
                    exc, f"Database error at {self.db_path}", suggestion=CORRUPT_INDEX_SUGGESTION
                ) from exc
            finally:
                cursor.close()

    def _load_extensions(self):
        """Try to load sqlite-vec extension for vector search."""
        self.vec_available = False
        self.vec_load_error: Exception | None = None
        try:
            import sqlite_vec
        except Exception as exc:
            self.vec_load_error = exc
            logger.info(
                "sqlite-vec extension not available: %s: %s",
                type(exc).__name__,
                exc,
            )
            return

        load_error = None
        try:
            self._raw_conn.enable_load_extension(True)
            sqlite_vec.load(self._raw_conn)
        except Exception as exc:
            load_error = exc
        finally:
            try:
                self._raw_conn.enable_load_extension(False)
            except Exception as exc:
                # Keep the load failure: it explains why vector search is
                # unavailable, while this cleanup failure is secondary.
                if load_error is None:
                    load_error = exc

        if load_error is not None:
            self.vec_load_error = load_error
            logger.info(
                "sqlite-vec extension not available: %s: %s",
                type(load_error).__name__,
                load_error,
            )
            return

        self.vec_available = True

    def _dispose_after_failed_open(self) -> None:
        if self._sa_conn is not None:
            try:
                self._sa_conn.close()
            except Exception:
                pass
            self._sa_conn = None
        if self.engine is not None:
            try:
                self.engine.dispose()
            except Exception:
                pass

    def _prepare_schema(self) -> None:
        """Bring the file to this build's schema; runs under ``schema_lock``.

        Four steps, in order, each its own call so the #718 explicit-upgrade
        mode can refuse before the first one writes: ``create_all`` adds
        missing tables, then the virtual tables, the numbered migrations, and
        the repair that adds any model column or index still missing
        (``schema_repair.py``), and last the plugin tables.
        """
        assert self.engine is not None  # set by _init_connection before the lock
        Base.metadata.create_all(self.engine)
        create_fts_tables(self._raw_conn)
        if self.vec_available:
            create_vec_table(self._raw_conn)
        self._run_migrations()
        self._repair_schema()
        self._create_plugin_tables()

    def _run_migrations(self):
        """Run any pending database migrations using legacy MigrationManager.

        A migration that fails refuses the open with `IndexSchemaRefusedError`,
        naming only the versions; the driver's text goes to the log.
        """
        from .migrations import CURRENT_VERSION, MigrationManager

        mgr = MigrationManager(self._raw_conn)
        self._version_at_open = mgr.get_current_version()
        pending = mgr.get_pending_migrations()
        if pending:
            from_version = self._version_at_open
            try:
                mgr.migrate()
            except MigrationError as exc:
                logger.warning("Index migration failed: %s", exc)
                raise storage_error_from(
                    exc,
                    f"Upgrading the index from schema version {from_version} to "
                    f"{CURRENT_VERSION} stopped at migration v{exc.version}; the steps "
                    "before it are kept.",
                    cls=IndexSchemaRefusedError,
                    with_detail=False,
                    suggestion=MIGRATION_FAILED_SUGGESTION,
                ) from exc
        # Create vec_entry table if sqlite-vec is available and table doesn't exist
        if self.vec_available:
            create_vec_table(self._raw_conn)

    def _repair_schema(self) -> list[str]:
        """Add model columns and indexes the file lacks whatever its recorded version (#1260)."""
        return repair_schema(
            cast(sqlite3.Connection, self._raw_conn),
            version_at_open=getattr(self, "_version_at_open", None),
        )

    def _create_plugin_tables(self):
        """Create custom tables defined by plugins."""
        try:
            from ..plugins import get_registry

            for table_def in get_registry().get_all_db_tables():
                self._create_table_from_def(table_def)
        except Exception:
            logger.warning("Plugin table creation failed", exc_info=True)

    _VALID_SQL_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    _VALID_SQL_TYPE = re.compile(r"^[A-Z][A-Z0-9_ ()]*$")
    _VALID_FK_REF = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*\([a-zA-Z_][a-zA-Z0-9_]*\)$")
    _VALID_SQL_DEFAULT = re.compile(
        r"^(?:NULL|TRUE|FALSE|CURRENT_TIMESTAMP|[0-9]+(?:\.[0-9]+)?|'[^']*')$",
        re.IGNORECASE,
    )

    def _validate_identifier(self, value: str, context: str) -> str:
        """Validate a SQL identifier to prevent injection."""
        if not self._VALID_SQL_IDENTIFIER.match(value):
            raise ValueError(f"Invalid SQL identifier for {context}: {value!r}")
        return value

    def _create_table_from_def(self, table_def: dict):
        """Create a single table from a plugin table definition."""
        name = self._validate_identifier(table_def["name"], "table name")
        columns = table_def.get("columns", [])
        indexes = table_def.get("indexes", [])

        col_defs = []
        for col in columns:
            col_name = self._validate_identifier(col["name"], "column name")
            col_type = col["type"]
            if not self._VALID_SQL_TYPE.match(col_type):
                raise ValueError(f"Invalid SQL type: {col_type!r}")
            parts = [col_name, col_type]
            if col.get("primary_key"):
                parts.append("PRIMARY KEY")
                if col_type == "INTEGER":
                    parts.append("AUTOINCREMENT")
            if col.get("nullable") is False:
                parts.append("NOT NULL")
            if "default" in col:
                default_val = str(col["default"])
                if not self._VALID_SQL_DEFAULT.match(default_val):
                    raise ValueError(f"Invalid SQL DEFAULT value: {default_val!r}")
                parts.append(f"DEFAULT {default_val}")
            col_defs.append(" ".join(parts))

        for fk in table_def.get("foreign_keys", []):
            fk_col = self._validate_identifier(fk["column"], "foreign key column")
            fk_ref = fk["references"]
            if not self._VALID_FK_REF.match(fk_ref):
                raise ValueError(f"Invalid FK reference: {fk_ref!r}")
            col_defs.append(f"FOREIGN KEY ({fk_col}) REFERENCES {fk_ref}")

        sql = f"CREATE TABLE IF NOT EXISTS {name} ({', '.join(col_defs)})"
        self._raw_conn.execute(sql)

        for idx in indexes:
            for idx_col in idx["columns"]:
                self._validate_identifier(idx_col, "index column")
            cols = ", ".join(idx["columns"])
            unique = "UNIQUE " if idx.get("unique") else ""
            idx_name = f"idx_{name}_{'_'.join(idx['columns'])}"
            self._raw_conn.execute(
                f"CREATE {unique}INDEX IF NOT EXISTS {idx_name} ON {name} ({cols})"
            )

        self._raw_conn.commit()

    @property
    def conn(self):
        """Backward-compat: returns raw sqlite3 connection.

        Deprecated; removed in 0.27. Use ``session`` for writes and ``execute_sql()`` for
        read-only raw SQL queries.
        """
        warnings.warn(
            "db.conn is deprecated and will be removed in 0.27. Use db.session for writes and "
            "db.execute_sql() for read-only queries.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._raw_conn

    def execute_sql(self, sql: str, params=None) -> list[dict]:
        """Execute raw SQL through the session connection.

        Uses named ``:param`` placeholders.  Returns a list of dicts for
        SELECT statements or an empty list for non-returning statements.
        Does **not** commit — call :meth:`execute_write_sql` for DML that
        should be committed immediately.
        """
        result = self.session.execute(text(sql), params or {})
        if result.returns_rows:
            cols = result.keys()
            return [dict(zip(cols, row, strict=False)) for row in result.fetchall()]
        return []

    def execute_write_sql(self, sql: str, params=None, *, commit: bool = True) -> int:
        """Execute a write (INSERT/UPDATE/DELETE) and return ``rowcount``.

        Uses named ``:param`` placeholders.  Commits by default; pass
        ``commit=False`` to defer the commit (e.g. for multi-statement
        transactions finished with ``self.session.commit()``).
        """
        result = self.session.execute(text(sql), params or {})
        if commit:
            self.session.commit()
        return result.rowcount

    def get_schema_version(self) -> int:
        """Get current schema version."""
        from .migrations import MigrationManager

        mgr = MigrationManager(self._raw_conn)
        return mgr.get_current_version()

    def get_migration_status(self) -> dict:
        """Get migration status including pending migrations."""
        from .migrations import MigrationManager

        mgr = MigrationManager(self._raw_conn)
        return mgr.status()

    def close(self):
        """Close database connection.

        Only the fallback sessions are closed here — every thread's, not just
        the caller's. Scoped sessions are owned by their ``session_scope()``
        and already closed in its ``finally``.
        """
        with self._fallback_lock:
            sessions, self._fallback_sessions = self._fallback_sessions, []
        for session in sessions:
            try:
                session.close()
            except Exception:  # pragma: no cover - defensive
                logger.warning("Failed to close fallback session", exc_info=True)
        self._local = threading.local()
        if hasattr(self, "_sa_conn"):
            self._sa_conn.close()
        self.engine.dispose()

    def __enter__(self):
        """Support `with PyriteDB(path) as db:` -- closes on block exit so
        callers (tests especially) don't have to remember a manual
        db.close(). An unclosed connection in WAL mode can recreate its
        -wal/-shm files while a caller's TemporaryDirectory is mid-rmtree
        (tests-leak-open-pyritedb-connections-into-temporarydirectory-
        teardown)."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    @contextmanager
    def transaction(self):
        """Context manager for ORM transactions with rollback on failure.

        Semantics are unchanged, but restated now that the session is
        scope-owned (#131 criterion 6): the transaction runs on **the calling
        scope's** session — the request's inside a ``session_scope()``, the
        fallback session outside one — and commits or rolls back exactly that
        one. Two concurrent requests therefore commit independently, where
        before they shared a transaction and one's rollback discarded the
        other's writes. The session is resolved once, so a nested
        ``session_scope()`` opened inside the block cannot redirect the commit.
        """
        session = self.session
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
