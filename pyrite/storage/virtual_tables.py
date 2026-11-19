"""
Virtual Table Adapter for SQLite-specific FTS5 and sqlite-vec tables.

These virtual tables have no ORM equivalent, so we manage them via raw SQL.
In a future Postgres migration, this module gets a counterpart using
tsvector + pgvector.
"""

import re

#: The columns of ``entry_fts``, in table order. The DDL and triggers below are
#: built from this tuple.
FTS_COLUMNS = ("id", "kb_name", "entry_type", "title", "body", "summary", "location")

#: The entry parts the Postgres ``tsvector`` is built from, with the weight of
#: each (title ranks above summary above body). Declared field values
#: (``metadata``) and tags are in neither the SQLite FTS table nor this vector,
#: so a keyword search does not find them (#1757). Each backend reports what
#: its own keyword search reads through ``keyword_searched_parts``: Postgres
#: from these, SQLite from ``FTS_COLUMNS``.
KEYWORD_SEARCHED_WEIGHTS = (("title", "A"), ("summary", "B"), ("body", "C"))
#: The entry parts a vector is built from (`embedding_service._entry_text`); the body
#: is cut to the model's `max_body_chars`, so it is named as a prefix. Reported as
#: ``searched`` when only the vector leg ran (#1757). Kept here, not in
#: `embedding_service`, so the search service need not import the embedding stack.
EMBEDDED_PARTS = ("title", "summary", "body_prefix")

KEYWORD_SEARCHED_PARTS = tuple(part for part, _ in KEYWORD_SEARCHED_WEIGHTS)


def postgres_fts_vector_expression(row: str = "NEW") -> str:
    """The ``fts_vector`` expression the Postgres trigger assigns, from the same
    parts ``searched`` names."""
    return " ||\n".join(
        f"setweight(to_tsvector('english', coalesce({row}.{part}, '')), '{weight}')"
        for part, weight in KEYWORD_SEARCHED_WEIGHTS
    )


_ALL_COLUMNS = frozenset(FTS_COLUMNS)
_BAREWORD = re.compile(r"[^\s(){}\",:]+")
_QUOTED = re.compile(r'"(?:[^"]|"")*"')


class _NotParsedError(Exception):
    """The query's filter structure is not one this reader understands."""


def fts_columns_read(query: str) -> list[str]:
    """The ``entry_fts`` columns a MATCH of ``query`` reads, in table order.

    Never more than the MATCH reads: ``searched`` is a claim a reader acts on,
    so this errs by naming too few, never too many (#1757). ``query`` is the
    text that reaches MATCH (after sanitizing, which quotes a stray ``word:``).

    FTS5 binds a column filter to the one operand that follows it, a phrase, a
    parenthesised group or a ``NEAR(...)``: ``title: hawk falcon`` filters
    ``hawk`` and reads every column for ``falcon``. The filter is ``col:``,
    ``"col":`` (quoted), ``{a b}:`` or, with a leading ``-``, every column but
    those. Filters nest by intersection: ``{title body}: ({body summary}: x)``
    reads ``body`` only. The columns read are the union, over every term, of the
    columns that term is read in. A query this reader cannot follow reads
    ``[]`` (nothing claimed); a query with no filter reads every column.
    """
    if ":" not in query:
        return list(FTS_COLUMNS)
    try:
        read = _columns_read(query)
    except _NotParsedError:
        return []
    return [c for c in FTS_COLUMNS if c in read]


