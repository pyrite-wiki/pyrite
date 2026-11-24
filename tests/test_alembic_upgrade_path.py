"""The alembic chain reaches the schema this release declares, from any earlier one (#718).

Alembic is the upgrade path for Postgres; SQLite keeps ``MigrationManager``
(``pyrite/storage/migrations.py``). The property, per the maintainer's
decision of 2026-10-23 on #718:

- every SQLite migration is answered once on the alembic side, by a revision
  (its ``sqlite_versions``) or by a declared reason it needs none
  (``pyrite.storage.alembic_chain.NO_REVISION``), so a new migration without
  its answer fails here;
- the chain has a single head;
- ``alembic upgrade head`` from an empty database, from a pre-007 one (stamped
  006, as #1021 found them) and from one ``create_all`` built and nobody
  stamped reaches the tables, columns and indexes ``PyriteDB`` builds on
  SQLite -- the oracle is the SQLite path, never the revisions themselves;
- users, grants and stars survive it, with the data steps SQLite's
  migrations take (v24, v26, v30), and only those.

The SQLite cases run everywhere. The Postgres cases run where
``PYRITE_TEST_PG_URL`` is set (CI's ``postgres`` job, #1791), each in a schema
of its own.

Contributed as the 006 -> 007 regression test by Rudra (#1021).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from pyrite.storage.migrations import MIGRATIONS
from pyrite.storage.models import Base, Entry

SCRIPT_LOCATION = Path(__file__).parents[1] / "pyrite" / "storage" / "alembic"


def alembic_config(connection=None) -> Config:
    """A Config for the chain, built here from alembic's own API so these tests
    need nothing from the code under test to reach the chain. The connection
    goes to ``env.py`` (``config.attributes``); its URL is given too, for an
    ``env.py`` that builds its own engine."""
    cfg = Config()
    cfg.set_main_option("script_location", str(SCRIPT_LOCATION))
    if connection is not None:
        cfg.attributes["connection"] = connection
        url = connection.engine.url.render_as_string(hide_password=False)
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


# Objects only one dialect has, by design: SQLite's FTS5 and sqlite-vec
# tables and its migration bookkeeping; alembic's own bookkeeping.
_SQLITE_ONLY_TABLE_PREFIXES = ("entry_fts", "vec_entry", "sqlite_")
_BOOKKEEPING_TABLES = {"schema_version", "alembic_version"}

# Every column a SQLite migration from v7 on adds to a table that existed at
# revision 006, written out from MIGRATIONS' descriptions (not from the
# revisions): what a database stamped 006 can lack.
_COLUMNS_ADDED_AFTER_006 = {
    "local_user": (
        "auth_provider",  # v7
        "provider_id",  # v7
        "avatar_url",  # v7
        "ephemeral_kb_count",  # v8
        "usage_tier",  # v10
        "github_access_token",  # v14
        "github_token_scopes",  # v14
        "global_access",  # v26
    ),
    "entry": (
        "lifecycle",  # v11
        "content_hash",  # v21
        "file_mtime_ns",  # v27
        "file_size",  # v27
        "aliases",  # v29
    ),
    "kb": ("source", "default_role"),  # v12, v13
    "entry_version": ("file_path",),  # v25
    "link": ("target_prefix", "target_path", "target_resolved"),  # v28, v30
}
# Tables created by raw SQL in migrations v18, v19, v22, v23: not ORM models,
# so nothing but a revision creates them on Postgres.
_RAW_TABLES = ("invite_code", "user_api_key", "oauth_state", "llm_usage")


# ---------------------------------------------------------------------------
# The oracle: what PyriteDB builds on SQLite
# ---------------------------------------------------------------------------


def _plugin_tables() -> set[str]:
    from pyrite.plugins import get_registry

    return {t["name"] for t in get_registry().get_all_db_tables()}


def _comparable(name: str) -> bool:
    return (
        name not in _BOOKKEEPING_TABLES
        and not name.startswith(_SQLITE_ONLY_TABLE_PREFIXES)
        and name not in _plugin_tables()
    )


def _schema(engine) -> dict[str, set[str]]:
    """``{table: {column, ...}}`` for every comparable table."""
    inspector = sa.inspect(engine)
    return {
        table: {c["name"] for c in inspector.get_columns(table)}
        for table in inspector.get_table_names()
        if _comparable(table)
    }


def _named_indexes(engine) -> set[str]:
    """The ``idx_``/``ix_`` indexes on comparable tables (constraint-backed and
    automatic indexes are named per dialect and are not compared)."""
    inspector = sa.inspect(engine)
    return {
        index["name"]
        for table in inspector.get_table_names()
        if _comparable(table)
        for index in inspector.get_indexes(table)
        if index["name"] and index["name"].startswith(("idx_", "ix_"))
    }


@pytest.fixture(scope="module")
def sqlite_oracle(tmp_path_factory):
    """Tables, columns and indexes of a database ``PyriteDB`` built and migrated."""
    from pyrite.storage.database import PyriteDB

    path = tmp_path_factory.mktemp("oracle") / "oracle.db"
    PyriteDB(path).close()
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        return _schema(engine), _named_indexes(engine)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Databases under test
# ---------------------------------------------------------------------------


def _pg_url() -> str | None:
    return os.environ.get("PYRITE_TEST_PG_URL")


@pytest.fixture(params=["sqlite", "postgres"])
def engine(request, tmp_path):
    """An empty database: a fresh SQLite file, or a fresh Postgres schema."""
    if request.param == "sqlite":
        eng = create_engine(f"sqlite:///{(tmp_path / 'alembic.db').as_posix()}")
        yield eng
        eng.dispose()
        return
    url = _pg_url()
    if not url:
        pytest.skip("PYRITE_TEST_PG_URL not set")
    from pyrite.storage.backends.postgres_backend import create_postgres_engine

    schema = f"pyrite_alembic_{os.environ.get('PYTEST_XDIST_WORKER', 'serial')}"
    admin = create_postgres_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    eng = create_postgres_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    yield eng
    eng.dispose()
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    admin.dispose()


def _upgrade(engine) -> None:
    """``alembic upgrade head`` on this engine's connection."""
    with engine.begin() as conn:
        cfg = alembic_config(conn)
        command.upgrade(cfg, "head")


