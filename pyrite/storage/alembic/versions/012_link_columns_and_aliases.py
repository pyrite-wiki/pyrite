"""link.target_prefix, entry.aliases, link.target_path/target_resolved.

Revision ID: 012
Revises: 011
Create Date: 2026-11-19

Answers SQLite migrations v28 (``link.target_prefix``, #980), v29
(``entry.aliases``, #1001) and v30 (``link.target_path``,
``link.target_resolved``, ``idx_link_target_path``, ``idx_entry_kb_file_path``,
#1723) (#718). Until this revision ``ensure_schema`` added these on every
Postgres startup; it now runs the chain instead, so this is their one writer.

**The v30 re-read.** A sync never re-reads a file whose recorded stat is
unchanged, so a database from before ``target_path`` would never get the
Markdown link rows of its unchanged files. When this revision adds
``target_path`` it clears every recorded stat, once, so the next sync reads
each file (decision D3, as SQLite's v30 and, before this, ``ensure_schema``
did). A database that already has the column keeps its stat.
"""

from collections.abc import Sequence

from alembic import op

from pyrite.storage.alembic_chain import add_missing_columns, create_index, existing_columns

revision: str = "012"
down_revision: str = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

sqlite_versions = (28, 29, 30)


def upgrade() -> None:
    added = add_missing_columns(
        "link",
        {
            "target_prefix": "TEXT",  # v28
            "target_path": "TEXT",  # v30
            "target_resolved": "INTEGER",  # v30
        },
    )
    add_missing_columns("entry", {"aliases": "TEXT"})  # v29
    create_index("idx_link_target_path", "link", ["target_kb", "target_path"])
    create_index("idx_entry_kb_file_path", "entry", ["kb_name", "file_path"])

    entry = existing_columns("entry") or set()
    if "target_path" in added and {"file_mtime_ns", "file_size"} <= entry:
        op.execute("UPDATE entry SET file_mtime_ns = NULL, file_size = NULL")


def downgrade() -> None:
    pass
