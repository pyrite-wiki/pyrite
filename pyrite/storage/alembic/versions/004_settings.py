"""Add settings table.

Revision ID: 004
Revises: 003
Create Date: 2026-02-23

004 originally revised 002, which forked the chain (002 had two children:
003 and 004), so `alembic upgrade head` failed with "Multiple heads are
present" (003, 006) on every database — there was no upgrade path at all
(#718). The parent is 003, making the chain linear 001..006 and leaving 007
as the single head.

The table comes from the model when missing (#718: it was raw SQLite DDL).
The ``setting`` table has no SQLite migration of its own: ``PyriteDB`` creates
it with ``create_all``, so this revision answers no version.
"""

from collections.abc import Sequence

from alembic import op

from pyrite.storage.alembic_chain import create_index, create_model_tables

revision: str = "004"
down_revision: str = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

sqlite_versions = ()


def upgrade() -> None:
    create_model_tables("setting")
    create_index("idx_setting_key", "setting", ["key"], unique=True)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS setting")