def _version(engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def _head() -> str:
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    assert len(heads) == 1, heads
    return heads[0]


def _drop_column(conn, table: str, column: str) -> None:
    # Indexes on the column go first: SQLite refuses to drop an indexed column.
    for index in sa.inspect(conn).get_indexes(table):
        if column in index["column_names"]:
            conn.execute(text(f'DROP INDEX "{index["name"]}"'))
    conn.execute(text(f'ALTER TABLE "{table}" DROP COLUMN "{column}"'))


def _pre_v24_starred_entry() -> sa.Table:
    """``starred_entry`` as the model declared it before v24: no owner, one
    star per entry."""
    return sa.Table(
        "starred_entry",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("entry_id", sa.String, nullable=False),
        sa.Column("kb_name", sa.String, nullable=False),
        sa.Column("sort_order", sa.Integer, default=0),
        sa.Column("created_at", sa.String, nullable=False),
        sa.UniqueConstraint("entry_id", "kb_name", name="uq_starred_entry"),
        sa.Index("idx_starred_entry_kb", "kb_name"),
        sa.Index("idx_starred_entry_sort", "sort_order"),
    )


def _make_pre_007(engine) -> None:
    """A database in the state #1021 found it: at revision 006, without
    every table and column a SQLite migration added after 006, with
    ``starred_entry`` unowned and unique per entry. It holds a user, a grant,
    a star and an entry.

    It is reached by running the chain to 006 and then removing what came
    later, so it carries everything 001-006 made (their indexes included).
    """
    with engine.begin() as conn:
        command.upgrade(alembic_config(conn), "006")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE starred_entry"))
        _pre_v24_starred_entry().create(conn)
        for table, columns in _COLUMNS_ADDED_AFTER_006.items():
            for column in columns:
                _drop_column(conn, table, column)
        conn.execute(text("INSERT INTO kb (name, kb_type, path) VALUES ('notes', 'generic', '.')"))
        conn.execute(
            text(
                "INSERT INTO entry (id, kb_name, entry_type, title)"
                " VALUES ('e1', 'notes', 'note', 'Old')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO local_user (id, username, password_hash, role)"
                " VALUES (7, 'ada', 'x', 'write')"
            )
        )
        conn.execute(
            text("INSERT INTO kb_permission (user_id, kb_name, role) VALUES (7, 'notes', 'admin')")
        )
        conn.execute(
            text(
                "INSERT INTO starred_entry (entry_id, kb_name, sort_order, created_at)"
                " VALUES ('e1', 'notes', 0, '2026-04-01')"
            )
        )
    assert _version(engine) == "006"


def _make_unstamped_pre_1723(engine) -> None:
    """A database a 0.26.4 build made (``create_all`` + ``ensure_schema``, no
    alembic version): ``link`` has no ``target_path``/``target_resolved``, and
    an entry's file stat is recorded."""
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        _drop_column(conn, "link", "target_path")
        _drop_column(conn, "link", "target_resolved")
        conn.execute(text("INSERT INTO kb (name, kb_type, path) VALUES ('notes', 'generic', '.')"))
        conn.execute(
            text(
                "INSERT INTO entry (id, kb_name, entry_type, title, file_mtime_ns, file_size)"
                " VALUES ('e1', 'notes', 'note', 'Old', 123, 45)"
            )
        )


def _make_unstamped_create_all(engine) -> None:
    """What ``tests/backends`` and ``benchmarks/run_all.py`` build: every ORM
    table from today's models, no alembic version, a self-registered user
    whose ``global_access`` is 0 and must stay 0."""
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO local_user (id, username, password_hash, role, global_access)"
                " VALUES (9, 'self', 'x', 'read', 0)"
            )
        )


