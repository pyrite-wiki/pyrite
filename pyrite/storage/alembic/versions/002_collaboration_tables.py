"""Add collaboration tables: user, repo, workspace_repo, entry_version.

Revision ID: 002
Revises: 001
Create Date: 2026-02-22

Adds Phase 7 collaboration support:
- user table (GitHub identity)
- repo table (repo registry with fork tracking)
- workspace_repo table (user <-> repo membership)
- entry_version table (change history from git log)
- New columns on kb (repo_id, repo_subpath) and entry (created_by, modified_by)
- Sentinel 'local' user for non-authenticated setups

Rewritten for #718 to run on Postgres as well as SQLite: the tables come from
the models when missing (they were raw SQLite DDL, with an unquoted ``user``
that Postgres reserves), and each column is added only when missing (a failed
``ALTER`` inside ``try`` aborts a Postgres transaction).
"""

from collections.abc import Sequence

from alembic import op

from pyrite.storage.alembic_chain import add_missing_columns, create_index, create_model_tables

revision: str = "002"
down_revision: str = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

sqlite_versions = (3,)


def upgrade() -> None:
    create_model_tables("user", "repo", "workspace_repo", "entry_version")

    create_index("idx_entry_version_entry", "entry_version", ["entry_id", "kb_name"])
    create_index("idx_entry_version_commit", "entry_version", ["commit_hash"])
    create_index("idx_entry_version_author", "entry_version", ["author_github_login"])
    create_index("idx_entry_version_date", "entry_version", ["commit_date"])
    create_index("idx_repo_name", "repo", ["name"])
    create_index("idx_user_github_login", "user", ["github_login"])

    add_missing_columns(
        "kb",
        {
            "repo_id": "INTEGER REFERENCES repo(id) ON DELETE SET NULL",
            "repo_subpath": "TEXT DEFAULT ''",
        },
    )
    add_missing_columns("entry", {"created_by": "TEXT", "modified_by": "TEXT"})

    # Sentinel 'local' user for non-authenticated setups (SQLite v3 does the same).
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "INSERT OR IGNORE INTO user (github_login, github_id, display_name)"
            " VALUES ('local', 0, 'Local User')"
        )
    else:
        op.execute(
            'INSERT INTO "user" (github_login, github_id, display_name)'
            " VALUES ('local', 0, 'Local User') ON CONFLICT DO NOTHING"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS entry_version")
    op.execute("DROP TABLE IF EXISTS workspace_repo")
    op.execute("DROP TABLE IF EXISTS repo")
    op.execute('DROP TABLE IF EXISTS "user"')
    # Note: SQLite does not support DROP COLUMN, so kb.repo_id, kb.repo_subpath,
    # entry.created_by, entry.modified_by will remain but be unused after downgrade.
