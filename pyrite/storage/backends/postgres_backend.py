"""
PostgresBackend — SearchBackend implementation backed by PostgreSQL + tsvector + pgvector.

Inherits shared ORM/SQL logic from BaseBackend.  Only overrides:
- ``_exec`` / ``_exec_one`` / ``_exec_scalar`` (SQLAlchemy text() with named params)
- ``_sync_links`` (delete-all-reinsert)
- Full-text search (tsvector/tsquery)
- Embedding operations (pgvector)
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Any, ClassVar

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from ...exceptions import StorageError
from ..models import Link
from .base_backend import BaseBackend
from .capabilities import BackendCapability
from .protocol import DEFAULT_MIN_SIMILARITY, similarity_from

logger = logging.getLogger(__name__)

# The Postgres backend is experimental in 0.25.x (#718): the alembic chain stops
# at revision 006, so an existing Postgres database cannot take a column added
# since. Said once per process, from the one place the backend is built, so the
# CLI, the REST server and the MCP server all get it without a per-command change.
EXPERIMENTAL_ACK_ENV = "PYRITE_POSTGRES_EXPERIMENTAL_ACK"
_EXPERIMENTAL_NOTICE = (
    "The Postgres backend is experimental in Pyrite 0.25.x: an existing Postgres "
    "database may not upgrade between releases. "
    "Details: https://github.com/pyrite-wiki/pyrite/issues/718 "
    f"(set {EXPERIMENTAL_ACK_ENV}=1 to silence this notice)"
)
_experimental_notice_shown = False
_experimental_notice_lock = threading.Lock()

# ``non_empty`` (#200), the same predicate as SQLite's: a body or a summary
# with a character other than space, tab, CR or LF.
NON_EMPTY_SQL = (
    " AND (btrim(COALESCE(e.body, ''), chr(32) || chr(9) || chr(10) || chr(13)) != ''"
    " OR btrim(COALESCE(e.summary, ''), chr(32) || chr(9) || chr(10) || chr(13)) != '')"
)


def _logging_reaches_stderr() -> bool:
    """True when a handler on the ``pyrite`` chain will write a WARNING to stderr."""
    log = logging.getLogger("pyrite")
    if not log.isEnabledFor(logging.WARNING):
        return False
    while log is not None:
        for h in log.handlers:
            if (
                isinstance(h, logging.StreamHandler)
                and getattr(h, "stream", None) is sys.stderr
                and h.level <= logging.WARNING
            ):
                return True
        log = log.parent if log.propagate else None
    return False


def _announce_experimental() -> None:
    """Say once per process that this backend is experimental.

    **Cannot stop the backend being constructed.** What can raise, and how each
    is handled: ``os.environ.get(...).strip().lower()`` cannot (a ``str``);
    the ``threading.Lock`` cannot; ``logger.warning`` can (a custom handler or
    filter may raise) and is guarded; ``_logging_reaches_stderr`` can (odd
    handler objects) and is guarded, assuming "not reached"; the stderr write
    and flush can (closed file, broken pipe) and are guarded. Each step is
    guarded on its own, so a failing log handler does not skip stderr. The
    outer ``try`` is the backstop. The latch is set before any write, so a
    failure loses the notice but never repeats it.

    **Never writes to stdout by Pyrite's own doing** (``--format json`` output
    and the MCP stdio stream stay clean). ``sys.stderr`` is read once; if it is
    ``None`` (``2>&-``) the direct write is skipped, never redirected to stdout.
    The log gets the notice for the server; stderr gets it directly unless a
    ``pyrite`` log handler already writes warnings to stderr.

    **Limits, not bugs.** That handler detection can print twice (``sys.stderr``
    rebound after logging was configured, or a non-``StreamHandler`` that writes
    to stderr) and can print nothing (a child logger's level raised above
    WARNING). Worst case under deliberate configuration is silence. An embedding
    application that routes logging to stdout has chosen that: it is not
    prevented.
    """
    global _experimental_notice_shown
    try:
        if os.environ.get(EXPERIMENTAL_ACK_ENV, "").strip().lower() in ("1", "true", "yes"):
            return
        with _experimental_notice_lock:
            if _experimental_notice_shown:
                return
            _experimental_notice_shown = True
        try:
            logger.warning(_EXPERIMENTAL_NOTICE)
        except Exception:  # noqa: BLE001 - a notice must never break construction
            pass
        try:
            covered = _logging_reaches_stderr()
        except Exception:  # noqa: BLE001
            covered = False
        err = sys.stderr  # read once; None when fd 2 was closed at startup
        if not covered and err is not None:
            try:
                err.write(f"pyrite: {_EXPERIMENTAL_NOTICE}\n")
                err.flush()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001 - backstop
        pass


# The DBAPI the ``postgres`` extra installs (``psycopg2-binary``). A bare
# ``postgresql://`` URL leaves the choice to SQLAlchemy, and SQLAlchemy 2.1
# changed its default from psycopg2 to psycopg (v3) -- which the extra does not
# install -- so every bare URL failed with ``No module named 'psycopg'``.
_DEFAULT_DRIVER = "psycopg2"


def postgres_url(url: str | URL) -> URL:
    """Resolve a Postgres URL to one whose driver the ``postgres`` extra installs.

    A bare ``postgresql://`` or ``postgres://`` URL is pinned to
    ``postgresql+psycopg2://``, the same on SQLAlchemy 2.0 and 2.1. A driver
    the user named (``postgresql+psycopg://``, ``+asyncpg`` ...) is theirs and
    is left alone; only the ``postgres`` alias, which SQLAlchemy does not
    accept as a dialect name, is spelled ``postgresql``. Returns a ``URL`` object rather than a string: rendering a
    URL to text masks its password.
    """
    parsed = make_url(url)
    backend, _, driver = parsed.drivername.partition("+")
    if backend not in ("postgresql", "postgres"):
        raise ValueError(f"not a PostgreSQL URL: {parsed.drivername}://...")
    return parsed.set(drivername=f"postgresql+{driver or _DEFAULT_DRIVER}")


def create_postgres_engine(url: str | URL, **kwargs: Any) -> Engine:
    """``create_engine`` for Postgres, through ``postgres_url``.

    Every Postgres engine is built here, so the driver choice lives in one
    place. ``kwargs`` go to ``create_engine`` unchanged.
    """
    return create_engine(postgres_url(url), **kwargs)


def ensure_schema(engine) -> None:
    """Create pgvector extension, FTS column, embedding column, and indexes.

    Idempotent — safe to call on every startup.
    """
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("ALTER TABLE entry ADD COLUMN IF NOT EXISTS fts_vector tsvector"))
        conn.execute(text("ALTER TABLE entry ADD COLUMN IF NOT EXISTS embedding vector(384)"))
        # GIN index for FTS
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS idx_entry_fts ON entry USING gin(fts_vector)")
        )
        # IVFFlat index for vector KNN (requires rows to exist; falls back to seq scan if empty)
        # Use HNSW for small corpora — no training needed
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_entry_embedding "
                "ON entry USING hnsw(embedding vector_cosine_ops)"
            )
        )
        # Trigger to auto-update fts_vector on INSERT/UPDATE
        conn.execute(
            text("""
            CREATE OR REPLACE FUNCTION entry_fts_trigger() RETURNS trigger AS $$
            BEGIN
                NEW.fts_vector :=
                    setweight(to_tsvector('english', coalesce(NEW.title, '')), 'A') ||
                    setweight(to_tsvector('english', coalesce(NEW.summary, '')), 'B') ||
                    setweight(to_tsvector('english', coalesce(NEW.body, '')), 'C');
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        )
        # The guard is scoped to *this* schema's `entry` table. `pg_trigger` is
        # cluster-wide and `tgname` is not unique across tables, so asking only
        # whether a trigger named `trg_entry_fts` exists anywhere answers "yes"
        # for some other schema's copy: this schema then silently gets no
        # trigger, `entry.fts_vector` is never populated, and keyword search
        # returns nothing with no error. `'entry'::regclass` resolves through
        # the connection's search_path, so it names the table this call is
        # actually setting up. Two instances sharing one database in separate
        # schemas is an ordinary deployment, and it is how the backend
        # conformance tests isolate xdist workers.
        conn.execute(
            text("""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger t
                    JOIN pg_class c ON c.oid = t.tgrelid
                    WHERE t.tgname = 'trg_entry_fts'
                      AND c.oid = 'entry'::regclass
                ) THEN
                    CREATE TRIGGER trg_entry_fts
                    BEFORE INSERT OR UPDATE ON entry
                    FOR EACH ROW EXECUTE FUNCTION entry_fts_trigger();
                END IF;
            END $$;
        """)
        )
        conn.commit()


