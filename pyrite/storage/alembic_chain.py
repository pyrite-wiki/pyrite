"""The alembic chain: how a Postgres database reaches this build's schema (#718).

Two mechanisms upgrade a schema, one per backend, and this module is where
they are held together:

- **SQLite** keeps ``MigrationManager`` (``migrations.py``: ``MIGRATIONS``,
  ``CURRENT_VERSION``), plus ``schema_repair.py`` on every open.
- **Postgres** upgrades through the alembic chain in ``alembic/versions/``.
  ``postgres_backend.ensure_schema`` runs it to head on every startup (the
  default, automatic mode of the maintainer's 2026-10-23 decision on #718),
  then adds only the Postgres-only objects (pgvector, ``fts_vector``,
  ``embedding``, their indexes and the FTS trigger).

**The parity rule.** Every SQLite migration is answered once on the alembic
side: a revision names the versions it answers in its module-level
``sqlite_versions``, or `NO_REVISION` says why the version needs none.
``tests/test_alembic_upgrade_path.py`` fails when a migration has no answer, or
two, and checks that ``alembic upgrade head`` reaches what ``PyriteDB`` builds
on SQLite.

**Every revision is guarded.** It adds a table, column or index only when the
database lacks it, and runs a data step only when it has just added the column
the step belongs to. So a database nobody stamped (one built by ``create_all``,
as every Postgres database before this was) upgrades from the base of the
chain, and each step skips what is already there. No stamp-on-detect step is
needed, and an unconditional data step can never re-run on rows it should not
touch (revision 008's ``global_access`` grant is the case that matters).

**Helpers here, not in ``alembic/``.** Revisions import from this module:
``pyrite/storage/alembic`` is a directory of scripts, not a package.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..exceptions import StorageError

if TYPE_CHECKING:  # alembic is imported only when the chain runs
    from alembic.config import Config

SCRIPT_LOCATION = Path(__file__).parent / "alembic"

#: SQLite migrations with no alembic revision, and why. Every other version in
#: ``MIGRATIONS`` is named by exactly one revision's ``sqlite_versions``.
NO_REVISION: dict[int, str] = {
    2: (
        "vec_entry is a sqlite-vec virtual table. Postgres keeps vectors in "
        "entry.embedding (pgvector), which ensure_schema creates with the other "
        "Postgres-only objects."
    ),
}


def alembic_config(connection: Any = None) -> Config:
    """A Config for the chain with no ini file, so no ``fileConfig``.

    ``connection`` is handed to ``env.py``, which then runs on it instead of
    building an engine: the caller's engine, ``search_path`` and transaction.
    """
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(SCRIPT_LOCATION))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


# alembic's `context` and `op` are module-global proxies: two threads running
# a command in one process at once overwrite each other's (KeyError 'script').
_UPGRADE_LOCK = threading.Lock()


def upgrade_to_head(connection: Any) -> None:
    """``alembic upgrade head`` on ``connection``, one upgrade at a time.

    **Serialized.** Two processes starting on one database at once both
    found a table missing and both created it ("already exists"), where
    dev's ``ADD COLUMN IF NOT EXISTS`` tolerated the race. On Postgres a
    transaction-scoped advisory lock, keyed by this database's own schema, is
    taken before alembic looks at anything: the second process waits, then
    finds the work done. It is held by the caller's transaction (``ensure_schema``
    runs this in ``engine.begin()``) and released when that commits or rolls
    back, so a crashed process leaves no lock behind. Within one process a
    thread lock does the same for alembic's global state, on every dialect.

    **Refuses an unknown schema** (`own_schema`).
    """
    from alembic import command
    from sqlalchemy import text

    with _UPGRADE_LOCK:
        if connection.dialect.name == "postgresql":
            schema = own_schema(connection)
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"pyrite.alembic_chain:{schema}"},
            )
        command.upgrade(alembic_config(connection), "head")


# ---------------------------------------------------------------------------
# Guarded DDL for revisions
# ---------------------------------------------------------------------------


def own_schema(bind: Any) -> str | None:
    """The schema this database's tables live in: Postgres's ``current_schema()``
    (the first existing schema on ``search_path``), None on SQLite. Raises
    `StorageError` when no schema on the search_path exists.

    Every existence check names it. Postgres's inspector otherwise answers for
    any table *visible* on the ``search_path``, so a second instance in its own
    schema of a shared database would see the first's ``public.entry`` and skip
    creating its own (the deployment ``test_postgres_schema_scoping`` guards).
    """
    if bind.dialect.name != "postgresql":
        return None
    from sqlalchemy import text

    schema = bind.execute(text("SELECT current_schema()")).scalar_one()
    if schema is None:
        # No schema on the search_path exists. Building anyway would put the
        # tables wherever the server falls back to, or check a schema named
        # "None"; refuse before anything is written.
        raise StorageError(
            "Cannot upgrade the Postgres schema: no schema on the connection's "
            "search_path exists (current_schema() is NULL). Create the schema the "
            "search_path names, or correct the search_path, and start again."
        )
    return str(schema)


def existing_columns(table: str) -> set[str] | None:
    """The columns ``table`` has now, or None when there is no such table."""
    import sqlalchemy as sa
    from alembic import op

    bind = op.get_bind()
    schema = own_schema(bind)
    inspector = sa.inspect(bind)
    if not inspector.has_table(table, schema=schema):
        return None
    return {c["name"] for c in inspector.get_columns(table, schema=schema)}


def add_missing_columns(table: str, columns: dict[str, str]) -> set[str]:
    """Add each ``{name: DDL type and default}`` the table lacks; return those added.

    Nothing when the table does not exist (a revision that creates tables
    creates them whole). The DDL is written to mean the same on SQLite and
    Postgres: ``TEXT``, ``INTEGER``, ``BIGINT``, a literal ``DEFAULT``.
    """
    from alembic import op

    existing = existing_columns(table)
    if existing is None:
        return set()
    added = set()
    for name, ddl in columns.items():
        if name not in existing:
            op.execute(f'ALTER TABLE "{table}" ADD COLUMN {name} {ddl}')
            added.add(name)
    return added


def create_index(
    name: str, table: str, columns: Iterable[str], *, unique: bool = False, where: str = ""
) -> None:
    """``CREATE [UNIQUE] INDEX IF NOT EXISTS`` (both dialects), when the table exists."""
    from alembic import op

    if existing_columns(table) is None:
        return
    kind = "UNIQUE INDEX" if unique else "INDEX"
    clause = f" WHERE {where}" if where else ""
    op.execute(f'CREATE {kind} IF NOT EXISTS {name} ON "{table}" ({", ".join(columns)}){clause}')


def create_model_tables(*names: str) -> None:
    """Create the named ORM tables the database lacks, from today's models.

    The same stand-in ``PyriteDB`` uses (``create_all``, then migrations that
    add what an older table lacks); later revisions add a column only when it
    is missing, so a table created whole here passes through them unchanged.
    """
    from alembic import op

    from .models import Base

    wanted = set(names) or set(Base.metadata.tables)
    bind = op.get_bind()
    # In dependency order; each checked in this database's own schema
    # (`own_schema`), which create_all's own checkfirst does not do.
    for table in Base.metadata.sorted_tables:
        if table.name in wanted and existing_columns(table.name) is None:
            table.create(bind, checkfirst=False)
