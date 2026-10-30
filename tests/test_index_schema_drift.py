"""An index on disk from another Pyrite build is repaired when it is opened (#1260, #1302).

The property, from #1260's groom: after ``PyriteDB`` opens an index, every
table has the model's columns and indexes, or the open is refused with one
``STORAGE_ERROR`` and a suggestion that works. No command answers with a raw
``OperationalError``, ``index build`` never files a storage error as a file
that cannot be parsed, and two processes opening one old index never both
migrate it.

The shapes are what three real builds left on disk
(``tests/fixtures/index-shapes/*.sql``, written by ``build_index_shapes.py``
from each tree's own code):

* ``v0.25.8``: schema 27, no ``entry.aliases``, no ``link.target_prefix``;
* ``v0.26.0``: schema 29, the current columns;
* ``arch-df6372f2``: schema 28 recorded, ``entry.aliases`` present and
  ``link.target_prefix`` absent. On ``arch/2026-10`` between df6372f2 and the
  sprint merge, migration 28 meant aliases; on dev it means the prefix, so dev
  saw 28 recorded and never added the column.

The CLI tests run the real entry point in a subprocess, the surface the bug
was reported on; the concurrency tests need separate processes anyway.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

try:
    import fcntl
except ImportError:  # Windows: no flock, so the open runs unlocked there
    fcntl = None  # type: ignore[assignment]

#: Only the lock and race tests need ``flock``; the repair tests run everywhere.
needs_flock = pytest.mark.skipif(fcntl is None, reason="the schema lock needs flock")

from pyrite.exceptions import StorageError
from pyrite.storage.database import PyriteDB
from pyrite.storage.migrations import CURRENT_VERSION
from pyrite.storage.models import Base

SHAPES_DIR = Path(__file__).parent / "fixtures" / "index-shapes"
SHAPES = ["v0.25.8", "v0.26.0", "arch-df6372f2"]

_HANDLED_BEFORE = (
    "a shape the code before #1260 already opened (its numbered migrations add the "
    "columns): guards the repair against regressing it"
)
# The shapes as test parameters: the two released ones are controls, since they
# pass without the fix; the arch window is the red one.
SHAPE_PARAMS = [
    pytest.param("v0.25.8", marks=pytest.mark.control(reason=_HANDLED_BEFORE)),
    pytest.param("v0.26.0", marks=pytest.mark.control(reason=_HANDLED_BEFORE)),
    "arch-df6372f2",
]

pytestmark = pytest.mark.integration

# The refusal class is named, not imported, so this file collects against the
# code before the fix and each test fails on behaviour there (verify-red).
REFUSAL = "IndexSchemaRefusedError"

# The schema lock's file, beside the index (`schema_repair.SCHEMA_LOCK_SUFFIX`).
# Spelled out for the same reason.
LOCK_SUFFIX = ".schema-lock"


@contextlib.contextmanager
def _hold_schema_lock(db_path: Path):
    assert fcntl is not None, "mark the test needs_flock"
    """Hold the schema lock the way another Pyrite process would."""
    fd = os.open(db_path.with_name(db_path.name + LOCK_SUFFIX), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# ---------------------------------------------------------------------------
# Building an index of a given shape
# ---------------------------------------------------------------------------


def _load_shape(shape: str, db_path: Path, *, wal: bool = True) -> None:
    """Write the schema that ``shape``'s build left, with plain sqlite3."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript((SHAPES_DIR / f"{shape}.sql").read_text())
        conn.commit()
        # Every Pyrite build left its index in WAL mode, and it persists. A
        # copy made with SQLite's backup API (`pyrite db backup`) is not.
        if wal:
            conn.execute("PRAGMA journal_mode = WAL")
    finally:
        conn.close()


def _seed_rows(db_path: Path, kb_path: Path) -> None:
    """Rows the old build would have indexed, in columns every shape has."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO kb (name, kb_type, path) VALUES ('demo', 'generic', ?)", (str(kb_path),)
        )
        for entry_id, title in (("old-one", "Old One"), ("new-one", "New One")):
            conn.execute(
                "INSERT INTO entry (id, kb_name, entry_type, title, body, file_path)"
                " VALUES (?, 'demo', 'note', ?, 'Body.', ?)",
                (entry_id, title, str(kb_path / f"{entry_id}.md")),
            )
        conn.execute(
            "INSERT INTO link (source_id, source_kb, target_id, target_kb, relation,"
            " inverse_relation) VALUES ('old-one', 'demo', 'new-one', 'demo', 'wikilink',"
            " 'wikilinked_by')"
        )
        conn.commit()
    finally:
        conn.close()


def _write_kb(kb_path: Path) -> None:
    kb_path.mkdir()
    (kb_path / "old-one.md").write_text(
        "---\nid: old-one\ntitle: Old One\ntype: note\n---\nSee [[new-one]].\n"
    )
    (kb_path / "new-one.md").write_text(
        "---\nid: new-one\ntitle: New One\ntype: note\n---\nBody.\n"
    )


@pytest.fixture
def make_home(tmp_path):
    """A config dir whose index is ``shape``, seeded with rows, and a KB on disk."""

    def make(shape: str | None, name: str = "home") -> Path:
        home = tmp_path / name
        home.mkdir()
        kb_path = tmp_path / f"{name}-kb"
        _write_kb(kb_path)
        (home / "config.yaml").write_text(
            f"knowledge_bases:\n  - name: demo\n    path: {kb_path}\n    kb_type: generic\n"
        )
        if shape is not None:
            _load_shape(shape, home / "index.db")
            _seed_rows(home / "index.db", kb_path)
        return home

    return make


def _env(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYRITE_CONFIG_DIR"] = str(home)
    env["PYRITE_DATA_DIR"] = str(home)
    env["PYRITE_LOG_LEVEL"] = "WARNING"
    return env


def _pyrite(home: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "from pyrite.cli import main; main()", *args],
        env=_env(home),
        capture_output=True,
        text=True,
        timeout=180,
    )


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _all_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """``table_xinfo``: unlike ``table_info``, it lists generated columns too."""
    return {row[1] for row in conn.execute(f"PRAGMA table_xinfo({table})")}


def _schema_snapshot(db_path: Path) -> tuple[int, list[tuple]]:
    """SQLite's schema cookie (bumped by every DDL) and the schema text."""
    conn = sqlite3.connect(db_path)
    try:
        cookie = conn.execute("PRAGMA schema_version").fetchone()[0]
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        return cookie, rows
    finally:
        conn.close()


def _error_json(proc: subprocess.CompletedProcess) -> dict:
    for line in (proc.stdout + "\n" + proc.stderr).splitlines():
        line = line.strip()
        if line.startswith("{") and '"error_code"' in line:
            return json.loads(line)
    raise AssertionError(f"no structured error in output:\n{proc.stdout}\n{proc.stderr}")


# ---------------------------------------------------------------------------
# (1) After an open, every model column and declared index exists
# ---------------------------------------------------------------------------


def _missing_after_open(db_path: Path) -> list[str]:
    db = PyriteDB(db_path)
    db.close()
    conn = sqlite3.connect(db_path)
    try:
        missing = []
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        for table in Base.metadata.sorted_tables:
            have = _columns(conn, table.name)
            missing += [f"{table.name}.{c.name}" for c in table.columns if c.name not in have]
            missing += [f"index {i.name}" for i in table.indexes if i.name not in names]
        # The two columns #1260 is about, by hand rather than from the model.
        if "target_prefix" not in _columns(conn, "link"):
            missing.append("link.target_prefix (by hand)")
        if "aliases" not in _columns(conn, "entry"):
            missing.append("entry.aliases (by hand)")
        return missing
    finally:
        conn.close()


@pytest.mark.parametrize("shape", SHAPE_PARAMS)
def test_open_gives_every_model_column_and_index(shape, tmp_path):
    db_path = tmp_path / "index.db"
    _load_shape(shape, db_path)
    assert _missing_after_open(db_path) == []


