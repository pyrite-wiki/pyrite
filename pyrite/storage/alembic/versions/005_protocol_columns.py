"""Add protocol columns to entry table (ADR-0017).

Revision ID: 005
Revises: 004
Create Date: 2026-03-03

Each column is added only when missing (#718): on a database whose ``entry``
came from ``create_all`` they already exist, and the unconditional ``ALTER``
this revision used to run failed there. Answers SQLite migration v9.
"""

from collections.abc import Sequence

from pyrite.storage.alembic_chain import add_missing_columns, create_index

revision: str = "005"
down_revision: str = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

sqlite_versions = (9,)


def upgrade() -> None:
    add_missing_columns(
        "entry",
        {
            # Assignable protocol
            "assignee": "TEXT",
            "assigned_at": "TEXT",
            # Prioritizable protocol
            "priority": "INTEGER",
            # Temporal protocol (date, status, location already exist)
            "due_date": "TEXT",
            "start_date": "TEXT",
            "end_date": "TEXT",
            # Locatable protocol (location already exists)
            "coordinates": "TEXT",
        },
    )
    # Indexes for common query patterns
    create_index("idx_entry_assignee", "entry", ["assignee"])
    create_index("idx_entry_priority", "entry", ["priority"])
    create_index("idx_entry_due_date", "entry", ["due_date"])


def downgrade() -> None:
    # SQLite doesn't support DROP COLUMN before 3.35.0
    # For safety, we do nothing on downgrade
    pass
