"""A Postgres database from before #980 takes a link write after `ensure_schema()`.

`link.target_prefix` (the `kb:` prefix as the file wrote it) is new in #980,
and the Postgres link insert sets it. `create_all` creates a missing table but
never alters an existing one, and the alembic chain stops at 006 (#718), so
`ensure_schema()`, which every Postgres startup runs, is what must add the
column. Without it, the first link write on an existing database fails with
`column "target_prefix" of relation "link" does not exist`.
"""

import os

import pytest

pytest.importorskip("psycopg2")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from pyrite.storage.backends.postgres_backend import (  # noqa: E402
    PostgresBackend,
    create_postgres_engine,
    ensure_schema,
)
from pyrite.storage.models import Base  # noqa: E402


@pytest.fixture
def pre_980_engine():
    """A schema of its own whose `link` table has no `target_prefix`, as one
    created before #980 has. Suffixed with the xdist worker id, as
    `test_postgres_schema_scoping.py` does, so workers never share it."""
    url = os.environ.get("PYRITE_TEST_PG_URL")
    if not url:
        pytest.skip("PYRITE_TEST_PG_URL not set")
    schema = f"pyrite_pre980_{os.environ.get('PYTEST_XDIST_WORKER', 'serial')}"

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
        conn.execute(text("ALTER TABLE link DROP COLUMN target_prefix"))
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


def test_ensure_schema_lets_an_existing_database_store_a_link_prefix(pre_980_engine):
    ensure_schema(pre_980_engine)  # what the next startup runs

    session = sessionmaker(bind=pre_980_engine)()
    try:
        PostgresBackend(session, pre_980_engine).upsert_entry(
            {
                "id": "note",
                "kb_name": "test",
                "entry_type": "note",
                "title": "Note",
                "body": "See [[ops:runbook]].",
                "summary": "",
                "tags": [],
                "sources": [],
                "links": [
                    {"target": "runbook", "kb": "ops-kb", "prefix": "ops", "relation": "wikilink"}
                ],
                "metadata": {},
            }
        )
        rows = session.execute(
            text("SELECT target_kb, target_prefix FROM link WHERE source_id = 'note'")
        ).fetchall()
    finally:
        session.close()
    assert [tuple(r) for r in rows] == [("ops-kb", "ops")]
