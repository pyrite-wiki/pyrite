"""A dev-shaped database upgrades to the schema the current ORM reads (#718).

The alembic chain stopped at revision 006 while the ORM's ``entry`` table kept
growing: ``lifecycle`` (SQLite migration v11), ``content_hash`` (v21) and
``file_mtime_ns``/``file_size`` (v27). An existing database upgraded by the
new release is therefore unreadable — ``BaseBackend.get_entries_for_indexing``
selects the stat columns and every ORM upsert writes ``lifecycle``, and both
fail with "column does not exist".

The fixtures below rebuild exactly that starting state: every table the ORM
knows, minus the columns alembic has no revision for, stamped as alembic
revision 006. The test then runs a real ``alembic upgrade head`` and demands
the two things a Postgres deployment needs afterwards: the columns exist, and
the exact query ``get_entries_for_indexing`` issues succeeds against them.
"""

import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from pyrite.storage.backends.postgres_backend import create_postgres_engine
from pyrite.storage.models import Base, Entry


def _engine(url: str):
    """The engine the product builds for each URL (psycopg2 pinned — #373)."""
    if url.startswith("postgresql"):
        return create_postgres_engine(url)
    return create_engine(url)

ALEMBIC_DIR = Path(__file__).parents[1] / "pyrite" / "storage" / "alembic"
ALEMBIC_INI = Path(__file__).parents[1] / "pyrite" / "storage" / "alembic.ini"

# The entry columns every SQLite migration after v20 (alembic 006) has added.
_COLUMNS_MISSING_AT_006 = ("lifecycle", "content_hash", "file_mtime_ns", "file_size")


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _drop_columns_post_006(engine, table: str, columns: tuple[str, ...]) -> None:
    pg = engine.dialect.name == "postgresql"
    with engine.connect() as conn:
        for column in columns:
            kind = "BIGINT" if (pg and column in ("file_mtime_ns", "file_size")) else "INTEGER"
            if pg:
                # Postgres: running against its own drop callback needs the
                # cascade because 006-era indexes hang off some of them.
                conn.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column} CASCADE"))
            else:
                conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
        conn.commit()


@pytest.fixture(params=["sqlite", "postgres"])
def dev_shaped_006_url(request, tmp_path):
    """A URL pointing at a database in exactly the pre-007 state.

    Every ORM table exists (a dev install built its schema with
    ``Base.metadata.create_all`` the way ``PyriteDB.__init__`` does), but the
    entry columns alembic has no revision for are removed, and alembic itself
    is stamped at revision 006 — this is an instance upgraded to the release
    006 was current, about to be upgraded again.
    """
    if request.param == "postgres":
        url = os.environ.get("PYRITE_TEST_PG_URL")
        if not url:
            pytest.skip("PYRITE_TEST_PG_URL not set")
    else:
        url = f"sqlite:///{(tmp_path / 'dev_shaped.db').as_posix()}"

    engine = _engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    # Stamp the alembic version table the way a deployment managed up to 006
    # has it (create_all has never made this table; a dev-shaped database
    # carries it from the alembic-managed installs it was seeded by).
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"))
        conn.execute(text("DELETE FROM alembic_version"))
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('006')"))
    _drop_columns_post_006(engine, "entry", _COLUMNS_MISSING_AT_006)
    engine.dispose()
    return url


def test_upgrade_adds_every_entry_column_missing_at_006(dev_shaped_006_url):
    """``alembic upgrade head`` brings a rev-006 database to the ORM's schema."""
    cfg = _alembic_config(dev_shaped_006_url)
    command.upgrade(cfg, "head")

    engine = _engine(dev_shaped_006_url)
    try:
        inspector = sa.inspect(engine)
        columns = {c["name"]: c for c in inspector.get_columns("entry")}
        for column in _COLUMNS_MISSING_AT_006:
            assert column in columns, f"entry.{column} missing after upgrade head"

        # The alembic chain itself reached the new revision — no silently
        # stamped-at-006 upgrade that never moved.
        version = _engine(dev_shaped_006_url).connect().execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        assert version == "007"

        # v11 also indexed lifecycle; the index must survive the free-up too
        # or archive filtering table-scans.
        index_names = {i["name"] for i in inspector.get_indexes("entry")}
        assert "idx_entry_lifecycle" in index_names
    finally:
        engine.dispose()


def test_get_entries_for_indexing_query_runs_after_upgrade(dev_shaped_006_url):
    """The read the indexer issues, verbatim from get_entries_for_indexing,
    works on the upgraded database — and an ORM write (which sets
    ``lifecycle`` on every upsert) succeeds on it too."""

    # Apply the release upgrade before touching the database.
    command.upgrade(_alembic_config(dev_shaped_006_url), "head")

    engine = _engine(dev_shaped_006_url)
    try:
        with Session(engine) as session:
            # A KB and a row that predates the upgrade: the *upgraded*
            # database must read and write like the backend expects it to.
            with engine.begin() as conn:
                conn.execute(
                    text("INSERT INTO kb (name, kb_type, path) VALUES ('dev-kb', 'generic', '.')")
                )
                conn.execute(
                    text(
                        "INSERT INTO entry (id, kb_name, entry_type, title)"
                        " VALUES ('e1', 'dev-kb', 'note', 'old row')"
                    )
                )
            session.commit()

            # The exact column list of BaseBackend.get_entries_for_indexing.
            rows = session.execute(
                sa.select(
                    Entry.id,
                    Entry.file_path,
                    Entry.indexed_at,
                    Entry.content_hash,
                    Entry.file_mtime_ns,
                    Entry.file_size,
                ).filter_by(kb_name="dev-kb")
            ).all()
            assert [r[0] for r in rows] == ["e1"]

            # An ORM upsert — every Entry carries lifecycle, so a revision
            # that forgets it breaks the write path the same way.
            session.add(
                Entry(id="e2", kb_name="dev-kb", entry_type="note",
                      title="new row", body="written after the upgrade")
            )
            session.commit()
    finally:
        engine.dispose()
