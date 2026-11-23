"""Drop idx_entry_embedding, the HNSW index on entry.embedding (Postgres only).

Revision ID: 013
Revises: 012
Create Date: 2026-11-21

Since #1821 Postgres semantic search sorts on ``(embedding <=> :vec) + 0``,
a key no index can supply: every query is an exact nearest-neighbour scan over
the rows its filters admit. The HNSW index served no query and was still
maintained on every write. Maintainer decision on #1822, option (a): drop it.
``ensure_schema`` no longer creates it, so the drop holds across restarts.

**Postgres only, no SQLite answer.** SQLite keeps vectors in ``vec_entry``
(sqlite-vec, `NO_REVISION` 2) and never had this index, so this revision
answers no SQLite migration: ``sqlite_versions`` is empty, and on SQLite both
directions do nothing.

The drop names this database's own schema (`own_schema`): an unqualified name
resolves through the search_path, and would drop another instance's index
when two share a database and this schema has none.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from pyrite.storage.alembic_chain import own_schema

revision: str = "013"
down_revision: str = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

sqlite_versions: tuple[int, ...] = ()


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(f'DROP INDEX IF EXISTS "{own_schema(bind)}".idx_entry_embedding')


def downgrade() -> None:
    """Recreate the index as ``ensure_schema`` built it before #1822, where
    there is an embedding column to put it on."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # information_schema, not the inspector: SQLAlchemy has no ``vector`` type
    # and warns on every column of it the inspector reflects.
    has_embedding = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = :schema"
            " AND table_name = 'entry' AND column_name = 'embedding'"
        ),
        {"schema": own_schema(bind)},
    ).first()
    if has_embedding is None:
        return
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_entry_embedding "
        "ON entry USING hnsw(embedding vector_cosine_ops)"
    )
