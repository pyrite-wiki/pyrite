"""
SQLiteBackend — SearchBackend implementation backed by SQLite + FTS5 + sqlite-vec.

Inherits shared ORM/SQL logic from BaseBackend.  Only overrides:
- ``_exec`` / ``_exec_one`` / ``_exec_scalar`` (raw sqlite3 connection)
- ``_sync_links`` (diff-based sync)
- Full-text search (FTS5)
- Embedding operations (sqlite-vec)
"""

from __future__ import annotations

import copy
import struct
from typing import Any, ClassVar

from ..models import Link
from .base_backend import BaseBackend
from .capabilities import BackendCapability

#: sqlite-vec's hard ceiling on ``k`` in a KNN query. Asking for more is not a
#: slow query but an error — sqlite-vec 0.1.9 raises
#: ``OperationalError: k value in knn query too large, provided N and the limit
#: is 4096``. Every ``k`` the semantic leg builds is clamped against this; a
#: query can still under-return when more matching candidates exist than the
#: cap, or when distance culling removes candidates (#56, #194).
_SQLITE_VEC_MAX_K = 4096


class SQLiteBackend(BaseBackend):
    """SearchBackend implementation for SQLite + FTS5 + sqlite-vec."""

    # SQLite implements the full backend protocol: entity CRUD (BaseBackend),
    # FTS5 keyword search, and sqlite-vec embeddings. EMBEDDING is declared at
    # the class level ("can in principle"); whether sqlite-vec is loaded right
    # now is a separate runtime gate (``vec_available``). See capabilities.py.
    # FILTERED_SEMANTIC: ``search_semantic`` compiles the same predicates as
    # ``search`` into the KNN query, so a fused hybrid result can never contain
    # an entry the caller's filter excluded (#56).
    capabilities: ClassVar[set[BackendCapability]] = {
        BackendCapability.ENTITY,
        BackendCapability.SEARCH,
        BackendCapability.EMBEDDING,
        BackendCapability.FILTERED_SEMANTIC,
    }

    def __init__(
        self,
        session,
        raw_conn,
        vec_available: bool = False,
        owner=None,
    ):
        # `owner` is the PyriteDB that built this backend. When given,
        # `self._session` resolves through it to the *current scope's* session
        # rather than caching one object for the backend's whole lifetime —
        # the shared-session bug (#131). An explicit `session` is still
        # accepted for callers that construct a backend with one they own
        # (diff/overlay backends, tests).
        self._owner = owner
        self._explicit_session = session
        self._raw_conn = raw_conn
        self.vec_available = vec_available

    @property
    def _session(self):
        """The session for the current scope (see ``PyriteDB.session``)."""
        if self._owner is not None:
            return self._owner.session
        return self._explicit_session

    @_session.setter
    def _session(self, value):
        self._explicit_session = value

    def for_owner(self, owner) -> SQLiteBackend:
        """A copy of this backend whose session resolves through *owner*.

        Used by ``PyriteDB.request_handle()`` so a per-request handle's ORM
        reads use that request's session while still sharing the engine, pool
        and raw connection (#131).
        """
        clone = copy.copy(self)
        clone._owner = owner
        return clone

    def close(self) -> None:
        """No-op — connection lifecycle owned by PyriteDB."""

    # =====================================================================
    # Raw SQL helpers (sqlite3 positional-param style via _raw_conn)
    # =====================================================================

    def _raw_cursor(self):
        """Serialised private cursor on the shared raw sqlite3 connection.

        ``_raw_conn`` is one connection shared by every caller — the same
        lifetime shape the session had (#131 criterion 7). sqlite3 serialises
        individual statements, but ``Connection.execute()`` returns a cursor
        whose rows are fetched afterwards, and with
        ``check_same_thread=False`` two threads can interleave execute and
        fetch on one implicit cursor. A private cursor under the owner's lock
        removes that without opening a second connection per request.

        The cursor comes from *this backend's* ``_raw_conn``, while the lock
        comes from the owner. They are normally the same connection, but a
        caller may substitute the backend's -- the conformance suite's
        ``_spy_on_sql`` wraps it to record the SQL the KNN escalation loop
        sends. Delegating wholesale to the owner would take a cursor from the
        unwrapped connection and silently ignore the substitution, so the two
        are kept separate.

        Falls back to the bare connection when no owner supplied a lock (a
        backend constructed directly in a test).
        """
        if self._owner is not None:
            return self._owner._raw_cursor(self._raw_conn)

        from contextlib import nullcontext

        return nullcontext(self._raw_conn)

    def _exec(self, sql: str, params: dict | None = None) -> list[dict[str, Any]]:
        """Execute raw SQL via the raw sqlite3 connection.

        Translates ``:named`` params to ``?``-style for sqlite3.
        """
        sql_out, param_list = self._translate_params(sql, params)
        with self._raw_cursor() as cur:
            rows = cur.execute(sql_out, param_list).fetchall()
        return [dict(r) for r in rows]

    def _exec_one(self, sql: str, params: dict | None = None) -> dict | None:
        sql_out, param_list = self._translate_params(sql, params)
        with self._raw_cursor() as cur:
            row = cur.execute(sql_out, param_list).fetchone()
        if row is None:
            return None
        return dict(row)

    def _exec_scalar(self, sql: str, params: dict | None = None):
        sql_out, param_list = self._translate_params(sql, params)
        with self._raw_cursor() as cur:
            row = cur.execute(sql_out, param_list).fetchone()
        return row[0] if row else None

    @staticmethod
    def _translate_params(sql: str, params: dict | None) -> tuple[str, list]:
        """Convert ``:name`` placeholders to ``?`` with a positional param list.

        This lets the base class use ``:named`` style everywhere while
        SQLite's raw connection receives ``?``-style it expects.
        """
        if not params:
            return sql, []

        import re

        param_list: list[Any] = []

        # Match :word_chars but not ::double-colon (Postgres cast)
        def _replacer(m):
            name = m.group(1)
            param_list.append(params[name])
            return "?"

        sql_out = re.sub(r"(?<!:):([a-zA-Z_]\w*)", _replacer, sql)
        return sql_out, param_list

    # =====================================================================
    # _sync_links (diff-based — SQLite-specific)
    # =====================================================================

    def _sync_links(self, entry_id: str, kb_name: str, links: list[dict[str, Any]]) -> None:
        from ...schema import get_inverse_relation

        # Build desired state as a dict keyed by (target_id, target_kb, relation)
        desired: dict[tuple[str, str, str], dict[str, Any]] = {}
        for link in links:
            relation = link.get("relation", "related_to")
            target_kb = link.get("kb", kb_name)
            target_id = link.get("target", "")
            key = (target_id, target_kb, relation)
            desired[key] = {
                "note": link.get("note", ""),
                "inverse_relation": get_inverse_relation(relation),
            }

        # Load existing links from DB
        existing = self._session.query(Link).filter_by(source_id=entry_id, source_kb=kb_name).all()

        existing_keys: dict[tuple[str, str, str], Link] = {}
        for row in existing:
            key = (row.target_id, row.target_kb, row.relation)
            existing_keys[key] = row

        # Delete links no longer in desired set
        for key, row in existing_keys.items():
            if key not in desired:
                self._session.delete(row)

        # Add new links and update changed ones
        for key, attrs in desired.items():
            if key in existing_keys:
                # Update note/inverse if changed
                row = existing_keys[key]
                if row.note != attrs["note"] or row.inverse_relation != attrs["inverse_relation"]:
                    row.note = attrs["note"]
                    row.inverse_relation = attrs["inverse_relation"]
            else:
                # Insert new link
                target_id, target_kb, relation = key
                self._session.add(
                    Link(
                        source_id=entry_id,
                        source_kb=kb_name,
                        target_id=target_id,
                        target_kb=target_kb,
                        relation=relation,
                        inverse_relation=attrs["inverse_relation"],
                        note=attrs["note"],
                    )
                )

    # =====================================================================
    # Full-text search (FTS5)
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
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT
                e.id, e.kb_name, e.entry_type, e.title, e.body, e.summary,
                e.file_path, e.date, e.importance, e.status, e.location,
                e.lifecycle, e.metadata, e.created_at, e.updated_at, e.indexed_at,
                e.created_by, e.modified_by,
                snippet(entry_fts, 4, '<mark>', '</mark>', '...', 32) as snippet,
                bm25(entry_fts) as rank
            FROM entry_fts
            JOIN entry e ON entry_fts.rowid = e.rowid
            WHERE entry_fts MATCH ?
        """
        params: list[Any] = [query]
        if lifecycle:
            sql += " AND e.lifecycle = ?"
            params.append(lifecycle)
        elif not include_archived:
            sql += " AND COALESCE(e.lifecycle, 'active') != 'archived'"
        if kb_name:
            sql += " AND e.kb_name = ?"
            params.append(kb_name)
        if kb_names is not None:
            names = list(kb_names)
            if names:
                sql += f" AND e.kb_name IN ({','.join('?' * len(names))})"
                params.extend(names)
            else:
                sql += " AND 0"
        if entry_type:
            sql += " AND e.entry_type = ?"
            params.append(entry_type)
        if date_from:
            sql += " AND e.date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND e.date <= ?"
            params.append(date_to)
        if tags:
            tag_placeholders = ",".join(["?"] * len(tags))
            sql += f"""
                AND e.id IN (
                    SELECT et.entry_id FROM entry_tag et
                    JOIN tag t ON et.tag_id = t.id
                    WHERE t.name IN ({tag_placeholders})
                    GROUP BY et.entry_id, et.kb_name
                    HAVING COUNT(DISTINCT t.name) = ?
                )
            """
            params.extend(tags)
            params.append(len(tags))
        if fips:
            sql += " AND e.fips = ?"
            params.append(fips)
        if state:
            sql += " AND e.state = ?"
            params.append(state)
        if status:
            sql += " AND e.status = ?"
            params.append(status)
        sql += " ORDER BY rank LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._raw_cursor() as cur:
            rows = cur.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def search_by_tag(
        self, tag: str, kb_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT e.* FROM entry e
            JOIN entry_tag et ON e.id = et.entry_id AND e.kb_name = et.kb_name
            JOIN tag t ON et.tag_id = t.id
            WHERE t.name = ?
        """
        params: list[Any] = [tag]
        if kb_name:
            sql += " AND e.kb_name = ?"
            params.append(kb_name)
        sql += " ORDER BY e.date DESC, e.title LIMIT ?"
        params.append(limit)
        with self._raw_cursor() as cur:
            rows = cur.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def search_by_date_range(
        self,
        date_from: str,
        date_to: str,
        kb_name: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM entry WHERE date >= ? AND date <= ?"
        params: list[Any] = [date_from, date_to]
        if kb_name:
            sql += " AND kb_name = ?"
            params.append(kb_name)
        sql += " ORDER BY date ASC LIMIT ?"
        params.append(limit)
        with self._raw_cursor() as cur:
            rows = cur.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def search_by_tag_prefix(
        self, prefix: str, kb_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT DISTINCT e.* FROM entry e
            JOIN entry_tag et ON e.id = et.entry_id AND e.kb_name = et.kb_name
            JOIN tag t ON et.tag_id = t.id
            WHERE (t.name = ? OR t.name LIKE ?)
        """
        params: list[Any] = [prefix, prefix + "/%"]
        if kb_name:
            sql += " AND e.kb_name = ?"
            params.append(kb_name)
        sql += " ORDER BY e.date DESC, e.title LIMIT ?"
        params.append(limit)
        with self._raw_cursor() as cur:
            rows = cur.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # =====================================================================
    # Semantic search (sqlite-vec embeddings)
    # =====================================================================

    @staticmethod
    def _embedding_to_blob(embedding: list[float]) -> bytes:
        return struct.pack(f"{len(embedding)}f", *embedding)

    def upsert_embedding(self, entry_id: str, kb_name: str, embedding: list[float]) -> bool:
        if not self.vec_available:
            return False
        # One critical section: the rowid lookup, the delete and the insert
        # are a read-modify-write, and a concurrent writer between them would
        # orphan or duplicate the vector row.
        with self._raw_cursor() as cur:
            row = cur.execute(
                "SELECT rowid FROM entry WHERE id = ? AND kb_name = ?",
                (entry_id, kb_name),
            ).fetchone()
            if not row:
                return False
            rowid = row[0]
            blob = self._embedding_to_blob(embedding)
            cur.execute("DELETE FROM vec_entry WHERE rowid = ?", (rowid,))
            cur.execute("INSERT INTO vec_entry(rowid, embedding) VALUES (?, ?)", (rowid, blob))
            self._raw_conn.commit()
        return True

    def search_semantic(
        self,
        embedding: list[float],
        kb_name: str | None = None,
        limit: int = 20,
        max_distance: float = 1.3,
        entry_type: str | None = None,
        tags: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        fips: str | None = None,
        state: str | None = None,
        status: str | None = None,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        """KNN over ``vec_entry``, filtered by the same predicates as ``search``.

        sqlite-vec's ``MATCH`` needs a literal ``k`` (the number of nearest
        neighbours to consider). For a caller-supplied filter, the matching
        rowids are passed into the KNN query so unrelated neighbours do not
        consume that budget. The outer predicates remain as a consistency
        check. The query over-fetches and escalates ``k`` for distance culling,
        until ``limit`` rows survive, the filtered candidate set is exhausted,
        or ``k`` reaches :data:`_SQLITE_VEC_MAX_K`.

        sqlite-vec refuses ``k`` above 4096 outright. Filtering candidates
        before KNN avoids losing recall because unrelated rows occupied those
        4096 slots. A request for more than the cap, or a restrictive
        ``max_distance``, can still return fewer than ``limit`` results. The
        keyword leg, which has no KNN ceiling, is unaffected; in hybrid mode it
        carries its own result.

        The escalation costs one extra query per round, and only when the
        previous budget did not fill ``limit``: an unfiltered search whose
        nearest neighbours all survive ``max_distance`` runs exactly one query,
        the same as before filters were threaded through.
        """
        if not self.vec_available:
            return []
        blob = self._embedding_to_blob(embedding)

        where, params, selective = self._semantic_filter_sql(
            kb_name=kb_name,
            entry_type=entry_type,
            tags=tags,
            date_from=date_from,
            date_to=date_to,
            fips=fips,
            state=state,
            status=status,
            include_archived=include_archived,
        )

        # The KNN k-set is materialised in a CTE and its size carried on every
        # row *and* on a filter-independent probe row, so exhaustion is visible
        # even when the filter removes every neighbour: fewer neighbours back
        # than k asked for means the table is exhausted and escalating again
        # would be wasted. That is the exhaustion signal — an unconditional
        # ``COUNT(*) FROM vec_entry`` would instead scan the whole vector table
        # on every semantic search, filtered or not.
        # Apply caller filters inside the KNN query so unrelated rows cannot exhaust the
        # hard k cap; keep the unfiltered hot path free of this extra query.
        knn_filter = (
            f" AND rowid IN (SELECT e.rowid FROM entry e WHERE 1=1{where})" if selective else ""
        )
        candidate_params = params if selective else []
        sql = f"""
            WITH knn AS (
                SELECT rowid, distance
                FROM vec_entry
                WHERE embedding MATCH ? AND k = ?{knn_filter}
            ), knn_size AS (
                SELECT COUNT(*) AS n FROM knn
            )
            SELECT knn.rowid, knn.distance, e.*, knn_size.n AS _knn_size
            FROM knn
            JOIN entry e ON knn.rowid = e.rowid
            CROSS JOIN knn_size
            WHERE 1=1{where}
            ORDER BY knn.distance
        """
        size_sql = f"""
            WITH knn AS (
                SELECT rowid FROM vec_entry WHERE embedding MATCH ? AND k = ?{knn_filter}
            )
            SELECT COUNT(*) FROM knn
        """
        # Over-fetch more when a caller-supplied filter may cull the k nearest.
        # The archived exclusion is not counted: it applies to every search, so
        # treating it as a filter would triple the budget of every query.
        k = min(limit * 3 if selective else limit * 2, _SQLITE_VEC_MAX_K)
        results: list[dict[str, Any]] = []
        while True:
            with self._raw_cursor() as cur:
                rows = cur.execute(sql, [blob, k, *candidate_params, *params]).fetchall()
            results = []
            for row in rows:
                entry = dict(row)
                entry.pop("_knn_size", None)
                if entry.get("distance", 0) > max_distance:
                    continue
                results.append(entry)
                if len(results) >= limit:
                    break
            if len(results) >= limit or k >= _SQLITE_VEC_MAX_K:
                # Filled, or at sqlite-vec's hard ceiling — recall is
                # best-effort from here.
                break
            # Did the KNN itself run out of neighbours, or did the filter eat
            # them? Rows carry the k-set's size; only when the filter removed
            # every row must we ask separately.
            if rows:
                knn_size = rows[0]["_knn_size"]
            else:
                with self._raw_cursor() as cur:
                    knn_size = cur.execute(size_sql, (blob, k, *candidate_params)).fetchone()[0]
            if knn_size < k:
                # The table is exhausted: a larger k cannot find more.
                break
            k = min(k * 4, _SQLITE_VEC_MAX_K)
        return results

    @staticmethod
    def _semantic_filter_sql(
        kb_name: str | None = None,
        entry_type: str | None = None,
        tags: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        fips: str | None = None,
        state: str | None = None,
        status: str | None = None,
        include_archived: bool = False,
    ) -> tuple[str, list[Any], bool]:
        """Build the WHERE fragment shared by the semantic leg and ``search``.

        Deliberately mirrors the predicates in :meth:`search` one for one — the
        two legs are fused, so any divergence is a filter the caller asked for
        and did not get.

        Returns ``(sql, params, selective)``. ``selective`` says whether any
        *caller-supplied* filter is present; the archived exclusion does not
        count, because it is the default on both legs and sizing every KNN
        budget as if it were a filter would triple the work of an ordinary
        search.
        """
        sql = ""
        params: list[Any] = []
        if not include_archived:
            # Same default exclusion the keyword leg applies (#56): an archived
            # entry must not enter a fused result via the vector side.
            sql += " AND COALESCE(e.lifecycle, 'active') != 'archived'"
        if kb_name:
            sql += " AND e.kb_name = ?"
            params.append(kb_name)
        if entry_type:
            sql += " AND e.entry_type = ?"
            params.append(entry_type)
        if date_from:
            sql += " AND e.date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND e.date <= ?"
            params.append(date_to)
        if tags:
            placeholders = ",".join(["?"] * len(tags))
            sql += f"""
                AND e.id IN (
                    SELECT et.entry_id FROM entry_tag et
                    JOIN tag t ON et.tag_id = t.id
                    WHERE t.name IN ({placeholders})
                    GROUP BY et.entry_id, et.kb_name
                    HAVING COUNT(DISTINCT t.name) = ?
                )
            """
            params.extend(tags)
            params.append(len(tags))
        if fips:
            sql += " AND e.fips = ?"
            params.append(fips)
        if state:
            sql += " AND e.state = ?"
            params.append(state)
        if status:
            sql += " AND e.status = ?"
            params.append(status)
        selective = any((kb_name, entry_type, date_from, date_to, tags, fips, state, status))
        return sql, params, selective

    def has_embeddings(self) -> bool:
        if not self.vec_available:
            return False
        with self._raw_cursor() as cur:
            row = cur.execute("SELECT COUNT(*) FROM vec_entry").fetchone()
        return row[0] > 0

    def embedding_stats(self) -> dict[str, Any]:
        if not self.vec_available:
            return {"available": False, "count": 0, "total_entries": 0}
        with self._raw_cursor() as cur:
            vec_count = cur.execute("SELECT COUNT(*) FROM vec_entry").fetchone()[0]
            entry_count = cur.execute("SELECT COUNT(*) FROM entry").fetchone()[0]
        return {
            "available": True,
            "count": vec_count,
            "total_entries": entry_count,
            "coverage": f"{vec_count / entry_count * 100:.1f}%" if entry_count > 0 else "0%",
        }

    def get_embedded_rowids(self) -> set[int]:
        if not self.vec_available:
            return set()
        with self._raw_cursor() as cur:
            rows = cur.execute("SELECT rowid FROM vec_entry").fetchall()
        return {r[0] for r in rows}

    def get_entries_for_embedding(self, kb_name: str | None = None) -> list[dict[str, Any]]:
        with self._raw_cursor() as cur:
            if kb_name:
                rows = cur.execute(
                    "SELECT rowid, id, kb_name, title, summary, body FROM entry WHERE kb_name = ?",
                    (kb_name,),
                ).fetchall()
            else:
                rows = cur.execute(
                    "SELECT rowid, id, kb_name, title, summary, body FROM entry"
                ).fetchall()
        return [dict(r) for r in rows]

    def delete_embedding(self, entry_id: str, kb_name: str) -> None:
        if not self.vec_available:
            return
        with self._raw_cursor() as cur:
            row = cur.execute(
                "SELECT rowid FROM entry WHERE id = ? AND kb_name = ?",
                (entry_id, kb_name),
            ).fetchone()
            if row:
                cur.execute("DELETE FROM vec_entry WHERE rowid = ?", (row[0],))
                self._raw_conn.commit()