def _make_at_head(engine) -> None:
    _upgrade(engine)


_SHAPES = {
    "empty": lambda engine: None,
    "pre_007": _make_pre_007,
    "unstamped_create_all": _make_unstamped_create_all,
    "unstamped_pre_1723": _make_unstamped_pre_1723,
    "at_head": _make_at_head,
}


# ---------------------------------------------------------------------------
# The structural tests
# ---------------------------------------------------------------------------


def test_the_chain_has_a_single_head():
    """`alembic upgrade head` needs one head (004 once revised 002, forking it)."""
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    assert len(heads) == 1, f"alembic chain has heads {heads}"


def test_every_sqlite_migration_has_exactly_one_alembic_answer():
    """Each `Migration(N)` is answered once: a revision's ``sqlite_versions``
    or `NO_REVISION`. A migration added without its answer fails here."""
    from pyrite.storage.alembic_chain import NO_REVISION

    migrations = {m.version for m in MIGRATIONS}
    answered: dict[int, list[str]] = {}
    script = ScriptDirectory.from_config(alembic_config())
    for rev in script.walk_revisions():
        for version in getattr(rev.module, "sqlite_versions", ()):
            revision = rev.revision
            answered.setdefault(version, []).append(f"revision {revision}")
    for version in NO_REVISION:
        answered.setdefault(version, []).append("NO_REVISION")

    unanswered = sorted(migrations - answered.keys())
    assert not unanswered, (
        f"SQLite migrations {unanswered} have no alembic answer: add a revision that "
        "declares them in `sqlite_versions`, or a reason in alembic_chain.NO_REVISION"
    )
    twice = {v: who for v, who in answered.items() if len(who) > 1}
    assert not twice, f"answered more than once: {twice}"
    unknown = sorted(answered.keys() - migrations)
    assert not unknown, f"answers name versions that are not migrations: {unknown}"


def test_every_no_revision_entry_gives_a_reason():
    from pyrite.storage.alembic_chain import NO_REVISION

    assert all(isinstance(r, str) and r.strip() for r in NO_REVISION.values())


# ---------------------------------------------------------------------------
# The upgrade reaches the schema, from every shape, on both dialects
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", list(_SHAPES))
def test_upgrade_head_reaches_the_schema_pyritedb_builds(shape, engine, sqlite_oracle):
    _SHAPES[shape](engine)
    _upgrade(engine)

    tables, indexes = sqlite_oracle
    reached = _schema(engine)
    assert reached.keys() == tables.keys(), (
        f"missing tables {sorted(tables.keys() - reached.keys())}, "
        f"extra tables {sorted(reached.keys() - tables.keys())}"
    )
    differ = {
        t: {"missing": sorted(tables[t] - reached[t]), "extra": sorted(reached[t] - tables[t])}
        for t in tables
        if reached[t] != tables[t]
    }
    assert not differ, f"columns differ from PyriteDB's: {differ}"
    missing_indexes = sorted(indexes - _named_indexes(engine))
    assert not missing_indexes, f"indexes PyriteDB builds but alembic does not: {missing_indexes}"
    assert _version(engine) == _head()


