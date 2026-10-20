"""Add the entry columns introduced after alembic revision 006.

Revision ID: 007
Revises: 006
Create Date: 2026-10-18

The alembic chain had stopped at 006 while the ORM's ``entry`` table kept
growing, so an existing database had no upgrade path at all: every ORM upsert
writes ``lifecycle``, and ``BaseBackend.get_entries_for_indexing`` selects
``content_hash``, ``file_mtime_ns`` and ``file_size`` — all four raise
"column does not exist" on a database upgraded across releases (#718). This
revision mirrors the SQLite migrations that added them: v11 (lifecycle),
v21 (content_hash), v27 (file_mtime_ns, file_size).

Columns are added only when missing, so the same revision is safe on a fresh
database whose tables were already created from the ORM, on a SQLite
database (the dialect the 001-006 revisions were written against) and on
Postgres.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: str = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# column -> (type on any dialect, mirrors SQLite migration)
_ENTRY_COLUMNS: dict[str, str] = {
    "lifecycle": "TEXT DEFAULT 'active'",  # v11 — archive support
    "content_hash": "VARCHAR(64)",  # v21 — hash-based staleness
    "file_mtime_ns": "BIGINT",  # v27 — reconcile staleness (ADR-0038)
    "file_size": "BIGINT",  # v27 — reconcile staleness (ADR-0038)
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("entry"):
        return
    existing = {c["name"] for c in inspector.get_columns("entry")}
    if "lifecycle" not in existing:
        op.execute("ALTER TABLE entry ADD COLUMN lifecycle TEXT DEFAULT 'active'")
        op.execute("CREATE INDEX IF NOT EXISTS idx_entry_lifecycle ON entry(lifecycle)")
    if "content_hash" not in existing:
        op.execute("ALTER TABLE entry ADD COLUMN content_hash VARCHAR(64)")
    for column in ("file_mtime_ns", "file_size"):
        if column not in existing:
            op.execute(f"ALTER TABLE entry ADD COLUMN {column} BIGINT")


def downgrade() -> None:
    # SQLite (the dialect revisions 001-006 were written for) cannot drop
    # columns; for safety this does nothing, same convention as 005 and 006.
    pass
