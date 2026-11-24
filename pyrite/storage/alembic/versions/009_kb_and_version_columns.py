"""kb.source, kb.default_role and entry_version.file_path.

Revision ID: 009
Revises: 008
Create Date: 2026-11-19

Answers SQLite migrations v12 (``kb.source``), v13 (``kb.default_role``) and
v25 (``entry_version.file_path``) (#718). v25 also rewrote absolute paths to
KB-relative ones, a repair for rows only a pre-release SQLite build wrote; no
Postgres database holds them, so only the column is added here.
"""

from collections.abc import Sequence

from pyrite.storage.alembic_chain import add_missing_columns

revision: str = "009"
down_revision: str = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

sqlite_versions = (12, 13, 25)


def upgrade() -> None:
    add_missing_columns("kb", {"source": "TEXT DEFAULT 'user'", "default_role": "TEXT"})
    add_missing_columns("entry_version", {"file_path": "TEXT"})


def downgrade() -> None:
    pass
