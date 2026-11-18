"""
Virtual Table Adapter for SQLite-specific FTS5 and sqlite-vec tables.

These virtual tables have no ORM equivalent, so we manage them via raw SQL.
In a future Postgres migration, this module gets a counterpart using
tsvector + pgvector.
"""

#: The columns of ``entry_fts``, in table order. The DDL and triggers below are
#: built from this tuple.
FTS_COLUMNS = ("id", "kb_name", "entry_type", "title", "body", "summary", "location")

#: The entry parts a keyword search reads as text, with the Postgres
#: ``tsvector`` weight of each (title ranks above summary above body). Declared
#: field values (``metadata``) and tags are in neither the SQLite FTS table nor
#: the Postgres vector, so a keyword search does not find them (#1757).
#: ``SearchService.search_response`` reports these names as ``searched``; the
#: semantic leg embeds the same three parts (``_entry_text``), so the list holds
#: in every mode.
KEYWORD_SEARCHED_WEIGHTS = (("title", "A"), ("summary", "B"), ("body", "C"))
KEYWORD_SEARCHED_PARTS = tuple(part for part, _ in KEYWORD_SEARCHED_WEIGHTS)


def postgres_fts_vector_expression(row: str = "NEW") -> str:
    """The ``fts_vector`` expression the Postgres trigger assigns, from the same
    parts ``searched`` names."""
    return " ||\n".join(
        f"setweight(to_tsvector('english', coalesce({row}.{part}, '')), '{weight}')"
        for part, weight in KEYWORD_SEARCHED_WEIGHTS
    )


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