def test_a_second_upgrade_changes_nothing(engine):
    _make_pre_007(engine)
    _upgrade(engine)
    before = (_schema(engine), _named_indexes(engine))
    with engine.connect() as conn:
        stars = conn.execute(text("SELECT * FROM starred_entry ORDER BY id")).all()
    _upgrade(engine)
    assert (_schema(engine), _named_indexes(engine)) == before
    with engine.connect() as conn:
        assert conn.execute(text("SELECT * FROM starred_entry ORDER BY id")).all() == stars


# ---------------------------------------------------------------------------
# State survives, with SQLite's data steps and only those
# ---------------------------------------------------------------------------


def test_users_grants_and_stars_survive_the_upgrade_from_pre_007(engine):
    _make_pre_007(engine)
    _upgrade(engine)
    with engine.connect() as conn:
        user = conn.execute(
            text("SELECT username, role, global_access FROM local_user WHERE id = 7")
        ).one()
        grant = conn.execute(text("SELECT user_id, kb_name, role FROM kb_permission")).all()
        stars = conn.execute(text("SELECT id, user_id, entry_id, kb_name FROM starred_entry")).all()
    # v26: a user that existed before global_access keeps what its role gave it.
    assert tuple(user) == ("ada", "write", 1)
    assert [tuple(g) for g in grant] == [(7, "notes", "admin")]
    # v24: stars made before they were per-user belong to the instance (user 0).
    assert [tuple(s) for s in stars] == [(1, 0, "e1", "notes")]


def test_stars_are_unique_per_user_after_v24(engine):
    _make_pre_007(engine)
    _upgrade(engine)
    with engine.begin() as conn:
        # Another user may star the same entry: the old (entry, kb) key is gone.
        conn.execute(
            text(
                "INSERT INTO starred_entry (user_id, entry_id, kb_name, sort_order, created_at)"
                " VALUES (7, 'e1', 'notes', 0, '2026-11-19')"
            )
        )
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO starred_entry (user_id, entry_id, kb_name, sort_order,"
                    " created_at) VALUES (7, 'e1', 'notes', 1, '2026-11-19')"
                )
            )


def test_a_user_added_after_the_upgrade_gets_no_global_access_by_default(engine):
    _make_pre_007(engine)
    _upgrade(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO local_user (id, username, password_hash, role)"
                " VALUES (8, 'new', 'x', 'read')"
            )
        )
        assert (
            conn.execute(text("SELECT global_access FROM local_user WHERE id = 8")).scalar_one()
            == 0
        )


def test_an_unstamped_database_keeps_a_self_registered_users_scope(engine):
    """The v26 grant runs only where the revision adds ``global_access``: a
    database built by ``create_all`` after v26, never stamped, upgrades from
    base, and its self-registered user must not gain global read."""
    _make_unstamped_create_all(engine)
    _upgrade(engine)
    with engine.connect() as conn:
        assert (
            conn.execute(text("SELECT global_access FROM local_user WHERE id = 9")).scalar_one()
            == 0
        )


def test_the_v30_step_clears_a_recorded_stat_once(engine):
    """When the upgrade adds ``link.target_path`` it clears every recorded file
    stat, so the next sync re-reads each file and writes its Markdown link rows
    (SQLite v30, decision D3). A later upgrade must not clear it again."""
    _make_unstamped_pre_1723(engine)
    _upgrade(engine)
    with engine.connect() as conn:
        stat = conn.execute(
            text("SELECT file_mtime_ns, file_size FROM entry WHERE id = 'e1'")
        ).one()
    assert tuple(stat) == (None, None)

    with engine.begin() as conn:
        conn.execute(text("UPDATE entry SET file_mtime_ns = 5, file_size = 6 WHERE id = 'e1'"))
    _upgrade(engine)
    with engine.connect() as conn:
        stat = conn.execute(
            text("SELECT file_mtime_ns, file_size FROM entry WHERE id = 'e1'")
        ).one()
    assert tuple(stat) == (5, 6)


def test_an_unstamped_database_that_has_target_path_keeps_its_stat(engine):
    """A database a build after #1723 made (it has ``link.target_path``) and
    nobody stamped upgrades from the base of the chain; revision 012 finds the
    column and must not clear the recorded stat, or every restart of an
    existing install after this release would re-read the whole KB once."""
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO kb (name, kb_type, path) VALUES ('notes', 'generic', '.')"))
        conn.execute(
            text(
                "INSERT INTO entry (id, kb_name, entry_type, title, file_mtime_ns, file_size)"
                " VALUES ('e1', 'notes', 'note', 'Old', 123, 45)"
            )
        )
    _upgrade(engine)
    with engine.connect() as conn:
        stat = conn.execute(
            text("SELECT file_mtime_ns, file_size FROM entry WHERE id = 'e1'")
        ).one()
    assert tuple(stat) == (123, 45)


