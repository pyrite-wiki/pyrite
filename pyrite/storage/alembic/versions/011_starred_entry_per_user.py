"""Scope starred_entry per user: user_id, unique per (user, entry).

Revision ID: 011
Revises: 010
Create Date: 2026-11-19

Answers SQLite migration v24 (#718). SQLite rebuilds the table because it
cannot alter a constraint; here the column is added in place, ``NOT NULL
DEFAULT 0``, so every existing star becomes user 0's (the instance's own list,
as v24 does), and the unique constraint ``uq_starred_entry`` moves from
``(entry_id, kb_name)`` to ``(user_id, entry_id, kb_name)``. Rows are kept.
Batch mode does the constraint swap on SQLite by copying the table, also
keeping rows. A table that already has ``user_id`` is past v24 and is left
alone.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from pyrite.storage.alembic_chain import create_index, existing_columns

revision: str = "011"
down_revision: str = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

sqlite_versions = (24,)


def upgrade() -> None:
    columns = existing_columns("starred_entry")
    if columns is not None and "user_id" not in columns:
        with op.batch_alter_table("starred_entry") as batch:
            batch.add_column(sa.Column("user_id", sa.Integer, nullable=False, server_default="0"))
            batch.drop_constraint("uq_starred_entry", type_="unique")
            batch.create_unique_constraint("uq_starred_entry", ["user_id", "entry_id", "kb_name"])
    create_index("idx_starred_entry_user", "starred_entry", ["user_id", "sort_order"])


def downgrade() -> None:
    # Not reversible without losing stars two users share (as SQLite's v24).
    pass
