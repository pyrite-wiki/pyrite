- **A Postgres database upgrades to the schema this release reads (#718).**
  `alembic upgrade head` failed on every database: revision 004 forked the
  chain, 001 and 002 were SQLite-only DDL, 005 and 006 failed on columns that
  already existed, and the chain stopped at 006 while SQLite's migrations
  reached v30. The chain now has one head and reaches the tables, columns and
  indexes `PyriteDB` builds, from an empty database, from one stamped 006, and
  from one never stamped. Users, grants and stars are kept: existing stars
  become the instance's own list and existing users keep global access, as on
  SQLite. `ensure_schema` runs the chain on every startup instead of patching
  columns itself. A test fails when a SQLite migration lands without its alembic
  answer. Revision 007, the regression test and the env.py driver fix were
  contributed by Rudra (#1021).
