"""Add settings table.

Revision ID: 004
Revises: 003
Create Date: 2026-02-23

004 originally revised 002, which forked the chain (002 had two children:
003 and 004), so `alembic upgrade head` failed with "Multiple heads are
present" (003, 006) on every database — there was no upgrade path at all
(#718). The parent is 003, making the chain linear 001..006 and leaving 007
as the single head.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "004"
down_revision: str = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS setting (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_setting_key ON setting(key)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS setting")