def _columns_read(query: str) -> frozenset[str]:
    scopes = [_ALL_COLUMNS]  # the columns each open group leaves readable
    near: list[bool] = []  # per open group: is it a NEAR(...)
    pending: frozenset[str] | None = None  # a filter waiting for its operand
    last_applied: frozenset[str] | None = None  # for a `+` chain
    skip_number = False
    read: set[str] = set()
    i, n = 0, len(query)

    def take_filter(spec: frozenset[str]) -> None:
        nonlocal pending
        if pending is not None:
            raise _NotParsedError
        pending = spec

    def colspec_at(j: int) -> tuple[frozenset[str], int] | None:
        """A ``name:``, ``"name":`` or ``{a b}:`` at ``j``: (names, index after the colon)."""
        if query[j] == "{":
            end = query.find("}", j)
            if end < 0:
                raise _NotParsedError
            names = [a or b for a, b in re.findall(r'"([^"]*)"|(\S+)', query[j + 1 : end])]
            k = end + 1
        elif query[j] == '"':
            m = _QUOTED.match(query, j)
            if not m:
                raise _NotParsedError
            names, k = [m.group()[1:-1]], m.end()
        else:
            m = _BAREWORD.match(query, j)
            if not m:
                return None
            names, k = [m.group()], m.end()
        while k < n and query[k].isspace():
            k += 1
        if k >= n or query[k] != ":":
            return None
        lowered = [x.lower() for x in names]
        if not lowered or any(x not in _ALL_COLUMNS for x in lowered):
            raise _NotParsedError
        return frozenset(lowered), k + 1

    def term() -> None:
        nonlocal pending, last_applied
        effective = scopes[-1] if pending is None else scopes[-1] & pending
        last_applied, pending = pending, None
        read.update(effective)

    while i < n:
        c = query[i]
        if c.isspace():
            i += 1
        elif c == "(":
            scopes.append(scopes[-1] if pending is None else scopes[-1] & pending)
            near.append(False)
            last_applied, pending = pending, None
            i += 1
        elif c == ")":
            if len(scopes) == 1 or pending is not None:
                raise _NotParsedError
            scopes.pop()
            near.pop()
            i += 1
        elif c == ",":
            if not near or not near[-1]:
                raise _NotParsedError
            skip_number = True
            i += 1
        elif c == ":":
            raise _NotParsedError
        elif c == "-":
            j = i + 1
            while j < n and query[j].isspace():
                j += 1
            found = colspec_at(j) if j < n else None
            if found is None:
                raise _NotParsedError
            take_filter(_ALL_COLUMNS - found[0])
            i = found[1]
        elif c in '{"':
            found = colspec_at(i)
            if found is not None:
                take_filter(found[0])
                i = found[1]
            elif c == '"':
                m = _QUOTED.match(query, i)
                if not m:
                    raise _NotParsedError
                i = m.end()
                term()
            else:
                raise _NotParsedError
        else:
            m = _BAREWORD.match(query, i)
            if not m:
                raise _NotParsedError
            word = m.group()
            found = colspec_at(i)
            if found is not None:
                take_filter(found[0])
                i = found[1]
                continue
            i = m.end()
            if word in ("AND", "OR", "NOT") or set(word) <= set("^*"):
                continue
            if word == "+":
                pending = last_applied
                continue
            if word == "NEAR":
                k = i
                while k < n and query[k].isspace():
                    k += 1
                if k < n and query[k] == "(":
                    scopes.append(scopes[-1] if pending is None else scopes[-1] & pending)
                    near.append(True)
                    last_applied, pending = pending, None
                    i = k + 1
                    continue
            if skip_number:
                skip_number = False
                continue
            term()
    if len(scopes) != 1 or pending is not None or not read:
        raise _NotParsedError
    return frozenset(read)


_COLS = ", ".join(FTS_COLUMNS)
_COLS_DECL = ",\n    ".join(FTS_COLUMNS)


def _fts_value(row: str, column: str) -> str:
    return (
        f"{row}.{column}"
        if column in ("id", "kb_name", "entry_type", "title")
        else (f"COALESCE({row}.{column}, '')")
    )


def _values(row: str) -> str:
    return ", ".join(_fts_value(row, c) for c in FTS_COLUMNS)


FTS_SCHEMA_SQL = f"""
-- Full-text search index
CREATE VIRTUAL TABLE IF NOT EXISTS entry_fts USING fts5(
    {_COLS_DECL},
    content='entry',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

-- FTS triggers for automatic sync
CREATE TRIGGER IF NOT EXISTS entry_ai AFTER INSERT ON entry BEGIN
    INSERT INTO entry_fts(rowid, {_COLS})
    VALUES (new.rowid, {_values("new")});
END;

CREATE TRIGGER IF NOT EXISTS entry_ad AFTER DELETE ON entry BEGIN
    INSERT INTO entry_fts(entry_fts, rowid, {_COLS})
    VALUES('delete', old.rowid, {_values("old")});
END;

CREATE TRIGGER IF NOT EXISTS entry_au AFTER UPDATE ON entry BEGIN
    INSERT INTO entry_fts(entry_fts, rowid, {_COLS})
    VALUES('delete', old.rowid, {_values("old")});
    INSERT INTO entry_fts(rowid, {_COLS})
    VALUES (new.rowid, {_values("new")});
END;
"""

VEC_SCHEMA_SQL = "CREATE VIRTUAL TABLE IF NOT EXISTS vec_entry USING vec0(embedding float[384])"


def create_fts_tables(connection) -> None:
    """Create FTS5 virtual table and sync triggers on a raw sqlite3 connection."""
    connection.executescript(FTS_SCHEMA_SQL)
    connection.commit()


def create_vec_table(connection) -> None:
    """Create sqlite-vec virtual table on a raw sqlite3 connection."""
    existing = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_entry'"
    ).fetchone()
    if not existing:
        connection.execute(VEC_SCHEMA_SQL)
        connection.commit()
