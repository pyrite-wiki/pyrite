"""local_user's columns since 006, and the kb_permission table.

Revision ID: 008
Revises: 007
Create Date: 2026-11-19

Answers SQLite migrations v7 (OAuth columns and the provider index), v8
(``kb_permission``, ``ephemeral_kb_count``), v10 (``usage_tier``), v14 (GitHub
token columns) and v26 (``global_access``) (#718).

**The v26 grant runs only when this revision adds the column.** SQLite's v26
sets ``global_access = 1`` for every user existing when it runs: they predate
self-registration scoping and keep what their role gave them. SQLite runs it
once, by version number. Here a database nobody stamped also starts at the
base of the chain; if it already has the column it is past v26, and its
self-registered users have 0 on purpose, so an unconditional ``UPDATE`` would
give them global read.
"""

from collections.abc import Sequence

from alembic import op

from pyrite.storage.alembic_chain import add_missing_columns, create_index, create_model_tables

revision: str = "008"
down_revision: str = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

sqlite_versions = (7, 8, 10, 14, 26)


def upgrade() -> None:
    create_model_tables("kb_permission")  # v8
    added = add_missing_columns(
        "local_user",
        {
            "auth_provider": "TEXT DEFAULT 'local'",  # v7
            "provider_id": "TEXT",  # v7
            "avatar_url": "TEXT",  # v7
            "ephemeral_kb_count": "INTEGER DEFAULT 0",  # v8
            "usage_tier": "TEXT DEFAULT 'default'",  # v10
            "github_access_token": "TEXT",  # v14
            "github_token_scopes": "TEXT",  # v14
            "global_access": "INTEGER NOT NULL DEFAULT 0",  # v26
        },
    )
    create_index(  # v7
        "idx_local_user_provider",
        "local_user",
        ["auth_provider", "provider_id"],
        unique=True,
        where="provider_id IS NOT NULL",
    )
    if "global_access" in added:
        op.execute("UPDATE local_user SET global_access = 1")


def downgrade() -> None:
    # Columns stay (SQLite before 3.35 cannot drop them), as in 005-007.
    pass
