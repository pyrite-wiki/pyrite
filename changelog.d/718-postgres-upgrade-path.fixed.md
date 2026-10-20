- **The Postgres upgrade path works again (#718).** `alembic upgrade head`
  failed on every database with "Multiple heads are present" (revision 004
  forked the chain by revising 002 instead of 003), and the chain stopped at
  006 while the ORM's `entry` table kept growing — an existing database
  upgraded across releases raised "column does not exist" on every ORM write
  (`lifecycle`) and on the indexer's read (`content_hash`, `file_mtime_ns`,
  `file_size`). 004 now revises 003, a new revision 007 adds the four entry
  columns and the lifecycle index idempotently, and alembic's env.py pins a
  bare `postgresql://` URL to psycopg2 the same way every other engine is
  built. A test upgrades a dev-shaped rev-006 database on SQLite and, when
  `PYRITE_TEST_PG_URL` is set, on real Postgres.