@pytest.mark.parametrize(
    ("recorded", "drop"),
    [
        # Another branch recorded 29 with aliases meaning something else.
        (29, [("entry", "aliases")]),
        # Recorded at 29, the arch window's other half.
        (29, [("link", "target_prefix")]),
        # The ADR-0038 stat columns lost under a recorded 28.
        (28, [("entry", "file_mtime_ns"), ("entry", "file_size")]),
        # A version recorded ahead of this build (a later branch's v30/v31).
        (CURRENT_VERSION + 2, [("link", "target_prefix"), ("entry", "aliases")]),
    ],
    ids=["29-no-aliases", "29-no-target-prefix", "28-no-stat", "ahead-no-columns"],
)
def test_open_adds_columns_a_recorded_version_claims(recorded, drop, tmp_path):
    db_path = tmp_path / "index.db"
    _load_shape("v0.26.0", db_path)
    conn = sqlite3.connect(db_path)
    for table, column in drop:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.execute("DELETE FROM schema_version WHERE version > ?", (recorded,))
    for version in range(CURRENT_VERSION + 1, recorded + 1):
        conn.execute(
            "INSERT INTO schema_version VALUES (?, 'another branch', '2026-10-17')", (version,)
        )
    conn.commit()
    conn.close()
    assert _missing_after_open(db_path) == []


