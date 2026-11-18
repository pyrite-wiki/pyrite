"""A Postgres index from before #1723 gets its Markdown link rows from a plain sync.

`link.target_path` is new in #1723. `create_all` never alters an existing
table, so `ensure_schema()` (every Postgres startup) adds the column. A sync
never re-reads a file whose recorded stat is unchanged, so an old database
would get no Markdown link rows for its unchanged files: when the column is
new, `ensure_schema()` also clears the recorded stat, once, so the next sync
reads every file (decision D3, as SQLite's migration v30 does). A database
that already has the column keeps its stat: a restart must not re-read a KB.
"""

import os

import pytest

pytest.importorskip("psycopg2")

from sqlalchemy import text  # noqa: E402

from pyrite.storage.backends.postgres_backend import (  # noqa: E402
    create_postgres_engine,
    ensure_schema,
)
from pyrite.storage.models import Base  # noqa: E402


@pytest.fixture
def pre_1723_engine():
    """A schema of its own whose `link` table has no `target_path`, holding one
    entry with its stat recorded, as a 0.26.4 database does."""
    url = os.environ.get("PYRITE_TEST_PG_URL")
    if not url:
        pytest.skip("PYRITE_TEST_PG_URL not set")
    schema = f"pyrite_pre1723_{os.environ.get('PYTEST_XDIST_WORKER', 'serial')}"
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
        conn.execute(text("ALTER TABLE link DROP COLUMN target_path"))
        conn.execute(
            text(
                "INSERT INTO kb (name, kb_type, path, description) "
                "VALUES ('test', 'generic', '/tmp/test', 'Test KB')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO entry (id, kb_name, entry_type, title, file_path, file_mtime_ns,"
                " file_size) VALUES ('a', 'test', 'note', 'A', '/tmp/test/a.md', 123, 45)"
            )
        )
        conn.commit()
    yield engine
    engine.dispose()
    with admin.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()
    admin.dispose()


def _stat(engine):
    with engine.connect() as conn:
        return tuple(conn.execute(text("SELECT file_mtime_ns, file_size FROM entry")).one())


def test_the_column_is_added_and_the_stat_cleared_once(pre_1723_engine):
    ensure_schema(pre_1723_engine)  # what the next startup runs
    with pre_1723_engine.connect() as conn:
        columns = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema = current_schema() AND table_name = 'link'"
                )
            )
        }
    assert "target_path" in columns
    assert _stat(pre_1723_engine) == (None, None)


def test_a_database_that_has_the_column_keeps_its_stat(pre_1723_engine):
    ensure_schema(pre_1723_engine)
    with pre_1723_engine.connect() as conn:
        conn.execute(text("UPDATE entry SET file_mtime_ns = 123, file_size = 45"))
        conn.commit()
    ensure_schema(pre_1723_engine)  # a later restart
    assert _stat(pre_1723_engine) == (123, 45)