def test_the_indexer_read_and_an_orm_write_work_after_the_upgrade(engine):
    """The columns ``BaseBackend.get_entries_for_indexing`` selects, and an ORM
    upsert (which writes ``lifecycle`` and ``aliases``), on an upgraded
    pre-007 database (#1021's original failure)."""
    _make_pre_007(engine)
    _upgrade(engine)
    with Session(engine) as session:
        rows = session.execute(
            sa.select(
                Entry.id,
                Entry.file_path,
                Entry.indexed_at,
                Entry.content_hash,
                Entry.file_mtime_ns,
                Entry.file_size,
            ).filter_by(kb_name="notes")
        ).all()
        assert [r[0] for r in rows] == ["e1"]
        session.add(
            Entry(
                id="e2",
                kb_name="notes",
                entry_type="note",
                title="New",
                body="written after the upgrade",
                aliases='["n"]',
            )
        )
        session.commit()


# ---------------------------------------------------------------------------
# Running alembic must not disturb the process
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("with_ini", [False, True], ids=["pyrite", "alembic-ini"])
def test_running_the_upgrade_leaves_existing_loggers_enabled(tmp_path, with_ini):
    """``env.py`` once called ``fileConfig(alembic.ini)``, which disables every
    logger created before it: later log-capturing tests on the same worker got
    nothing (#1021 review, 2026-10-19). Pyrite's own Config has no ini file;
    one built from ``alembic.ini`` (the alembic command line) must not disable
    them either."""
    log = logging.getLogger("pyrite.storage.test_alembic_logger_probe")
    log.disabled = False
    url = f"sqlite:///{(tmp_path / 'log.db').as_posix()}"
    root_handlers = list(logging.getLogger().handlers)
    root_level = logging.getLogger().level
    try:
        if with_ini:
            cfg = Config(str(SCRIPT_LOCATION.parent / "alembic.ini"))
            cfg.set_main_option("sqlalchemy.url", url)
            command.upgrade(cfg, "head")
        else:
            eng = create_engine(url)
            try:
                _upgrade(eng)
            finally:
                eng.dispose()
        assert log.disabled is False
        assert logging.getLogger("pyrite").disabled is False
    finally:
        # fileConfig replaces the root logger's handlers; give pytest's back.
        root = logging.getLogger()
        root.handlers[:] = root_handlers
        root.setLevel(root_level)


# ---------------------------------------------------------------------------
# Two instances in one Postgres database, one schema each
# ---------------------------------------------------------------------------


_ALLOW_NEW_INSTANCE = "PYRITE_POSTGRES_ALLOW_NEW_INSTANCE"


@pytest.fixture
def two_schemas(monkeypatch):
    """Two fresh schemas: ``other``, and ``mine`` in front of it on a second
    engine's search_path (``mine, other``), as an empty ``"$user"`` schema sits
    in front of ``public`` by default. Yields (admin, mine, other, first, second):
    ``first`` sees only ``other``; ``second`` sees ``mine`` then ``other``."""
    url = _pg_url()
    if not url:
        pytest.skip("PYRITE_TEST_PG_URL not set")
    from pyrite.storage.backends.postgres_backend import create_postgres_engine

    monkeypatch.delenv(_ALLOW_NEW_INSTANCE, raising=False)
    worker = os.environ.get("PYTEST_XDIST_WORKER", "serial")
    other, mine = f"pyrite_alembic_other_{worker}", f"pyrite_alembic_mine_{worker}"
    admin = create_postgres_engine(url)
    with admin.begin() as conn:
        for schema in (other, mine):
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    first = create_postgres_engine(url, connect_args={"options": f"-csearch_path={other}"})
    second = create_postgres_engine(url, connect_args={"options": f"-csearch_path={mine},{other}"})
    try:
        yield admin, mine, other, first, second
    finally:
        first.dispose()
        second.dispose()
        with admin.begin() as conn:
            for schema in (other, mine):
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def _tables_in(admin, schema: str) -> set[str]:
    with admin.connect() as conn:
        return set(
            conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = :s"),
                {"s": schema},
            ).scalars()
        )


