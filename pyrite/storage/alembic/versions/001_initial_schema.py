"""Initial schema: the ORM tables, and FTS5 on SQLite.

Revision ID: 001
Revises: None
Create Date: 2026-02-21

The baseline creates every ORM table the database lacks from today's models
(``create_model_tables``), as ``PyriteDB`` does with ``create_all``. That makes
an empty database and one ``create_all`` built and nobody stamped both start
here (#718). Later revisions add a column only when it is missing, so tables
created whole here pass through them unchanged.

The FTS5 table and its triggers are SQLite's. Postgres's full-text search is
``entry.fts_vector`` and its trigger, which ``ensure_schema`` creates
(dialect-only objects, outside the chain).

Answers SQLite migrations v1 (FTS5), and v5, v6, v15, v16, v17, whose tables
(``block``, ``local_user``/``session``, ``review``, ``edge_endpoint``,
``worktree``) are ORM models that predate revision 006.
"""

from collections.abc import Sequence

from alembic import op

from pyrite.storage.alembic_chain import create_index, create_model_tables

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

sqlite_versions = (1, 5, 6, 15, 16, 17)

_MIGRATION_INDEXES = (
    ("idx_block_entry", "block", ["entry_id", "kb_name"]),  # v5
    ("idx_block_block_id", "block", ["block_id"]),  # v5
    ("idx_session_token", "session", ["token_hash"]),  # v6
    ("idx_session_user", "session", ["user_id"]),  # v6
    ("idx_session_expires", "session", ["expires_at"]),  # v6
    ("idx_local_user_username", "local_user", ["username"]),  # v6
    ("idx_review_entry", "review", ["entry_id", "kb_name"]),  # v15
    ("idx_review_content_hash", "review", ["content_hash"]),  # v15
    ("idx_review_reviewer", "review", ["reviewer"]),  # v15
    ("idx_edge_endpoint_edge", "edge_endpoint", ["edge_entry_id", "edge_entry_kb"]),  # v16
    ("idx_edge_endpoint_target", "edge_endpoint", ["endpoint_id", "endpoint_kb"]),  # v16
    ("idx_edge_endpoint_type", "edge_endpoint", ["edge_type", "edge_entry_kb"]),  # v16
    ("idx_worktree_user", "worktree", ["user_id"]),  # v17
    ("idx_worktree_kb", "worktree", ["kb_name"]),  # v17
    ("idx_worktree_status", "worktree", ["status"]),  # v17
)


def upgrade() -> None:
    create_model_tables()
    # The indexes SQLite's migrations create on those tables beside what the
    # models declare (v5, v6, v15, v16, v17), so both backends have them.
    for name, table, columns in _MIGRATION_INDEXES:
        create_index(name, table, columns)
    if op.get_bind().dialect.name != "sqlite":
        return

    # FTS5 virtual table and triggers (raw SQL, no ORM equivalent)
    op.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS entry_fts USING fts5(
            id, kb_name, entry_type, title, body, summary, location,
            content='entry', content_rowid='rowid',
            tokenize='porter unicode61'
        )
    """)

    op.execute("""
        CREATE TRIGGER IF NOT EXISTS entry_ai AFTER INSERT ON entry BEGIN
            INSERT INTO entry_fts(rowid, id, kb_name, entry_type, title, body, summary, location)
            VALUES (new.rowid, new.id, new.kb_name, new.entry_type, new.title,
                    COALESCE(new.body, ''), COALESCE(new.summary, ''), COALESCE(new.location, ''));
        END
    """)

    op.execute("""
        CREATE TRIGGER IF NOT EXISTS entry_ad AFTER DELETE ON entry BEGIN
            INSERT INTO entry_fts(entry_fts, rowid, id, kb_name, entry_type, title, body, summary, location)
            VALUES('delete', old.rowid, old.id, old.kb_name, old.entry_type, old.title,
                   COALESCE(old.body, ''), COALESCE(old.summary, ''), COALESCE(old.location, ''));
        END
    """)

    op.execute("""
        CREATE TRIGGER IF NOT EXISTS entry_au AFTER UPDATE ON entry BEGIN
            INSERT INTO entry_fts(entry_fts, rowid, id, kb_name, entry_type, title, body, summary, location)
            VALUES('delete', old.rowid, old.id, old.kb_name, old.entry_type, old.title,
                   COALESCE(old.body, ''), COALESCE(old.summary, ''), COALESCE(old.location, ''));
            INSERT INTO entry_fts(rowid, id, kb_name, entry_type, title, body, summary, location)
            VALUES (new.rowid, new.id, new.kb_name, new.entry_type, new.title,
                    COALESCE(new.body, ''), COALESCE(new.summary, ''), COALESCE(new.location, ''));
        END
    """)

    # Index the rows already in ``entry`` (a database create_all built and
    # nobody stamped). The triggers above delete a row's old values from the
    # index on every update; for a row that was never indexed, that delete
    # corrupts the external-content table ("database disk image is
    # malformed") (#718).
    op.execute("INSERT INTO entry_fts(entry_fts) VALUES ('rebuild')")


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    op.execute("DROP TRIGGER IF EXISTS entry_au")
    op.execute("DROP TRIGGER IF EXISTS entry_ad")
    op.execute("DROP TRIGGER IF EXISTS entry_ai")
    op.execute("DROP TABLE IF EXISTS entry_fts")
