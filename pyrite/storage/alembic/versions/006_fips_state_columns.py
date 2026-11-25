"""Add fips and state columns to entry table for geographic filtering.

Revision ID: 006
Revises: 005
Create Date: 2026-04-10

Each column is added only when missing (#718), as in 005. Answers SQLite
migration v20.
"""

from collections.abc import Sequence

from pyrite.storage.alembic_chain import add_missing_columns, create_index

revision: str = "006"
down_revision: str = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

sqlite_versions = (20,)


def upgrade() -> None:
    add_missing_columns("entry", {"fips": "TEXT", "state": "TEXT"})
    create_index("idx_entry_fips", "entry", ["fips"])
    create_index("idx_entry_state", "entry", ["state"])


def downgrade() -> None:
    pass