def _start(engine) -> None:
    """The startup path's upgrade step (the first thing ``ensure_schema`` does)."""
    from pyrite.storage.alembic_chain import upgrade_to_head

    with engine.begin() as conn:
        upgrade_to_head(conn)


def test_an_empty_schema_in_front_of_an_instance_is_refused(two_schemas):
    """Maintainer decision on #1820 (L2, option b): the current schema holds no
    Pyrite tables and a later one on the search_path does. Starting would build
    a new, empty instance in front of the real one and hide its data, so
    startup refuses with POSTGRES_SCHEMA_SHADOWED, naming both schemas and the
    opt-in, and writes nothing in the current schema."""
    from pyrite.exceptions import PostgresSchemaShadowedError

    admin, mine, other, first, second = two_schemas
    _start(first)  # the existing instance, in `other`
    with pytest.raises(PostgresSchemaShadowedError) as refused:
        _start(second)
    assert refused.value.error_code == "POSTGRES_SCHEMA_SHADOWED"
    message = str(refused.value)
    assert mine in message and other in message
    assert _ALLOW_NEW_INSTANCE in message
    assert _tables_in(admin, mine) == set()
    # The existing instance is untouched.
    assert _version(first) == _head()


def test_the_opt_in_creates_a_new_instance_in_the_empty_schema(two_schemas, monkeypatch):
    """With the opt-in set, the same start creates a whole new instance in the
    current schema, and stamps its own ``alembic_version`` there: Postgres's
    inspector answers for any table *visible* on the search_path, so without
    the own-schema checks the other instance's tables would stand in for this
    one's."""
    admin, mine, other, first, second = two_schemas
    _start(first)
    monkeypatch.setenv(_ALLOW_NEW_INSTANCE, "1")
    _start(second)
    assert {"entry", "kb", "local_user", "invite_code", "alembic_version"} <= _tables_in(
        admin, mine
    )
    assert _version(second) == _head()


def test_no_instance_anywhere_on_the_search_path_starts_fresh(two_schemas):
    """A fresh install is not refused: with no Pyrite tables in any schema on
    the search_path, startup creates the instance in the current schema."""
    admin, mine, other, first, second = two_schemas
    _start(second)
    assert {"entry", "kb", "alembic_version"} <= _tables_in(admin, mine)
    assert _tables_in(admin, other) == set()


def test_an_existing_instance_in_the_current_schema_starts_whatever_is_behind_it(two_schemas):
    """The refusal is only for an *empty* current schema: an instance already
    in it upgrades as before, even with another instance later on the path."""
    admin, mine, other, first, second = two_schemas
    _start(first)
    with second.begin() as conn:
        command.upgrade(alembic_config(conn), "006")  # an existing, older instance in `mine`
    _start(second)
    assert _version(second) == _head()


# ---------------------------------------------------------------------------
# Fail closed where the old ensure_schema tolerated the case (#1820 cold read)
# ---------------------------------------------------------------------------


def _fresh_pg_schema(name: str):
    """A new, empty schema and an engine whose search_path is only it."""
    url = _pg_url()
    if not url:
        pytest.skip("PYRITE_TEST_PG_URL not set")
    from pyrite.storage.backends.postgres_backend import create_postgres_engine

    schema = f"{name}_{os.environ.get('PYTEST_XDIST_WORKER', 'serial')}"
    admin = create_postgres_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    return admin, schema, url


_START_ONE = """
import sys, time
from sqlalchemy import text
from pyrite.storage.alembic_chain import upgrade_to_head
from pyrite.storage.backends.postgres_backend import create_postgres_engine

url, schema, start_at = sys.argv[1], sys.argv[2], float(sys.argv[3])
engine = create_postgres_engine(url, connect_args={"options": f"-csearch_path={schema}"})
with engine.connect() as conn:
    conn.execute(text("SELECT 1"))  # connected before the start line
    conn.rollback()
    time.sleep(max(0.0, start_at - time.time()))
    with conn.begin():
        upgrade_to_head(conn)
engine.dispose()
"""


