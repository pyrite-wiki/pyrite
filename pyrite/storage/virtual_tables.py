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
KEYWORD_SEARCHED_PARTS = tuple(part for part, _ in KEYWORD_SEARCHED_WEIGHTS)


def postgres_fts_vector_expression(row: str = "NEW") -> str:
    """The ``fts_vector`` expression the Postgres trigger assigns, from the same
    parts ``searched`` names."""
    return " ||\n".join(
        f"setweight(to_tsvector('english', coalesce({row}.{part}, '')), '{weight}')"
        for part, weight in KEYWORD_SEARCHED_WEIGHTS
    )


_COLUMN_FILTER = re.compile(r"(?<![\w])(-)?\s*(?:\{([^}]*)\}|([A-Za-z_]\w*))\s*:")
_PHRASE = re.compile(r'"(?:[^"]|"")*"')


def fts_columns_read(query: str) -> list[str]:
    """The ``entry_fts`` columns a MATCH of ``query`` reads, in table order.

    FTS5 reads ``title:hawk`` and ``{title body}: hawk`` as a search of the named
    columns only, and ``- title: hawk`` as every column but that one. ``query``
    is the text that reaches MATCH (after sanitizing, which quotes a stray
    ``word:``); a colon inside a quoted phrase is not a filter. A query that
    mixes filtered and unfiltered terms reads every column for the unfiltered
    ones; this reports the filtered columns, which understates, never overstates.
    """
    unquoted = _PHRASE.sub(" ", query)
    named: set[str] = set()
    excluded: set[str] = set()
    for negated, group, single in _COLUMN_FILTER.findall(unquoted):
        names = group.split() if group else [single]
        (excluded if negated else named).update(
            n.lower() for n in names if n.lower() in FTS_COLUMNS
        )
    if named:
        return [c for c in FTS_COLUMNS if c in named and c not in excluded]
    return [c for c in FTS_COLUMNS if c not in excluded]


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
