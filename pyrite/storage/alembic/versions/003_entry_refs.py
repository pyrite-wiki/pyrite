"""Add entry_ref table for typed object references.

Revision ID: 003
Revises: 002
Create Date: 2026-02-23

Adds entry_ref table to index object-ref field types from KB schemas,
enabling reverse lookups like "which entries reference this entry".

Rewritten for #718: the table comes from the model when missing (it was raw
SQLite DDL, ``AUTOINCREMENT``, which Postgres rejects).
"""

from collections.abc import Sequence

from alembic import op

from pyrite.storage.alembic_chain import create_index, create_model_tables

revision: str = "003"
down_revision: str = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

sqlite_versions = (4,)


def upgrade() -> None:
    create_model_tables("entry_ref")
    create_index("idx_entry_ref_source", "entry_ref", ["source_id", "source_kb"])
    create_index("idx_entry_ref_target", "entry_ref", ["target_id", "target_kb"])
    create_index("idx_entry_ref_field", "entry_ref", ["field_name"])


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS entry_ref")
