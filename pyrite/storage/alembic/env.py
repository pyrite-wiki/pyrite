"""
Alembic environment configuration for pyrite.

Configures Alembic to use the ORM models from storage/models.py.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url

from pyrite.storage.models import Base

# Alembic Config object
config = context.config

# Set up logging from alembic.ini if available
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Virtual tables to exclude from autogenerate
VIRTUAL_TABLES = {"entry_fts", "vec_entry"}


def include_object(object, name, type_, reflected, compare_to):
    """Exclude virtual tables from autogenerate."""
    if type_ == "table" and name in VIRTUAL_TABLES:
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = _connectable()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


def _connectable():
    """The engine the URL names, through the driver choice the product makes.

    A bare ``postgresql://`` URL must resolve to psycopg2 (the driver the
    ``postgres`` extra installs) exactly as ``create_postgres_engine`` does
    for every other engine Pyrite builds (#373) — through ``engine_from_config``
    it resolved to SQLAlchemy's own default, which is psycopg 3 on 2.1, so
    ``alembic upgrade`` against Postgres failed with ``No module named
    'psycopg'`` before it could run a single revision (#718).
    """
    url = config.get_main_option("sqlalchemy.url")
    if url is not None and make_url(url).drivername.partition("+")[0] in (
        "postgresql",
        "postgres",
    ):
        from pyrite.storage.backends.postgres_backend import create_postgres_engine

        return create_postgres_engine(url, poolclass=pool.NullPool)
    return engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