def test_two_first_startups_at_once_both_succeed():
    """Two processes starting on one never-upgraded database at once. dev's
    ``ADD COLUMN IF NOT EXISTS`` tolerated that; a check-then-create chain
    does not ("relation ... already exists") unless the upgrade is serialized
    across processes (a transaction-scoped advisory lock per schema): the
    second must wait, then find the work done."""
    import subprocess
    import time

    admin, schema, url = _fresh_pg_schema("pyrite_alembic_race")
    start_at = time.time() + 3.0
    try:
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", _START_ONE, url, schema, str(start_at)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        results = [p.communicate(timeout=120) for p in procs]
        failed = [err[-600:] for p, (_, err) in zip(procs, results, strict=True) if p.returncode]
        assert not failed, f"a concurrent first startup failed: {failed}"
        engine = create_engine(
            make_url(url).set(drivername="postgresql+psycopg2"),
            connect_args={"options": f"-csearch_path={schema}"},
        )
        try:
            assert _version(engine) == _head()
            with engine.connect() as conn:
                assert conn.execute(text("SELECT count(*) FROM alembic_version")).scalar_one() == 1
        finally:
            engine.dispose()
    finally:
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def test_two_threads_upgrading_at_once_in_one_process_both_succeed(tmp_path):
    """alembic's ``context`` and ``op`` are module-global: two threads running
    an upgrade at once in one process (two databases, no shared rows) trampled
    each other's state (KeyError 'script'). `upgrade_to_head` runs one at a
    time per process."""
    import threading

    from pyrite.storage.alembic_chain import upgrade_to_head

    engines = [create_engine(f"sqlite:///{(tmp_path / f'{i}.db').as_posix()}") for i in range(2)]
    barrier = threading.Barrier(2, timeout=60)
    errors: list[BaseException] = []

    def start(engine) -> None:
        try:
            with engine.begin() as conn:
                barrier.wait()
                upgrade_to_head(conn)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=start, args=(e,)) for e in engines]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        assert not errors, f"a concurrent upgrade failed: {errors!r}"
        assert [_version(e) for e in engines] == [_head(), _head()]
    finally:
        for e in engines:
            e.dispose()


def test_a_search_path_naming_no_existing_schema_is_refused():
    """``current_schema()`` is NULL when no schema on the search_path exists.
    The upgrade must refuse with a clear error, never build tables under a
    schema named "None" or wherever the server falls back to."""
    from pyrite.exceptions import StorageError
    from pyrite.storage.backends.postgres_backend import create_postgres_engine

    admin, schema, url = _fresh_pg_schema("pyrite_alembic_noschema")
    missing = f"{schema}_absent"
    engine = create_postgres_engine(url, connect_args={"options": f"-csearch_path={missing}"})
    try:
        with pytest.raises(StorageError, match="search_path"):
            with engine.begin() as conn:
                from pyrite.storage.alembic_chain import upgrade_to_head

                upgrade_to_head(conn)
        with admin.connect() as conn:
            made = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables"
                    " WHERE table_schema IN (:a, 'None')"
                ),
                {"a": missing},
            ).scalar_one()
        assert made == 0
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


# ---------------------------------------------------------------------------
# Postgres keeps no vector index on entry (#1822, option a)
# ---------------------------------------------------------------------------
#
# #1821 made Postgres semantic search an exact nearest-neighbour scan whose
# sort key no index can supply, so the HNSW index on entry.embedding served no
# query and was still rebuilt on every write. Maintainer decision on #1822:
# drop it. These tests read pg_indexes, the server's own catalogue, never the
# revision or ensure_schema.

# pgvector's index methods and operator classes: an index whose definition
# names one of these, or the embedding column, serves the ``<=>`` operator.
_VECTOR_INDEX_MARKS = ("hnsw", "ivfflat", "vector_cosine_ops", "vector_l2_ops", "vector_ip_ops")

# The index as every Postgres database before #1822 had it (ensure_schema
# created it on each start).
_DEV_HNSW_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_entry_embedding "
    "ON entry USING hnsw(embedding vector_cosine_ops)"
)


def _vector_indexes_on_entry(admin, schema: str) -> dict[str, str]:
    """``{indexname: indexdef}`` for every index on ``schema.entry`` that uses
    pgvector or names the embedding column."""
    with admin.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes"
                " WHERE schemaname = :s AND tablename = 'entry'"
            ),
            {"s": schema},
        ).all()
    return {
        name: definition
        for name, definition in rows
        if "embedding" in definition.lower()
        or any(mark in definition.lower() for mark in _VECTOR_INDEX_MARKS)
    }


