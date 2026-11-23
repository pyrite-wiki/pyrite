"""A Postgres database from before #1001 takes entry aliases after `ensure_schema()`.

`entry.aliases` is new in #1001, and index queries/upserts reference it.
`create_all` creates a missing table but never alters an existing one, so
`ensure_schema()`, which every Postgres startup runs, is what must add the
column (#1011): since #718 it does so by running the alembic chain, whose
revision 012 adds it. Without it, an existing database fails when queried for
aliases.
"""

import json
import os

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from pyrite.storage.backends.postgres_backend import (
    PostgresBackend,
    create_postgres_engine,
    ensure_schema,
)
from pyrite.storage.models import Base


@pytest.fixture
def pre_1001_engine():
    """A schema of its own whose `entry` table has no `aliases` column, as one
    created before #1001 has. Suffixed with the xdist worker id so workers never
    share it."""
    pytest.importorskip("psycopg2")
    url = os.environ.get("PYRITE_TEST_PG_URL")
    if not url:
        pytest.skip("PYRITE_TEST_PG_URL not set")
    schema = f"pyrite_pre1001_{os.environ.get('PYTEST_XDIST_WORKER', 'serial')}"

    admin = create_postgres_engine(url)
    with admin.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public"))
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        conn.commit()
    engine = create_postgres_engine(url, connect_args={"options": f"-csearch_path={schema},public"})
    Base.metadata.create_all(engine)
    ensure_schema(engine)
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE entry DROP COLUMN aliases"))
        # A database from before #718 was never stamped: nothing ran alembic.
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(
            text(
                "INSERT INTO kb (name, kb_type, path, description) "
                "VALUES ('test', 'generic', '/tmp/test', 'Test KB')"
            )
        )
        conn.commit()

    yield engine

    engine.dispose()
    with admin.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()
    admin.dispose()


def test_ensure_schema_lets_an_existing_database_store_entry_aliases(pre_1001_engine):
    ensure_schema(pre_1001_engine)  # what the next startup runs

    session = sessionmaker(bind=pre_1001_engine)()
    try:
        backend = PostgresBackend(session, pre_1001_engine)
        backend.upsert_entry(
            {
                "id": "note",
                "kb_name": "test",
                "entry_type": "note",
                "title": "Note",
                "body": "Body text.",
                "summary": "",
                "tags": [],
                "sources": [],
                "links": [],
                "aliases": ["old-note", "legacy-note"],
                "metadata": {},
            }
        )
        rows = session.execute(text("SELECT aliases FROM entry WHERE id = 'note'")).fetchall()
    finally:
        session.close()
    assert len(rows) == 1
    assert json.loads(rows[0][0]) == ["old-note", "legacy-note"]