class PostgresBackend(BaseBackend):
    """SearchBackend implementation for PostgreSQL + tsvector + pgvector."""

    # Postgres implements the full backend protocol: entity CRUD (BaseBackend),
    # tsvector keyword search, and pgvector embeddings. Declared at the class
    # level; runtime availability of the extensions is a separate gate. See
    # capabilities.py.
    # FILTERED_SEMANTIC: ``search_semantic`` puts the same predicates as
    # ``search`` in the same ``WHERE`` as the distance ordering, so a fused
    # hybrid result can never contain an entry the caller's filter excluded
    # (#56).
    capabilities: ClassVar[set[BackendCapability]] = {
        BackendCapability.ENTITY,
        BackendCapability.SEARCH,
        BackendCapability.EMBEDDING,
        BackendCapability.FILTERED_SEMANTIC,
    }

    def __init__(self, session: Session | None = None, engine=None, owner=None):
        # Same session lifetime as SQLiteBackend (#131 criterion 8): when an
        # `owner` (a PyriteDB) is given, `self._session` resolves to that
        # owner's *current scope* session rather than caching one object for
        # the backend's lifetime. A shared psycopg connection under concurrent
        # use raises rather than corrupting results the way SQLite does, so the
        # symptom differs — the lifetime bug is the same one.
        _announce_experimental()
        self._owner = owner
        self._explicit_session = session
        self._engine = engine

    @property
    def _session(self):
        """The session for the current scope (see ``PyriteDB.session``)."""
        if self._owner is not None:
            return self._owner.session
        return self._explicit_session

    @_session.setter
    def _session(self, value):
        self._explicit_session = value

    def for_owner(self, owner):
        """A copy whose session resolves through *owner* (see SQLiteBackend)."""
        import copy as _copy

        clone = _copy.copy(self)
        clone._owner = owner
        return clone

    def close(self) -> None:
        """No-op — connection lifecycle owned by caller."""

    # =====================================================================
    # Raw SQL helpers (SQLAlchemy text() with :named params)
    # =====================================================================

    def _exec(self, sql: str, params: dict | None = None) -> list[dict[str, Any]]:
        """Execute raw SQL and return all rows as list of dicts.

        A legitimate zero-row result returns ``[]``. A failure during result
        materialization (driver decode error, unexpected row shape, strict-zip
        mismatch) is logged and re-raised as ``StorageError`` rather than
        masked as an empty list — empty-on-error is indistinguishable from a
        genuine no-match and has historically hidden silent-data-loss bugs.
        """
        result = self._session.execute(text(sql), params or {})
        try:
            rows = result.fetchall()
            cols = result.keys()
            return [dict(zip(cols, row, strict=True)) for row in rows]
        except Exception as e:
            logger.error("Failed to materialize result for SQL: %s", sql, exc_info=True)
            raise StorageError(f"Failed to materialize query result: {e}") from e

    def _exec_one(self, sql: str, params: dict | None = None) -> dict | None:
        """Execute raw SQL and return first row as dict, or None."""
        result = self._session.execute(text(sql), params or {})
        row = result.fetchone()
        if row is None:
            return None
        return dict(zip(result.keys(), row, strict=True))

    def _exec_scalar(self, sql: str, params: dict | None = None):
        """Execute raw SQL and return scalar value."""
        result = self._session.execute(text(sql), params or {})
        row = result.fetchone()
        return row[0] if row else None

    # =====================================================================
    # _sync_links (delete-all-reinsert — Postgres-specific)
    # =====================================================================

    def _sync_links(self, entry_id: str, kb_name: str, links: list[dict[str, Any]]) -> None:
        self._session.query(Link).filter_by(source_id=entry_id, source_kb=kb_name).delete()
        for link in links:
            from ...schema import get_inverse_relation

            relation = link.get("relation", "related_to")
            inverse = get_inverse_relation(relation)
            target_kb = link.get("kb", kb_name)
            self._session.add(
                Link(
                    source_id=entry_id,
                    source_kb=kb_name,
                    target_id=link.get("target"),
                    target_kb=target_kb,
                    relation=relation,
                    inverse_relation=inverse,
                    note=link.get("note", ""),
                )
            )

    # =====================================================================
    # Full-text search (PostgreSQL tsvector/tsquery)
    # =====================================================================

    def search(
        self,
        query: str,
        kb_name: str | None = None,
        kb_names: set[str] | list[str] | None = None,
        entry_type: str | None = None,
        tags: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
        include_archived: bool = False,
        lifecycle: str | None = None,
        fips: str | None = None,
        state: str | None = None,
        status: str | None = None,
        non_empty: bool = False,
    ) -> list[dict[str, Any]]:
        # Build the tsquery — plainto_tsquery handles user input safely
        sql = """
            SELECT
                e.id, e.kb_name, e.entry_type, e.title, e.body, e.summary,
                e.file_path, e.date, e.importance, e.status, e.location,
                e.lifecycle, e.metadata, e.created_at, e.updated_at, e.indexed_at,
                e.created_by, e.modified_by,
                ts_headline('english', coalesce(e.body, ''),
                    plainto_tsquery('english', :query),
                    'StartSel=<mark>, StopSel=</mark>, MaxFragments=3, MaxWords=32'
                ) as snippet,
                ts_rank(e.fts_vector, plainto_tsquery('english', :query)) as rank
            FROM entry e
            WHERE e.fts_vector @@ plainto_tsquery('english', :query)
        """
        params: dict[str, Any] = {"query": query}

        if lifecycle:
            sql += " AND e.lifecycle = :lifecycle"
            params["lifecycle"] = lifecycle
        elif not include_archived:
            sql += " AND COALESCE(e.lifecycle, 'active') != 'archived'"

        if kb_name:
            sql += " AND e.kb_name = :kb_name"
            params["kb_name"] = kb_name
        if kb_names is not None:
            names = list(kb_names)
            if names:
                sql += " AND e.kb_name = ANY(:kb_names)"
                params["kb_names"] = names
            else:
                sql += " AND FALSE"
        if entry_type:
            sql += " AND e.entry_type = :entry_type"
            params["entry_type"] = entry_type
        if date_from:
            sql += " AND e.date >= :date_from"
            params["date_from"] = date_from
        if date_to:
            sql += " AND e.date <= :date_to"
            params["date_to"] = date_to
        if tags:
            tag_params = {}
            for i, t in enumerate(tags):
                tag_params[f"tag_{i}"] = t
            tag_placeholders = ", ".join(f":tag_{i}" for i in range(len(tags)))
            sql += f"""
                AND e.id IN (
                    SELECT et.entry_id FROM entry_tag et
                    JOIN tag t ON et.tag_id = t.id
                    WHERE t.name IN ({tag_placeholders})
                    GROUP BY et.entry_id, et.kb_name
                    HAVING COUNT(DISTINCT t.name) = :tag_count
                )
            """
            params.update(tag_params)
            params["tag_count"] = len(tags)
        if fips:
            sql += " AND e.fips = :fips"
            params["fips"] = fips
        if state:
            sql += " AND e.state = :state"
            params["state"] = state
        if status:
            # The effective status, as the sqlite backend applies it.
            from .. import effective_status

            keys = effective_status.derived_done_keys(self._session, kb_name)
            sql += f" AND {effective_status.status_sql('e.', keys, params)} = :status"
            params["status"] = status
        if non_empty:
            sql += NON_EMPTY_SQL

        sql += " ORDER BY rank DESC LIMIT :limit OFFSET :offset"
        params["limit"] = limit
        params["offset"] = offset

        return self._exec(sql, params)

    def search_by_tag(
        self, tag: str, kb_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT e.* FROM entry e
            JOIN entry_tag et ON e.id = et.entry_id AND e.kb_name = et.kb_name
            JOIN tag t ON et.tag_id = t.id
            WHERE t.name = :tag
        """
        params: dict[str, Any] = {"tag": tag}
        if kb_name:
            sql += " AND e.kb_name = :kb_name"
            params["kb_name"] = kb_name
        sql += " ORDER BY e.date DESC, e.title LIMIT :limit"
        params["limit"] = limit
        return self._exec(sql, params)

    def search_by_date_range(
        self,
        date_from: str,
        date_to: str,
        kb_name: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM entry WHERE date >= :date_from AND date <= :date_to"
        params: dict[str, Any] = {"date_from": date_from, "date_to": date_to}
        if kb_name:
            sql += " AND kb_name = :kb_name"
            params["kb_name"] = kb_name
        sql += " ORDER BY date ASC LIMIT :limit"
        params["limit"] = limit
        return self._exec(sql, params)

    def search_by_tag_prefix(
        self, prefix: str, kb_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT DISTINCT e.* FROM entry e
            JOIN entry_tag et ON e.id = et.entry_id AND e.kb_name = et.kb_name
            JOIN tag t ON et.tag_id = t.id
            WHERE (t.name = :prefix OR t.name LIKE :prefix_like)
        """
        params: dict[str, Any] = {"prefix": prefix, "prefix_like": prefix + "/%"}
        if kb_name:
            sql += " AND e.kb_name = :kb_name"
            params["kb_name"] = kb_name
        sql += " ORDER BY e.date DESC, e.title LIMIT :limit"
        params["limit"] = limit
        return self._exec(sql, params)

    # =====================================================================
    # Semantic search (pgvector embeddings)
    # =====================================================================

    def upsert_embedding(self, entry_id: str, kb_name: str, embedding: list[float]) -> bool:
        # Store embedding directly on the entry row
        vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
        result = self._session.execute(
            text(
                "UPDATE entry SET embedding = CAST(:vec AS vector) "
                "WHERE id = :entry_id AND kb_name = :kb_name"
            ),
            {"vec": vec_str, "entry_id": entry_id, "kb_name": kb_name},
        )
        self._session.commit()
        return result.rowcount > 0

    def search_semantic(
        self,
        embedding: list[float],
        kb_name: str | None = None,
        limit: int = 20,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
        entry_type: str | None = None,
        tags: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        fips: str | None = None,
        state: str | None = None,
        status: str | None = None,
        include_archived: bool = False,
        kb_names: set[str] | list[str] | None = None,
        non_empty: bool = False,
    ) -> list[dict[str, Any]]:
        """KNN over pgvector, honouring the same filters as ``search`` (#56).

        Unlike sqlite-vec there is no separate KNN budget to escalate: every
        predicate, the similarity floor included, goes into the same ``WHERE`` as
        the distance ordering, so ``LIMIT`` applies after filtering and cannot
        under-return. The predicates below mirror
        :meth:`SQLiteBackend._semantic_filter_sql` one for one — the two
        backends must return the same rows for the same call — and every value
        is bound, never interpolated.
        """
        vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
        sql = """
            SELECT e.*, (e.embedding <=> CAST(:vec AS vector)) as distance
            FROM entry e
            WHERE e.embedding IS NOT NULL
        """
        params: dict[str, Any] = {"vec": vec_str}
        if not include_archived:
            # Character for character the predicate ``search`` uses on this
            # backend (see the keyword leg above): an archived entry must not
            # enter a fused result via the vector side (#56).
            sql += " AND COALESCE(e.lifecycle, 'active') != 'archived'"
        if kb_name:
            sql += " AND e.kb_name = :kb_name"
            params["kb_name"] = kb_name
        if kb_names is not None:
            # The caller's readable set, as the keyword leg applies it (#668).
            names = list(kb_names)
            if names:
                sql += " AND e.kb_name = ANY(:kb_names)"
                params["kb_names"] = names
            else:
                sql += " AND FALSE"
        if entry_type:
            sql += " AND e.entry_type = :entry_type"
            params["entry_type"] = entry_type
        if date_from:
            sql += " AND e.date >= :date_from"
            params["date_from"] = date_from
        if date_to:
            sql += " AND e.date <= :date_to"
            params["date_to"] = date_to
        if tags:
            tag_keys = []
            for i, tag in enumerate(tags):
                key = f"sem_tag_{i}"
                tag_keys.append(f":{key}")
                params[key] = tag
            sql += f"""
                AND e.id IN (
                    SELECT et.entry_id FROM entry_tag et
                    JOIN tag t ON et.tag_id = t.id
                    WHERE t.name IN ({",".join(tag_keys)})
                    GROUP BY et.entry_id, et.kb_name
                    HAVING COUNT(DISTINCT t.name) = :sem_tag_count
                )
            """
            params["sem_tag_count"] = len(tags)
        if fips:
            sql += " AND e.fips = :fips"
            params["fips"] = fips
        if state:
            sql += " AND e.state = :state"
            params["state"] = state
        if status:
            # The effective status, as the sqlite backend applies it.
            from .. import effective_status

            keys = effective_status.derived_done_keys(self._session, kb_name)
            sql += f" AND {effective_status.status_sql('e.', keys, params)} = :status"
            params["status"] = status
        if non_empty:
            sql += NON_EMPTY_SQL
        # The similarity floor belongs in the WHERE, not in a Python filter
        # after the fact: applied post-LIMIT it culls rows the LIMIT already
        # paid for and under-returns, where sqlite escalates its k instead. One
        # predicate here keeps the two backends returning the same rows (#56).
        # ``<=>`` is cosine distance, so the floor is ``1 - min_similarity``;
        # a zero vector's distance is NaN, which no comparison admits.
        sql += " AND (e.embedding <=> CAST(:vec3 AS vector)) <= :max_cosine_distance"
        params["vec3"] = vec_str
        params["max_cosine_distance"] = 1.0 - min_similarity
        sql += " ORDER BY e.embedding <=> CAST(:vec2 AS vector) LIMIT :limit"
        params["vec2"] = vec_str
        params["limit"] = limit

        rows = self._exec(sql, params)
        for row in rows:
            # ``distance`` here is already cosine distance (#668).
            row["similarity"] = similarity_from(row.get("distance"))
        return rows

    def has_embeddings(
        self, kb_name: str | None = None, kb_names: set[str] | list[str] | None = None
    ) -> bool:
        sql = "SELECT COUNT(*) FROM entry e WHERE e.embedding IS NOT NULL"
        params: dict[str, Any] = {}
        if kb_name:
            sql += " AND e.kb_name = :kb_name"
            params["kb_name"] = kb_name
        if kb_names is not None:
            names = list(kb_names)
            if not names:
                return False
            sql += " AND e.kb_name = ANY(:kb_names)"
            params["kb_names"] = names
        return (self._exec_scalar(sql, params) or 0) > 0

    def embedding_stats(self) -> dict[str, Any]:
        vec_count = self._exec_scalar("SELECT COUNT(*) FROM entry WHERE embedding IS NOT NULL") or 0
        entry_count = self._exec_scalar("SELECT COUNT(*) FROM entry") or 0
        return {
            "available": True,
            "count": vec_count,
            "total_entries": entry_count,
            "coverage": f"{vec_count / entry_count * 100:.1f}%" if entry_count > 0 else "0%",
        }

    def get_embedded_rowids(self) -> set[int]:
        rows = self._exec("SELECT id FROM entry WHERE embedding IS NOT NULL")
        # Return entry IDs as a set (Postgres doesn't use rowids the same way)
        return {hash(r["id"]) for r in rows}

    def get_entries_for_embedding(self, kb_name: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT id, kb_name, title, summary, body FROM entry"
        params: dict[str, Any] = {}
        if kb_name:
            sql += " WHERE kb_name = :kb_name"
            params["kb_name"] = kb_name
        rows = self._exec(sql, params)
        # Add a synthetic rowid for protocol compatibility
        for i, r in enumerate(rows):
            r["rowid"] = i
        return rows

    def delete_embedding(self, entry_id: str, kb_name: str) -> None:
        self._session.execute(
            text("UPDATE entry SET embedding = NULL WHERE id = :entry_id AND kb_name = :kb_name"),
            {"entry_id": entry_id, "kb_name": kb_name},
        )
        self._session.commit()