def test_open_recreates_a_declared_index_that_is_missing(tmp_path):
    db_path = tmp_path / "index.db"
    _load_shape("v0.26.0", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("DROP INDEX idx_link_target")
    conn.commit()
    conn.close()
    assert _missing_after_open(db_path) == []


# ---------------------------------------------------------------------------
# Half-applied runs complete on the next open
# ---------------------------------------------------------------------------


@pytest.mark.control(
    reason="the add-if-missing migration handlers already finish this; guards that the repair keeps it"
)
def test_a_migration_whose_column_landed_but_not_its_record_completes(tmp_path):
    """v28's ALTER ran, then the process died before ``schema_version`` got 28."""
    db_path = tmp_path / "index.db"
    _load_shape("v0.25.8", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE link ADD COLUMN target_prefix TEXT")
    conn.commit()
    conn.close()
    assert _missing_after_open(db_path) == []
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == CURRENT_VERSION
    conn.close()


@pytest.mark.control(
    reason="the add-if-missing migration handlers already finish this; guards that the repair keeps it"
)
def test_a_repair_cut_short_completes_on_the_next_open(tmp_path):
    """One of two missing columns added, as a repair stopped between ALTERs would."""
    db_path = tmp_path / "index.db"
    _load_shape("arch-df6372f2", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE entry DROP COLUMN aliases")
    conn.execute("ALTER TABLE link ADD COLUMN target_prefix VARCHAR")
    conn.commit()
    conn.close()
    assert _missing_after_open(db_path) == []


# ---------------------------------------------------------------------------
# (3) A second open changes nothing
# ---------------------------------------------------------------------------


@pytest.mark.control(
    reason="no DDL on a second open held before the fix; guards the repair against churning"
)
@pytest.mark.parametrize("shape", SHAPES)
def test_second_open_runs_no_ddl(shape, tmp_path):
    db_path = tmp_path / "index.db"
    _load_shape(shape, db_path)
    PyriteDB(db_path).close()
    before = _schema_snapshot(db_path)
    PyriteDB(db_path).close()
    assert _schema_snapshot(db_path) == before


# ---------------------------------------------------------------------------
# (2) The #1260 crash paths, through the CLI
# ---------------------------------------------------------------------------


@pytest.mark.cli
@pytest.mark.parametrize("shape", SHAPE_PARAMS)
@pytest.mark.parametrize(
    "argv",
    [
        ("get", "new-one", "-k", "demo"),
        ("backlinks", "new-one", "-k", "demo"),
        ("index", "health", "--format", "json"),
    ],
    ids=["get", "backlinks", "index-health"],
)
def test_read_commands_work_on_every_shape(shape, argv, make_home):
    home = make_home(shape)
    proc = _pyrite(home, *argv)
    out = proc.stdout + proc.stderr
    assert "Traceback" not in out, out
    assert "OperationalError" not in out, out
    if argv[0] == "index":
        # Health answers with a report; the seeded rows predate the files, so
        # it may call them stale and exit 1. A report is the property here.
        assert "status" in json.loads(proc.stdout), out
    else:
        assert proc.returncode == 0, out


@pytest.mark.cli
@pytest.mark.parametrize("shape", SHAPE_PARAMS)
def test_index_build_indexes_every_file_on_every_shape(shape, make_home):
    home = make_home(shape)
    proc = _pyrite(home, "index", "build", "--no-embed")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "could not be parsed" not in out, out
    conn = sqlite3.connect(home / "index.db")
    ids = sorted(r[0] for r in conn.execute("SELECT id FROM entry WHERE kb_name = 'demo'"))
    conn.close()
    assert ids == ["new-one", "old-one"]


@pytest.mark.cli
def test_backlinks_answer_on_the_arch_window_shape(make_home):
    """Not only no crash: the link row the old build wrote is still found."""
    home = make_home("arch-df6372f2")
    proc = _pyrite(home, "backlinks", "new-one", "-k", "demo", "--format", "json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "old-one" in proc.stdout


# ---------------------------------------------------------------------------
# (5) Where the repair cannot run: one refusal the user can read
# ---------------------------------------------------------------------------


def _drop_kb_path(db_path: Path) -> None:
    """``kb.path`` is NOT NULL with no default: SQLite cannot add it in place."""
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE kb DROP COLUMN path")
    conn.commit()
    conn.close()


def _drop_link_created_at(db_path: Path) -> None:
    """``link.created_at`` has ``DEFAULT CURRENT_TIMESTAMP``, non-constant (#1318)."""
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE link DROP COLUMN created_at")
    conn.commit()
    conn.close()


def test_a_column_that_cannot_be_added_refuses_the_open(tmp_path):
    db_path = tmp_path / "index.db"
    _load_shape("v0.26.0", db_path)
    _drop_kb_path(db_path)
    # An addable column is missing too: the refusal must not add it first.
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE entry DROP COLUMN aliases")
    conn.commit()
    conn.close()
    with pytest.raises(StorageError) as info:
        PyriteDB(db_path)
    err = info.value
    assert type(err).__name__ == REFUSAL
    assert err.error_code == "STORAGE_ERROR"
    assert "kb.path" in str(err)
    assert str(tmp_path) not in str(err), "the message must not carry a path"
    assert err.public_message is None, "the message is safe; the generic one must not replace it"
    assert "pyrite index build" in (err.suggestion or "")
    # The repair plans before it writes: nothing was half-added.
    conn = sqlite3.connect(db_path)
    assert "aliases" not in _columns(conn, "entry")
    conn.close()
    # And a second refused open changes nothing more.
    before = _schema_snapshot(db_path)
    with pytest.raises(StorageError, match="kb.path"):
        PyriteDB(db_path)
    assert _schema_snapshot(db_path) == before


def test_a_non_constant_default_on_a_table_with_rows_refuses_the_open(tmp_path):
    """#1318: SQLite refuses ``DEFAULT CURRENT_TIMESTAMP`` once the table has a
    row. The repair must plan the refusal, not fail inside the transaction and
    tell the user to report a bug."""
    db_path = tmp_path / "index.db"
    _load_shape("v0.26.0", db_path)
    _seed_rows(db_path, tmp_path / "kb")
    _drop_link_created_at(db_path)
    # An addable column is missing too: the refusal must not add it first.
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE entry DROP COLUMN aliases")
    conn.commit()
    conn.close()
    with pytest.raises(StorageError) as info:
        PyriteDB(db_path)
    err = info.value
    assert type(err).__name__ == REFUSAL
    assert err.error_code == "STORAGE_ERROR"
    assert "link.created_at" in str(err)
    assert "non-constant default" in str(err)
    assert str(tmp_path) not in str(err), "the message must not carry a path"
    assert err.public_message is None, "the message is safe; the generic one must not replace it"
    assert "pyrite index build" in (err.suggestion or "")
    # Planned before it wrote: neither field was added.
    conn = sqlite3.connect(db_path)
    assert "created_at" not in _columns(conn, "link")
    assert "aliases" not in _columns(conn, "entry")
    conn.close()
    # And a second refused open changes nothing more.
    before = _schema_snapshot(db_path)
    with pytest.raises(StorageError, match="link.created_at"):
        PyriteDB(db_path)
    assert _schema_snapshot(db_path) == before


@pytest.mark.control(
    reason="SQLite already accepted the default on an empty table; guards against over-refusal"
)
def test_a_non_constant_default_is_added_to_an_empty_table(tmp_path):
    """The same column on an empty table is what SQLite accepts in place."""
    db_path = tmp_path / "index.db"
    _load_shape("v0.26.0", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM link")
    conn.execute("ALTER TABLE link DROP COLUMN created_at")
    conn.commit()
    conn.close()
    PyriteDB(db_path).close()
    conn = sqlite3.connect(db_path)
    assert "created_at" in _columns(conn, "link")
    link_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'link'"
    ).fetchone()[0]
    assert "CURRENT_TIMESTAMP" in link_sql.upper(), link_sql
    conn.close()


@pytest.mark.control(
    reason="a constant default always added in place; guards the classifier against over-refusal"
)
def test_a_constant_default_still_adds_to_a_table_with_rows(tmp_path):
    """The refusal is only for the defaults SQLite cannot evaluate per row."""
    db_path = tmp_path / "index.db"
    _load_shape("v0.26.0", db_path)
    _seed_rows(db_path, tmp_path / "kb")
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE kb DROP COLUMN source")
    conn.commit()
    conn.close()
    PyriteDB(db_path).close()
    conn = sqlite3.connect(db_path)
    assert "source" in _columns(conn, "kb")
    assert {row[0] for row in conn.execute("SELECT source FROM kb")} == {"user"}
    conn.close()


@pytest.mark.parametrize(
    "default",
    ["CURRENT_TIMESTAMP", "CURRENT_DATE", "CURRENT_TIME", "(datetime('now'))"],
    ids=["timestamp", "date", "time", "parenthesised"],
)
@pytest.mark.parametrize(
    "rows",
    [
        pytest.param(
            0,
            id="empty",
            marks=pytest.mark.control(
                reason="SQLite already accepted the default on an empty table; guards against over-refusal"
            ),
        ),
        pytest.param(1, id="with-rows"),
    ],
)
def test_a_non_constant_sql_default_is_refused_only_on_rows(default, rows, tmp_path):
    """Every default SQLite calls non-constant: accepted empty, refused with rows."""
    from sqlalchemy import Column, Integer, MetaData, Table, Text, text

    from pyrite.storage.schema_repair import repair_schema

    conn = sqlite3.connect(tmp_path / "bare.db")
    conn.execute("CREATE TABLE t (a INTEGER)")
    if rows:
        conn.execute("INSERT INTO t (a) VALUES (1)")
    conn.commit()
    metadata = MetaData()
    Table("t", metadata, Column("a", Integer), Column("x", Text, server_default=text(default)))
    if rows:
        with pytest.raises(StorageError) as info:
            repair_schema(conn, metadata)
        assert type(info.value).__name__ == REFUSAL
        assert "t.x" in str(info.value)
        assert "non-constant default" in str(info.value)
        assert "x" not in _columns(conn, "t")
    else:
        assert repair_schema(conn, metadata) == ["t.x"]
        assert "x" in _columns(conn, "t")
    conn.close()


@pytest.mark.control(
    reason="literal SQL defaults always added in place; guards the classifier against over-refusal"
)
@pytest.mark.parametrize("default", ["0", "'active'", "((1))", "NULL", "TRUE"])
def test_a_constant_sql_default_is_added_to_a_table_with_rows(default, tmp_path):
    """A literal default, even parenthesised, is what SQLite accepts in place."""
    from sqlalchemy import Column, Integer, MetaData, Table, text

    from pyrite.storage.schema_repair import repair_schema

    conn = sqlite3.connect(tmp_path / "bare.db")
    conn.execute("CREATE TABLE t (a INTEGER)")
    conn.execute("INSERT INTO t (a) VALUES (1)")
    conn.commit()
    metadata = MetaData()
    Table("t", metadata, Column("a", Integer), Column("x", Integer, server_default=text(default)))
    assert repair_schema(conn, metadata) == ["t.x"]
    assert "x" in _columns(conn, "t")
    conn.close()


@pytest.mark.parametrize(
    "persisted",
    [
        pytest.param(True, id="stored"),
        pytest.param(
            False,
            id="virtual",
            marks=pytest.mark.control(
                reason="SQLite adds a VIRTUAL generated column even with rows; guards against over-refusal"
            ),
        ),
    ],
)
@pytest.mark.parametrize(
    "rows",
    [
        pytest.param(
            0,
            id="empty",
            marks=pytest.mark.control(
                reason="SQLite already accepted a STORED generated column on an empty table"
            ),
        ),
        pytest.param(1, id="with-rows"),
    ],
)
def test_a_generated_column_is_refused_only_when_stored_on_rows(persisted, rows, tmp_path):
    """SQLite refuses a STORED generated column on a table with rows, not a VIRTUAL one."""
    from sqlalchemy import Column, Computed, Integer, MetaData, Table

    from pyrite.storage.schema_repair import repair_schema

    conn = sqlite3.connect(tmp_path / "bare.db")
    conn.execute("CREATE TABLE t (a INTEGER)")
    if rows:
        conn.execute("INSERT INTO t (a) VALUES (1)")
    conn.commit()
    metadata = MetaData()
    Table(
        "t",
        metadata,
        Column("a", Integer),
        Column("g", Integer, Computed("a + 1", persisted=persisted)),
    )
    if persisted and rows:
        with pytest.raises(StorageError) as info:
            repair_schema(conn, metadata)
        assert type(info.value).__name__ == REFUSAL
        assert "t.g" in str(info.value)
        assert "stored generated column" in str(info.value)
        assert "g" not in _all_columns(conn, "t")
    else:
        assert repair_schema(conn, metadata) == ["t.g"]
        assert "g" in _all_columns(conn, "t")
    conn.close()


@pytest.mark.cli
@pytest.mark.parametrize(
    "argv",
    [
        ("get", "new-one", "-k", "demo"),
        ("backlinks", "new-one", "-k", "demo"),
        ("index", "health"),
        ("index", "build", "--no-embed"),
    ],
    ids=["get", "backlinks", "index-health", "index-build"],
)
def test_refusal_reaches_the_user_structured(argv, make_home):
    home = make_home("v0.26.0")
    _drop_kb_path(home / "index.db")
    proc = _pyrite(home, *argv, "--format", "json") if argv[0] != "index" else _pyrite(home, *argv)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "Traceback" not in out, out
    if argv[0] != "index":
        err = _error_json(proc)
        assert err["error_code"] == "STORAGE_ERROR"
        assert "kb.path" in err["error"], err
        assert "pyrite index build" in err["suggestion"]


@pytest.mark.cli
def test_the_refusal_suggestion_works(make_home):
    """Do what the suggestion says, in its order, and the commands work again."""
    home = make_home("v0.26.0")
    _drop_kb_path(home / "index.db")
    refused = _error_json(_pyrite(home, "get", "new-one", "-k", "demo", "--format", "json"))
    suggestion = refused["suggestion"]
    assert suggestion.index("pyrite db backup") < suggestion.index("pyrite index build")
    backup = _pyrite(home, "db", "backup", "--output", str(home / "kept.db"))
    assert backup.returncode == 0, backup.stdout + backup.stderr
    assert (home / "kept.db").stat().st_size > 0
    for suffix in ("", "-wal", "-shm"):
        p = home / f"index.db{suffix}"
        if p.exists():
            p.rename(home / f"index.db{suffix}.aside")
    build = _pyrite(home, "index", "build", "--no-embed")
    assert build.returncode == 0, build.stdout + build.stderr
    got = _pyrite(home, "get", "new-one", "-k", "demo")
    assert got.returncode == 0, got.stdout + got.stderr


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores modes")
@pytest.mark.parametrize("shape", ["v0.25.8", "arch-df6372f2"])
def test_a_read_only_index_is_told_about_the_disk_not_to_rebuild(shape, tmp_path):
    """Refused at connect (journal_mode=WAL writes), before the repair. A
    rebuild fixes nothing here and loses what only the index holds."""
    db_path = tmp_path / "index.db"
    _load_shape(shape, db_path)
    db_path.chmod(0o444)
    try:
        with pytest.raises(StorageError) as info:
            PyriteDB(db_path)
    finally:
        db_path.chmod(0o644)
    suggestion = info.value.suggestion or ""
    assert "writable" in suggestion, suggestion
    assert "index build" not in suggestion, suggestion


def _shape_needing_a_failing_v24(db_path: Path) -> None:
    """An index recorded at 23 whose ``starred_entry`` predates v24 and lacks
    ``sort_order``, so v24's copy of the stars fails: a migration that cannot
    finish, as a full disk or a hand-edited file would make it."""
    _load_shape("v0.25.8", db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        DROP TABLE starred_entry;
        CREATE TABLE starred_entry (
            id INTEGER PRIMARY KEY, entry_id VARCHAR NOT NULL,
            kb_name VARCHAR NOT NULL, created_at VARCHAR NOT NULL);
        DELETE FROM schema_version WHERE version >= 24;
        """
    )
    conn.commit()
    conn.close()


def test_a_migration_that_fails_is_a_refusal_not_a_raw_migration_error(tmp_path):
    db_path = tmp_path / "index.db"
    _shape_needing_a_failing_v24(db_path)
    with pytest.raises(StorageError) as info:
        PyriteDB(db_path)
    assert type(info.value).__name__ == REFUSAL
    message = str(info.value)
    assert "23" in message and str(CURRENT_VERSION) in message, message
    assert str(tmp_path) not in message
    assert "sort_order" not in message, "the driver's text is logged, not shown"
    assert "stopped at migration v24" in message, message
    # The file is intact and behind: no move-aside-and-rebuild advice.
    suggestion = info.value.suggestion or ""
    assert "do not rebuild" in suggestion, suggestion
    assert "move the index file aside" not in suggestion.lower(), suggestion


@pytest.mark.cli
def test_a_failing_migration_reaches_the_cli_structured(make_home):
    home = make_home(None)
    _shape_needing_a_failing_v24(home / "index.db")
    proc = _pyrite(home, "get", "new-one", "-k", "demo", "--format", "json")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "Traceback" not in out, out
    err = _error_json(proc)
    assert err["error_code"] == "STORAGE_ERROR"
    assert "schema version 23" in err["error"], err


# ---------------------------------------------------------------------------
# (6) index build: a database error is a storage error, not a bad file
# ---------------------------------------------------------------------------


def test_reconcile_raises_storage_error_on_a_database_fault_and_keeps_rows(tmp_path):
    """A column lost after the open (another process, a bad copy) fails the
    build with STORAGE_ERROR; it is not filed under "could not be parsed",
    and the rows already there are kept."""
    from pyrite.config import KBConfig, PyriteConfig, Settings
    from pyrite.storage.index import IndexManager

    kb_path = tmp_path / "kb"
    _write_kb(kb_path)
    kb = KBConfig(name="demo", path=kb_path, kb_type="generic")
    db_path = tmp_path / "index.db"
    db = PyriteDB(db_path)
    try:
        config = PyriteConfig(knowledge_bases=[kb], settings=Settings(index_path=db_path))
        mgr = IndexManager(db, config)
        mgr.reconcile_kb(kb)
        rows_before = db.execute_sql("SELECT id FROM entry ORDER BY id")
        assert [r["id"] for r in rows_before] == ["new-one", "old-one"]
        other = sqlite3.connect(db_path)
        other.execute("ALTER TABLE link DROP COLUMN target_prefix")
        other.commit()
        other.close()
        with pytest.raises(StorageError) as info:
            mgr.reconcile_kb(kb, force=True)
        # The file is intact; the next open repairs the column. No rebuild.
        assert "run the command again" in (info.value.suggestion or "").lower()
        assert "index build" not in (info.value.suggestion or "")
        rows_after = db.execute_sql("SELECT id FROM entry ORDER BY id")
        assert [r["id"] for r in rows_after] == ["new-one", "old-one"]
    finally:
        db.close()


@pytest.mark.control(reason="a file's own fault stays a per-file problem; passes before the fix")
def test_a_files_own_fault_stays_could_not_be_parsed(tmp_path):
    from pyrite.config import KBConfig, PyriteConfig, Settings
    from pyrite.storage.index import IndexManager

    kb_path = tmp_path / "kb"
    _write_kb(kb_path)
    (kb_path / "bad.md").write_text("---\nid: bad\ntitle: [a, b]\ntype: note\n---\nx\n")
    kb = KBConfig(name="demo", path=kb_path, kb_type="generic")
    db_path = tmp_path / "index.db"
    db = PyriteDB(db_path)
    try:
        config = PyriteConfig(knowledge_bases=[kb], settings=Settings(index_path=db_path))
        results = IndexManager(db, config).reconcile_kb(kb)
        assert [Path(m["path"]).name for m in results["malformed"]] == ["bad.md"]
        assert results["added"] == 2
    finally:
        db.close()


# ---------------------------------------------------------------------------
# (4) Concurrent first opens (#1302)
# ---------------------------------------------------------------------------

_OPENER = r"""
import sys, time
from pathlib import Path
from pyrite.storage.database import PyriteDB

db_path, ready, go = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
ready.touch()
deadline = time.monotonic() + 60
while not go.exists():
    if time.monotonic() > deadline:
        sys.exit(3)
    time.sleep(0.001)
db = PyriteDB(db_path)
db.get_backlinks("new-one", "demo", readable_kbs=None)
db.close()
"""


def _race(db_path: Path, n: int, work: Path) -> list[tuple[int, str]]:
    """Start ``n`` processes, release them together, return the failures."""
    go = work / "go"
    procs, readies = [], []
    for i in range(n):
        ready = work / f"ready-{i}"
        readies.append(ready)
        procs.append(
            subprocess.Popen(
                [sys.executable, "-c", _OPENER, str(db_path), str(ready), str(go)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        )
    deadline = time.monotonic() + 120
    while not all(r.exists() for r in readies):
        assert time.monotonic() < deadline, "openers never became ready"
        time.sleep(0.01)
    go.touch()
    failures = []
    for p in procs:
        out, _ = p.communicate(timeout=120)
        if p.returncode != 0:
            last = out.strip().splitlines()[-1:] or [""]
            failures.append((p.returncode, last[0]))
    return failures


@needs_flock
@pytest.mark.parametrize(
    "shape",
    [
        pytest.param(
            "v0.25.8",
            marks=pytest.mark.control(
                reason="timing-dependent: passes on dev when the openers do not overlap; "
                "test_openers_that_all_read_the_old_version_never_both_migrate is its red twin"
            ),
        ),
        "arch-df6372f2",
        "v0.25.8-not-wal",
        "fresh",
    ],
)
@pytest.mark.parametrize("n", [2, 4])
def test_concurrent_first_opens_all_succeed(shape, n, tmp_path):
    """``fresh``: no file yet, so every opener creates it. ``-not-wal``: a
    restored backup in rollback-journal mode, so every opener's first
    connection switches it to WAL, which SQLite refuses to a second process at
    once with "database is locked" whatever the busy timeout: the reason the
    lock is taken before the first connection."""
    failures = []
    for round_ in range(3):
        work = tmp_path / f"round-{round_}"
        work.mkdir()
        db_path = work / "index.db"
        if shape != "fresh":
            _load_shape(shape.removesuffix("-not-wal"), db_path, wal=not shape.endswith("-not-wal"))
            _seed_rows(db_path, tmp_path / "kb")
        failures += _race(db_path, n, work)
        assert _missing_after_open(db_path) == []
    assert failures == []


# ---------------------------------------------------------------------------
# The repair step on a bare connection (the #820 v30 lesson)
# ---------------------------------------------------------------------------


def test_repair_on_a_connection_with_no_tables_does_nothing():
    """``tests/test_migrations.py`` drives storage code on bare connections; a
    step that assumes the model's tables exist broke 11 of them in the spike."""
    from pyrite.storage.schema_repair import repair_schema

    conn = sqlite3.connect(":memory:")
    assert repair_schema(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0


@needs_flock
def test_an_open_waits_for_another_process_holding_the_schema_lock(tmp_path):
    """Deterministic half of #1302: while one process holds the schema step,
    another's open waits and migrates nothing; once released it finds the
    index current. (The race above depends on timing; this does not.)"""
    work = tmp_path / "work"
    work.mkdir()
    db_path = work / "index.db"
    _load_shape("v0.25.8", db_path)
    _seed_rows(db_path, tmp_path / "kb")
    (work / "go").touch()
    with _hold_schema_lock(db_path):
        opener = subprocess.Popen(
            [sys.executable, "-c", _OPENER, str(db_path), str(work / "ready"), str(work / "go")],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + 60
        while not (work / "ready").exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        time.sleep(1.0)
        assert opener.poll() is None, "the open did not wait for the lock"
        conn = sqlite3.connect(db_path)
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 27
        conn.close()
    out, _ = opener.communicate(timeout=120)
    assert opener.returncode == 0, out
    assert _missing_after_open(db_path) == []


@pytest.mark.control(
    reason="passes before the fix, which has no repair to misread table_info; guards the repair"
)
def test_a_generated_column_counts_as_present(tmp_path):
    """``PRAGMA table_info`` hides generated columns; the repair must see them
    (``table_xinfo``), or it adds the column again and refuses the open."""
    db_path = tmp_path / "index.db"
    _load_shape("v0.26.0", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE entry DROP COLUMN aliases")
    conn.execute("ALTER TABLE entry ADD COLUMN aliases TEXT GENERATED ALWAYS AS ('[]') VIRTUAL")
    conn.commit()
    conn.close()
    PyriteDB(db_path).close()


@needs_flock
def test_an_open_that_cannot_get_the_schema_lock_is_busy_not_hung(tmp_path, monkeypatch):
    """A holder that never lets go (a stopped process) fails the open with the
    retryable StorageBusyError after the bound, not a hang or a traceback."""
    from pyrite.exceptions import StorageBusyError

    try:
        from pyrite.storage import schema_repair
    except ImportError:  # the code before the fix: no lock, so no bound to shorten
        pass
    else:
        monkeypatch.setattr(schema_repair, "SCHEMA_LOCK_TIMEOUT", 0.2)
    db_path = tmp_path / "index.db"
    _load_shape("v0.25.8", db_path)
    with _hold_schema_lock(db_path):
        with pytest.raises(StorageBusyError) as info:
            PyriteDB(db_path)
    assert info.value.retryable is True
    assert info.value.suggestion
    # Released: the next open proceeds and repairs.
    assert _missing_after_open(db_path) == []


# ---------------------------------------------------------------------------
# Round 1, property 1: every SQLite fault met while opening, migrating,
# repairing or writing the index goes through the one classifier
# (`storage_error_from`), so a lock is retryable and never told to rebuild.
# ---------------------------------------------------------------------------


def _with_vec_table(db_path: Path) -> None:
    """Create ``vec_entry`` as the open would, so the open's first write is
    the step under test and not the vector table."""
    try:
        import sqlite_vec
    except ImportError:  # then the open has no vector table to create either
        return
    from pyrite.storage.virtual_tables import create_vec_table

    conn = sqlite3.connect(db_path)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        create_vec_table(conn)
    finally:
        conn.close()


def _open_with_a_writer_holding(db_path: Path, begin: str) -> StorageError:
    holder = sqlite3.connect(db_path, isolation_level=None)
    holder.execute(begin)
    try:
        with pytest.raises(StorageError) as info:
            PyriteDB(db_path)
        return info.value
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def _lock_at_connect(tmp_path: Path) -> StorageError:
    """`ConnectionMixin._init_connection`: the first connection's switch to WAL
    meets another connection's exclusive lock on a rollback-journal file."""
    db_path = tmp_path / "index.db"
    _load_shape("v0.26.0", db_path, wal=False)
    return _open_with_a_writer_holding(db_path, "BEGIN EXCLUSIVE")


def _lock_in_a_migration(tmp_path: Path) -> StorageError:
    """`ConnectionMixin._run_migrations`: v28's ALTER meets a writer's lock."""
    db_path = tmp_path / "index.db"
    _load_shape("v0.25.8", db_path)
    _with_vec_table(db_path)
    return _open_with_a_writer_holding(db_path, "BEGIN IMMEDIATE")


def _lock_in_the_repair(tmp_path: Path) -> StorageError:
    """`schema_repair.repair_schema`: its BEGIN IMMEDIATE meets a writer's lock."""
    db_path = tmp_path / "index.db"
    _load_shape("v0.26.0", db_path)
    _with_vec_table(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE link DROP COLUMN target_prefix")
    conn.commit()
    conn.close()
    return _open_with_a_writer_holding(db_path, "BEGIN IMMEDIATE")


def _lock_writing_a_row(tmp_path: Path) -> StorageError:
    """`index._storage_error_for_write` (from `reconcile_kb`): another
    connection takes the write lock while the build is writing rows."""
    from pyrite.config import KBConfig, PyriteConfig, Settings
    from pyrite.storage.index import IndexManager

    kb_path = tmp_path / "kb"
    _write_kb(kb_path)
    kb = KBConfig(name="demo", path=kb_path, kb_type="generic")
    db_path = tmp_path / "index.db"
    db = PyriteDB(db_path)
    holder = sqlite3.connect(db_path, isolation_level=None)
    try:
        mgr = IndexManager(
            db, PyriteConfig(knowledge_bases=[kb], settings=Settings(index_path=db_path))
        )
        mgr.reconcile_kb(kb)

        def take_the_lock(entry, path, data):
            if not holder.in_transaction:
                holder.execute("BEGIN IMMEDIATE")
            return None

        with pytest.raises(StorageError) as info:
            mgr.reconcile_kb(kb, force=True, enrich=take_the_lock)
        return info.value
    finally:
        if holder.in_transaction:
            holder.execute("ROLLBACK")
        holder.close()
        db.close()


def _lock_backing_up(tmp_path: Path) -> StorageError:
    """`index_backup.backup_index` (`pyrite db backup`): the source index is a
    rollback-journal file another connection holds under BEGIN EXCLUSIVE."""
    from pyrite.services.index_backup import backup_index

    db_path = tmp_path / "index.db"
    _load_shape("v0.26.0", db_path, wal=False)
    holder = sqlite3.connect(db_path, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(StorageError) as info:
            backup_index(db_path, tmp_path / "backup.db")
        return info.value
    finally:
        holder.execute("ROLLBACK")
        holder.close()


#: The sites that turn a SQLite error met while opening, migrating, repairing,
#: writing or backing up the index into a StorageError, each with a real lock
#: that reaches it. `test_every_conversion_site_raises_the_classifier_result`
#: keeps every other site under pyrite/ on the classifier.
LOCK_SITES = {
    "connection._init_connection": _lock_at_connect,
    "connection._run_migrations": _lock_in_a_migration,
    "schema_repair.repair_schema": _lock_in_the_repair,
    "index._storage_error_for_write": _lock_writing_a_row,
    "index_backup.backup_index": _lock_backing_up,
}


@pytest.mark.parametrize("site", sorted(LOCK_SITES))
def test_a_lock_is_retryable_and_never_told_to_rebuild(site, tmp_path, monkeypatch):
    """docs/json-contracts.md: `retryable` is true for a locked database, so
    wait and repeat. A rebuild would lose what only the index holds."""
    from pyrite.storage import connection

    monkeypatch.setattr(connection, "SQLITE_BUSY_TIMEOUT_MS", 50)
    err = LOCK_SITES[site](tmp_path)
    assert type(err).__name__ == "StorageBusyError", (type(err).__name__, str(err))
    assert err.retryable is True
    suggestion = err.suggestion or ""
    assert "wait" in suggestion.lower(), suggestion
    assert "index build" not in suggestion, suggestion


# ---------------------------------------------------------------------------
# Round 2: the classifier guard reads every module under pyrite/ (#1260).
#
# Round 1's guard scanned four modules, keyed sites by function (so a second,
# unclassified handler in a classified function hid behind the first) and
# matched exception names as substrings (so `except DatabaseError` was not a
# site). This one resolves each handler's caught names through the module's
# imports and constants, judges each handler on its own, and requires every
# error a database handler raises to be the classifier's result.
# ---------------------------------------------------------------------------

_PYRITE_ROOT = Path(__file__).resolve().parents[1] / "pyrite"

#: Functions whose result is the classifier's: `storage_error_from` itself and
#: wrappers that return what it returns (checked by source below).
_CLASSIFIERS = {
    "pyrite.storage.errors.storage_error_from",
    "pyrite.storage.index._storage_error_for_write",
}

#: Calls that turn the fault into the user's error without raising one.
_ERROR_EMITTERS = {"pyrite.utils.errors.cli_error"}


class _Module:
    """One module's AST, its imported names (alias -> qualified name) and its
    module-level assignments, for resolving what an ``except`` catches."""

    def __init__(self, name: str, path: Path):
        import ast

        self.name = name
        self.path = path
        self.tree = ast.parse(path.read_text(encoding="utf-8"))
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        self.imports: dict[str, str] = {}
        # Function-local imports count too: Pyrite imports lazily.
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self.imports[alias.asname] = alias.name
                    else:
                        top = alias.name.split(".")[0]
                        self.imports[top] = top
            elif isinstance(node, ast.ImportFrom):
                source = node.module or ""
                if node.level:
                    base = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                    source = ".".join(base + ([source] if source else []))
                for alias in node.names:
                    self.imports[alias.asname or alias.name] = f"{source}.{alias.name}"
        self.constants = {
            node.targets[0].id: node.value
            for node in self.tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        }


_MODULES: dict[str, _Module | None] = {}


def _module(name: str) -> _Module | None:
    if name not in _MODULES:
        base = _PYRITE_ROOT.parent.joinpath(*name.split("."))
        found = [p for p in (base.with_suffix(".py"), base / "__init__.py") if p.is_file()]
        _MODULES[name] = _Module(name, found[0]) if found else None
    return _MODULES[name]


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(_PYRITE_ROOT.parent).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _resolve(module: _Module, expr, depth: int = 0) -> set[str]:
    """The qualified names ``expr`` may stand for: through ``import ... as``,
    ``from ... import X as Y``, tuples, and module constants (here or in the
    pyrite module a name was imported from)."""
    import ast

    if depth > 8:
        return {ast.unparse(expr)}
    if isinstance(expr, ast.Tuple):
        return set().union(*(_resolve(module, e, depth + 1) for e in expr.elts))
    if isinstance(expr, ast.Name):
        constant = module.constants.get(expr.id)
        if isinstance(constant, ast.Tuple | ast.Name | ast.Attribute):
            return _resolve(module, constant, depth + 1)
        if expr.id in module.imports:
            qualified = module.imports[expr.id]
            owner, _, attr = qualified.rpartition(".")
            source = _module(owner) if owner.startswith("pyrite") else None
            if source is not None and source is not module:
                return _resolve(source, ast.Name(id=attr), depth + 1)
            return {qualified}
        return {f"{module.name}.{expr.id}"}
    if isinstance(expr, ast.Attribute):
        return {f"{q}.{expr.attr}" for q in _resolve(module, expr.value, depth + 1)}
    return {ast.unparse(expr)}


def _is_database_fault(qualified: str) -> bool:
    return (
        qualified.startswith(("sqlite3.", "sqlalchemy."))
        or qualified == "pyrite.storage.migrations.MigrationError"
    )


def _is_storage_error_class(qualified: str) -> bool:
    """Does ``qualified`` name StorageError, a subclass, or MigrationError?"""
    import importlib

    from pyrite.storage.migrations import MigrationError

    owner, _, attr = qualified.rpartition(".")
    if not owner.startswith("pyrite"):
        return False
    try:
        obj = getattr(importlib.import_module(owner), attr)
    except (ImportError, AttributeError):
        return False
    return isinstance(obj, type) and issubclass(obj, StorageError | MigrationError)


def _returns_storage_error(qualified: str) -> bool:
    """Is ``qualified`` a pyrite function annotated to return a StorageError
    (a factory such as ``kb_service._storage_error``)? A handler that raises
    its result builds a storage error as surely as one that calls the class."""
    import ast

    owner, _, attr = qualified.rpartition(".")
    module = _module(owner) if owner.startswith("pyrite") else None
    if module is None:
        return False
    for node in module.tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == attr and node.returns is not None:
            return any(_is_storage_error_class(q) for q in _resolve(module, node.returns))
    return False


def _handler_nodes(handler):
    """The handler's own nodes, not those of a function defined inside it."""
    import ast

    stack = list(handler.body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _callee(module: _Module, call) -> set[str]:
    return _resolve(module, call.func)


def _handler_findings(module: _Module, handler) -> tuple[bool, list[str]]:
    """Is this handler a conversion site, and what in it bypasses the classifier?

    A site is a handler that catches a database fault (resolved names), or one
    of any type that raises a StorageError or MigrationError it built. In a
    site, every ``raise X`` must re-raise the caught error or raise the
    classifier's result (a call, or a name bound to one in the handler); no
    ``cli_error`` may report the fault; and nothing may set ``.suggestion``,
    which would put the site's advice over a lock's or a disk's.
    """
    import ast

    caught = _resolve(module, handler.type) if handler.type is not None else {"BaseException"}
    nodes = list(_handler_nodes(handler))
    classified: set[str] = set()
    for node in nodes:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and _callee(module, node.value) & _CLASSIFIERS
        ):
            classified |= {t.id for t in node.targets if isinstance(t, ast.Name)}

    def builds_storage_error(exc) -> bool:
        call = exc
        if isinstance(exc, ast.Name):
            bound = [
                n.value
                for n in nodes
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == exc.id for t in n.targets)
            ]
            call = bound[0] if bound else None
        return isinstance(call, ast.Call) and any(
            _is_storage_error_class(q) or _returns_storage_error(q) for q in _callee(module, call)
        )

    findings: list[str] = []
    raises = [n for n in nodes if isinstance(n, ast.Raise) and n.exc is not None]
    is_site = any(_is_database_fault(q) for q in caught) or any(
        builds_storage_error(r.exc) for r in raises
    )
    if not is_site:
        return False, []
    for node in raises:
        exc = node.exc
        if isinstance(exc, ast.Name) and (exc.id == handler.name or exc.id in classified):
            continue
        if isinstance(exc, ast.Call) and _callee(module, exc) & _CLASSIFIERS:
            continue
        findings.append(f"raises {ast.unparse(exc)[:60]}")
    for node in nodes:
        if isinstance(node, ast.Call) and _callee(module, node) & _ERROR_EMITTERS:
            findings.append(f"reports with {ast.unparse(node.func)}")
        if isinstance(node, ast.Assign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr == "suggestion":
                    findings.append(f"sets {ast.unparse(target)} after the classifier")
    return True, findings


def _conversion_sites(root: Path = _PYRITE_ROOT) -> dict[str, list[str]]:
    """Every conversion site under ``root``, keyed one handler at a time as
    ``module:qualname:caught`` (with ``#n`` for a repeat in one function),
    mapped to what in it bypasses the classifier."""
    import ast

    sites: dict[str, list[str]] = {}

    def visit(module: _Module, node, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                visit(module, child, [*scope, child.name])
                continue
            if isinstance(child, ast.ExceptHandler):
                is_site, findings = _handler_findings(module, child)
                if is_site:
                    caught = ast.unparse(child.type) if child.type is not None else "<bare>"
                    key = f"{module.name}:{'.'.join(scope) or '<module>'}:{caught}"
                    n = 1
                    while (unique := key if n == 1 else f"{key}#{n}") in sites:
                        n += 1
                    sites[unique] = findings
            visit(module, child, scope)

    for path in sorted(root.rglob("*.py")):
        name = _module_name(path)
        module = _module(name)
        assert module is not None, path
        visit(module, module.tree, [])
    return sites


#: Conversion sites that report a database fault without the classifier, and
#: why that is right. Keyed as `_conversion_sites` keys them; an entry that no
#: longer matches a site with findings fails the test, so the list cannot rot.
NOT_THE_CLASSIFIERS_JOB = {
    "pyrite.storage.migrations:MigrationManager._apply_migration:Exception": (
        "wraps the fault as MigrationError with it as __cause__; "
        "connection._run_migrations, the one caller on the open path, classifies that"
    ),
    "pyrite.storage.migrations:MigrationManager._rollback_migration:Exception": (
        "MigrationError for MigrationManager.rollback, which no open path and no command calls"
    ),
    "pyrite.services.search_service:SearchService._db_search:sqlite3.DatabaseError": (
        "QuerySyntaxError only where _looks_like_query_syntax_error matches the "
        "caller's query; every other fault raises storage_error_from"
    ),
    "pyrite.services.auth_service:AuthService._insert_local_user:IntegrityError": (
        "ValueError for the UNIQUE username constraint: IntegrityError is SQLite's "
        "constraint class, never a lock, a disk fault or corruption"
    ),
    "pyrite.cli.db_commands:db_restore:sqlite3.DatabaseError": (
        "VALIDATION_FAILED for the backup file the user names, which is checked "
        "before the index is touched; the index is not the file at fault"
    ),
    "pyrite.storage.backends.postgres_backend:PostgresBackend._exec:Exception": (
        "the Postgres backend's row materialisation; storage_error_from classifies "
        "SQLite's result codes only"
    ),
    # Faults that are not the database's: each builds its StorageError from a
    # file lock or the filesystem, which the SQLite classifier cannot judge.
    "pyrite.storage.schema_repair:schema_lock:BlockingIOError": (
        "the schema lock file (fcntl) held past its timeout: StorageBusyError, "
        "retryable, with its own wait advice"
    ),
    "pyrite.services.kb_service:KBService._update:LockTimeout": (
        "the entry file's write lock (utils.file_lock): StorageBusyError, retryable"
    ),
    "pyrite.services.kb_service:KBService._update:OSError": (
        "the markdown file's write (a hard link, the lock directory, the disk)"
    ),
    "pyrite.services.kb_service:KBService.rename_entry:LockTimeout": (
        "the entry file's rename lock (utils.file_lock): StorageBusyError, retryable"
    ),
    "pyrite.services.kb_service:KBService.rename_entry:OSError": (
        "the markdown file's rename (a hard link, the lock directory, the disk)"
    ),
    "pyrite.services.kb_service:KBService.rename_entry:Exception": (
        "the rename is already on disk, so retrying it is wrong whatever the "
        "sync's fault, a lock included: IndexSyncRecoveryHintError says to run "
        "`pyrite index sync`, which recovers from every cause and moves nothing aside"
    ),
}


def test_every_conversion_site_raises_the_classifier_result():
    sites = _conversion_sites()
    bypassing = {key: findings for key, findings in sites.items() if findings}
    unexplained = {k: v for k, v in bypassing.items() if k not in NOT_THE_CLASSIFIERS_JOB}
    assert not unexplained, (
        "these handlers turn a database fault into a Pyrite error without "
        "storage_error_from: raise its result (pass the advice as suggestion=), "
        f"or explain them in NOT_THE_CLASSIFIERS_JOB: {unexplained}"
    )
    stale = set(NOT_THE_CLASSIFIERS_JOB) - set(bypassing)
    assert not stale, f"NOT_THE_CLASSIFIERS_JOB names handlers that are gone or fixed: {stale}"


def test_the_classifier_wrappers_return_its_result():
    import inspect

    from pyrite.storage import index

    assert "storage_error_from(" in inspect.getsource(index._storage_error_for_write)


#: Sites found by reading the code (#1260 round 2), each routed through the
#: classifier. The scan must see them: a resolver that lost an alias or a
#: tuple would find no site and pass the test above for the wrong reason.
_KNOWN_CLASSIFIED_SITES = {
    "pyrite.storage.connection:ConnectionMixin._init_connection:"
    "(OSError, sqlite3.Error, SQLAlchemyError, MigrationError)",
    "pyrite.storage.connection:ConnectionMixin._raw_cursor:sqlite3.DatabaseError",
    "pyrite.storage.connection:ConnectionMixin._run_migrations:MigrationError",
    "pyrite.storage.schema_repair:repair_schema:sqlite3.Error",
    "pyrite.storage.index:IndexManager.reconcile_kb:_DATABASE_FAULTS",
    "pyrite.services.index_backup:backup_index:sqlite3.Error",
    "pyrite.services.index_backup:backup_index:sqlite3.Error#2",
    "pyrite.services.url_checker:URLChecker.collect_urls:"
    "(SQLAlchemyError, sqlite3.Error, StorageError)",
}


def test_the_scan_sees_every_known_site():
    sites = _conversion_sites()
    missing = _KNOWN_CLASSIFIED_SITES - set(sites)
    assert not missing, (missing, sorted(sites))
    assert all(not sites[key] for key in _KNOWN_CLASSIFIED_SITES), {
        k: sites[k] for k in _KNOWN_CLASSIFIED_SITES if sites[k]
    }


_SCRATCH_TRICKS = {
    # Round 1 review (a): a second handler in a classified function.
    "a second handler in a classified function": """
import sqlite3
from pyrite.exceptions import StorageError
from pyrite.storage.errors import storage_error_from

def _init_connection(conn):
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError as exc:
        raise StorageError(f"Cannot open: {exc}") from exc
    except sqlite3.Error as exc:
        raise storage_error_from(exc, "Cannot open") from exc
""",
    # Round 1 review (b): a name the substring match did not know.
    "DatabaseError imported by name": """
from sqlite3 import DatabaseError
from pyrite.exceptions import StorageError

class Thing:
    def read(self, conn):
        try:
            conn.execute("SELECT 1")
        except DatabaseError as exc:
            raise StorageError(f"read failed: {exc}") from exc
""",
    "an aliased SQLAlchemy class": """
from sqlalchemy.exc import DBAPIError as DriverFault
from pyrite.exceptions import StorageError

def read(session):
    try:
        session.execute("SELECT 1")
    except DriverFault as exc:
        raise StorageError("read failed") from exc
""",
    "an IntegrityError in a tuple through a module alias": """
import sqlite3 as db
from pyrite.exceptions import ValidationError

def write(conn):
    try:
        conn.execute("INSERT INTO t VALUES (1)")
    except (KeyError, db.IntegrityError) as exc:
        raise ValidationError("bad row") from exc
""",
    "a classified error whose advice is replaced": """
import sqlite3
from pyrite.storage.errors import storage_error_from

def write(conn):
    try:
        conn.execute("INSERT INTO t VALUES (1)")
    except sqlite3.Error as exc:
        err = storage_error_from(exc, "write failed")
        err.suggestion = "Run `pyrite index build`."
        raise err from exc
""",
    "a broad handler that builds a StorageError": """
from pyrite.exceptions import StorageError

def write(conn):
    try:
        conn.execute("INSERT INTO t VALUES (1)")
    except Exception as exc:
        raise StorageError("write failed") from exc
""",
}


@pytest.mark.control(
    reason="tests the guard in this file against scratch modules, not pyrite/; the "
    "guard's red against the real tree is test_every_conversion_site_raises_the_"
    "classifier_result"
)
@pytest.mark.parametrize("trick", sorted(_SCRATCH_TRICKS))
def test_the_guard_catches(trick, tmp_path, monkeypatch):
    """Each way round 1's guard was fooled, written into a scratch module
    under a scratch ``pyrite`` package and scanned as the real tree is."""
    root = tmp_path / "pyrite"
    root.mkdir()
    (root / "scratch.py").write_text(_SCRATCH_TRICKS[trick], encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "_PYRITE_ROOT", root)
    monkeypatch.setattr(sys.modules[__name__], "_MODULES", {})
    sites = _conversion_sites(root)
    assert any(sites.values()), sites


# ---------------------------------------------------------------------------
# Round 1, property 2: no suggestion advises a destructive recovery without
# naming what it loses.
# ---------------------------------------------------------------------------


#: Each suggestion and the module that defines it.
_SUGGESTION_HOMES = {
    "BACKUP_DESTINATION_SUGGESTION": "pyrite.services.index_backup",
    "BACKUP_RETRY_SUGGESTION": "pyrite.services.index_backup",
    "BUSY_SUGGESTION": "pyrite.storage.errors",
    "CORRUPT_INDEX_SUGGESTION": "pyrite.storage.connection",
    "DISK_SUGGESTION": "pyrite.storage.errors",
    "MIGRATION_FAILED_SUGGESTION": "pyrite.storage.schema_repair",
    "REBUILD_SUGGESTION": "pyrite.storage.schema_repair",
    "REPAIR_FAILED_SUGGESTION": "pyrite.storage.schema_repair",
    "WRITE_RETRY_SUGGESTION": "pyrite.storage.index",
}


def _suggestion(name: str) -> str:
    """Resolved one at a time, so a suggestion that exists before the fix
    (CORRUPT_INDEX_SUGGESTION) is judged there on its text."""
    import importlib

    return getattr(importlib.import_module(_SUGGESTION_HOMES[name]), name)


def _suggestions() -> dict[str, str]:
    return {name: _suggestion(name) for name in _SUGGESTION_HOMES}


#: What only the index holds, written out here rather than read from
#: `errors.INDEX_ONLY_STATE`, so the test does not restate the code.
LOST_ON_REBUILD = ("pyrite kb add", "users", "sessions", "API keys", "stars", "reviews", "history")


SUGGESTION_NAMES = [
    "BACKUP_DESTINATION_SUGGESTION",
    "BACKUP_RETRY_SUGGESTION",
    "BUSY_SUGGESTION",
    "CORRUPT_INDEX_SUGGESTION",
    "DISK_SUGGESTION",
    "MIGRATION_FAILED_SUGGESTION",
    "REBUILD_SUGGESTION",
    "REPAIR_FAILED_SUGGESTION",
    "WRITE_RETRY_SUGGESTION",
]


def test_every_suggestion_is_checked():
    assert sorted(_suggestions()) == SUGGESTION_NAMES


@pytest.mark.parametrize("name", SUGGESTION_NAMES)
def test_a_suggestion_that_moves_the_index_aside_names_what_is_lost(name):
    text = _suggestion(name)
    if "pyrite index build" not in text:
        return  # no rebuild advised
    for thing in LOST_ON_REBUILD:
        assert thing in text, f"{name} moves the index aside without naming {thing!r}"
    # And says how to keep it: a backup, or the moved file itself.
    assert "pyrite db backup" in text or "keep it" in text, name


@pytest.mark.cli
def test_the_rebuild_suggestion_is_honest_for_a_kb_added_with_kb_add(tmp_path):
    """A KB registered only with `pyrite kb add` lives in the index's `kb` table.
    The refusal must say a rebuild loses it, and following the advice keeps it
    in the backup."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("knowledge_bases: []\n")
    kb_path = tmp_path / "added-kb"
    _write_kb(kb_path)
    added = _pyrite(home, "kb", "add", str(kb_path), "--name", "added")
    assert added.returncode == 0, added.stdout + added.stderr
    built = _pyrite(home, "index", "build", "--no-embed")
    assert built.returncode == 0, built.stdout + built.stderr
    # A column this build cannot add in place (NOT NULL, no default).
    conn = sqlite3.connect(home / "index.db")
    conn.execute("ALTER TABLE review DROP COLUMN result")
    conn.commit()
    conn.close()

    refused = _pyrite(home, "kb", "list", "--format", "json")
    err = _error_json(refused)
    assert "review.result" in err["error"]
    assert "pyrite kb add" in err["suggestion"], err["suggestion"]
    assert "pyrite db backup" in err["suggestion"], err["suggestion"]

    assert _pyrite(home, "db", "backup", "--output", str(home / "kept.db")).returncode == 0
    kept = sqlite3.connect(home / "kept.db")
    assert kept.execute("SELECT name FROM kb WHERE name = 'added'").fetchone() == ("added",)
    kept.close()
    # What the suggestion warns of: after the rebuild the added KB is gone.
    (home / "index.db").rename(home / "index.db.aside")
    for suffix in ("-wal", "-shm"):
        (home / f"index.db{suffix}").unlink(missing_ok=True)
    assert _pyrite(home, "index", "build", "--no-embed").returncode == 0
    listed = _pyrite(home, "kb", "list", "--format", "json")
    assert '"added"' not in listed.stdout, listed.stdout


def test_a_repair_that_fails_on_the_data_is_not_called_a_disk_fault(tmp_path):
    """A declared UNIQUE index that duplicate rows block rolls the repair back;
    the suggestion is not about the disk and not a rebuild."""
    db_path = tmp_path / "index.db"
    _load_shape("v0.26.0", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("DROP INDEX ix_tag_name")
    conn.execute("INSERT INTO tag (name) VALUES ('twice'), ('twice')")
    conn.execute("ALTER TABLE entry DROP COLUMN aliases")
    conn.commit()
    conn.close()
    with pytest.raises(StorageError) as info:
        PyriteDB(db_path)
    assert type(info.value).__name__ == REFUSAL
    assert "nothing was changed" in str(info.value)
    suggestion = info.value.suggestion or ""
    assert "do not rebuild" in suggestion and "writable" in suggestion, suggestion
    conn = sqlite3.connect(db_path)
    assert "aliases" not in _columns(conn, "entry"), "the repair is one transaction"
    conn.close()


def test_a_refusal_after_this_opens_migrations_says_what_was_applied(tmp_path):
    """v0.25.8 (27) with kb.path gone: this open migrates to the current
    version, then refuses. The message says both, not "built by another
    Pyrite version"."""
    db_path = tmp_path / "index.db"
    _load_shape("v0.25.8", db_path)
    _drop_kb_path(db_path)
    with pytest.raises(StorageError) as info:
        PyriteDB(db_path)
    message = str(info.value)
    assert type(info.value).__name__ == REFUSAL
    assert "schema version 27" in message, message
    assert f"applied the migrations to {CURRENT_VERSION}" in message, message
    assert "another Pyrite version" not in message


# ---------------------------------------------------------------------------
# #1302 made deterministic: openers that all read the old version before any
# migrates. A writer's lock holds every opener at v28's ALTER (on the code
# before the fix, after each has read version 27); released together, all but
# one then add a column that is already there.
# ---------------------------------------------------------------------------

_GATED_OPENER = r"""
import sys, time
from pathlib import Path
from pyrite.storage.database import PyriteDB

db_path, ready, go = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
ready.touch()
deadline = time.monotonic() + 60
while not go.exists():
    if time.monotonic() > deadline:
        sys.exit(3)
    time.sleep(0.001)
PyriteDB(db_path).close()
"""


@needs_flock
@pytest.mark.parametrize("shape", ["v0.25.8", "arch-df6372f2"])
@pytest.mark.parametrize("n", [2, 4])
def test_openers_that_all_read_the_old_version_never_both_migrate(shape, n, tmp_path):
    db_path = tmp_path / "index.db"
    _load_shape(shape, db_path)
    _with_vec_table(db_path)
    go = tmp_path / "go"
    readies = [tmp_path / f"ready-{i}" for i in range(n)]
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _GATED_OPENER, str(db_path), str(r), str(go)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for r in readies
    ]
    deadline = time.monotonic() + 120
    while not all(r.exists() for r in readies):
        assert time.monotonic() < deadline, "openers never became ready"
        time.sleep(0.01)
    holder = sqlite3.connect(db_path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    go.touch()
    # Well inside the 5 s busy timeout, long enough for every opener to reach
    # the write that waits on this lock.
    time.sleep(1.5)
    holder.execute("ROLLBACK")
    holder.close()
    failures = []
    for p in procs:
        out, _ = p.communicate(timeout=120)
        if p.returncode != 0:
            failures.append((p.returncode, (out.strip().splitlines() or [""])[-1]))
    assert failures == []
    assert _missing_after_open(db_path) == []
