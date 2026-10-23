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

fcntl = pytest.importorskip("fcntl")

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
    """Do what the suggestion says, and the commands work again."""
    home = make_home("v0.26.0")
    _drop_kb_path(home / "index.db")
    refused = _error_json(_pyrite(home, "get", "new-one", "-k", "demo", "--format", "json"))
    assert "move" in refused["suggestion"].lower()
    for suffix in ("", "-wal", "-shm"):
        p = home / f"index.db{suffix}"
        if p.exists():
            p.rename(home / f"index.db{suffix}.aside")
    build = _pyrite(home, "index", "build", "--no-embed")
    assert build.returncode == 0, build.stdout + build.stderr
    got = _pyrite(home, "get", "new-one", "-k", "demo")
    assert got.returncode == 0, got.stdout + got.stderr


@pytest.mark.control(
    reason="a read-only file is refused at connect (journal_mode=WAL writes), before the "
    "repair; this pins that it stays one structured StorageError with a suggestion"
)
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores modes")
@pytest.mark.parametrize("shape", ["v0.25.8", "arch-df6372f2"])
def test_a_read_only_index_is_one_storage_error_with_a_suggestion(shape, tmp_path):
    db_path = tmp_path / "index.db"
    _load_shape(shape, db_path)
    db_path.chmod(0o444)
    try:
        with pytest.raises(StorageError) as info:
            PyriteDB(db_path)
    finally:
        db_path.chmod(0o644)
    assert info.value.suggestion


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
    assert info.value.suggestion


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
        assert info.value.suggestion
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


@pytest.mark.parametrize("shape", ["v0.25.8", "arch-df6372f2", "v0.25.8-not-wal", "fresh"])
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
