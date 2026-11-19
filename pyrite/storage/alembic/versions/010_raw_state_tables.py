"""The four state tables SQLite creates in raw SQL: invite_code, user_api_key,
oauth_state, llm_usage.

Revision ID: 010
Revises: 009
Create Date: 2026-11-19

Answers SQLite migrations v18, v19, v22 and v23 (#718). These tables are not
ORM models, so neither ``create_all`` nor any earlier revision makes them on
Postgres. Each is created only when missing, with the columns, keys, defaults
and indexes of its SQLite migration. They hold invitations, users' API keys,
OAuth state and usage quotas: state, never rebuilt from files, so this
revision never drops one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from pyrite.storage.alembic_chain import create_index, existing_columns

revision: str = "010"
down_revision: str = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

sqlite_versions = (18, 19, 22, 23)

_NOW = sa.text("CURRENT_TIMESTAMP")


def _create(name: str, *columns_and_constraints: sa.schema.SchemaItem) -> None:
    if existing_columns(name) is None:
        op.create_table(name, *columns_and_constraints)


def upgrade() -> None:
    _create(  # v18
        "invite_code",
        sa.Column("code", sa.Text, primary_key=True),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", sa.Text, server_default=_NOW),
        sa.Column("expires_at", sa.Text),
        sa.Column("used_by", sa.Text),
        sa.Column("used_at", sa.Text),
        sa.Column("role", sa.Text, server_default="write"),
        sa.Column("note", sa.Text, server_default=""),
    )
    create_index("idx_invite_code_used", "invite_code", ["used_by"])

    _create(  # v19
        "user_api_key",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("encrypted_key", sa.Text, nullable=False),
        sa.Column("model", sa.Text, server_default=""),
        sa.Column("created_at", sa.Text, server_default=_NOW),
        sa.Column("updated_at", sa.Text, server_default=_NOW),
        sa.UniqueConstraint("user_id", "provider"),
    )
    create_index("idx_user_api_key_user", "user_api_key", ["user_id"])

    _create(  # v22
        "oauth_state",
        sa.Column("state", sa.Text, primary_key=True),
        sa.Column("flow", sa.Text, nullable=False, server_default="login"),
        sa.Column("user_id", sa.Integer),
        sa.Column("created_at", sa.Text, server_default=_NOW),
        sa.Column("expires_at", sa.Text, nullable=False),
    )
    create_index("idx_oauth_state_expires", "oauth_state", ["expires_at"])

    _create(  # v23
        "llm_usage",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False, server_default="chat"),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_creation_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text, nullable=False, server_default=_NOW),
    )
    create_index("idx_llm_usage_user", "llm_usage", ["user_id", "created_at"])


def downgrade() -> None:
    # State tables: a downgrade does not drop them (SQLite's own down
    # migrations would, but nothing here rebuilds what they hold).
    pass