@pytest.fixture
def vector_schemas(monkeypatch):
    """Fresh schemas ``mine`` and ``other`` with pgvector reachable (in public),
    and a factory for engines whose search_path is ``<schemas...>, public``.
    The new-instance opt-in is set: ``public`` may hold another suite's
    instance in a serial run, and refusing that is not what these test."""
    url = _pg_url()
    if not url:
        pytest.skip("PYRITE_TEST_PG_URL not set")
    from pyrite.storage.backends.postgres_backend import create_postgres_engine

    monkeypatch.setenv(_ALLOW_NEW_INSTANCE, "1")
    worker = os.environ.get("PYTEST_XDIST_WORKER", "serial")
    mine, other = f"pyrite_vec_mine_{worker}", f"pyrite_vec_other_{worker}"
    admin = create_postgres_engine(url)
    with admin.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public"))
        for schema in (mine, other):
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engines = []

    def engine_for(*path: str):
        eng = create_postgres_engine(
            url, connect_args={"options": f"-csearch_path={','.join((*path, 'public'))}"}
        )
        engines.append(eng)
        return eng

    try:
        yield admin, mine, other, engine_for
    finally:
        for eng in engines:
            eng.dispose()
        with admin.begin() as conn:
            for schema in (mine, other):
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def _make_pre_1822(engine) -> None:
    """A database as dev left it before #1822: the chain at 012, then the
    Postgres-only embedding column and its HNSW index from ensure_schema."""
    with engine.begin() as conn:
        command.upgrade(alembic_config(conn), "012")
        conn.execute(text("ALTER TABLE entry ADD COLUMN IF NOT EXISTS embedding vector(384)"))
        conn.execute(text(_DEV_HNSW_INDEX))


def test_upgrade_head_drops_the_hnsw_index_and_a_restart_does_not_rebuild_it(vector_schemas):
    """A database from before #1822 carries idx_entry_embedding. After
    ``alembic upgrade head`` no index on entry uses pgvector, and two full
    ``ensure_schema`` starts after it (a restart) do not create one again."""
    from pyrite.storage.backends.postgres_backend import ensure_schema

    admin, mine, _other, engine_for = vector_schemas
    engine = engine_for(mine)
    _make_pre_1822(engine)
    assert set(_vector_indexes_on_entry(admin, mine)) == {"idx_entry_embedding"}

    _upgrade(engine)
    assert _vector_indexes_on_entry(admin, mine) == {}

    ensure_schema(engine)
    ensure_schema(engine)
    assert _vector_indexes_on_entry(admin, mine) == {}


def test_a_fresh_database_never_gets_a_vector_index(vector_schemas):
    """A first start (and a second) on an empty schema builds the embedding
    column but no index on it."""
    from pyrite.storage.backends.postgres_backend import ensure_schema

    admin, mine, _other, engine_for = vector_schemas
    engine = engine_for(mine)
    ensure_schema(engine)
    ensure_schema(engine)
    with admin.connect() as conn:
        has_embedding = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = :s"
                " AND table_name = 'entry' AND column_name = 'embedding'"
            ),
            {"s": mine},
        ).first()
    assert has_embedding is not None
    assert _vector_indexes_on_entry(admin, mine) == {}


@pytest.mark.control(
    reason="dev drops no index at all; this guards the revision's schema-qualified DROP"
)
def test_the_drop_touches_only_this_instances_schema(vector_schemas):
    """Two instances share a database, ``other`` later on ``mine``'s
    search_path. Upgrading ``mine``, which has no HNSW index of its own, must
    not find and drop ``other``'s through the search_path: each instance's
    schema is changed by its own upgrade only."""
    admin, mine, other, engine_for = vector_schemas
    _make_pre_1822(engine_for(other))
    _upgrade(engine_for(mine, other))
    assert set(_vector_indexes_on_entry(admin, other)) == {"idx_entry_embedding"}


def test_downgrading_the_drop_recreates_the_index_as_dev_defined_it(vector_schemas):
    """``alembic downgrade -1`` from head puts back idx_entry_embedding:
    HNSW, cosine operator class, on entry.embedding."""
    admin, mine, _other, engine_for = vector_schemas
    engine = engine_for(mine)
    _make_pre_1822(engine)
    _upgrade(engine)
    with engine.begin() as conn:
        command.downgrade(alembic_config(conn), "-1")
    assert _version(engine) == "012"
    found = _vector_indexes_on_entry(admin, mine)
    assert set(found) == {"idx_entry_embedding"}
    definition = found["idx_entry_embedding"].lower()
    assert "using hnsw" in definition and "embedding vector_cosine_ops" in definition
